"""The /link page: shows the WhatsApp login QR code so the bot's phone can be
linked from anywhere. Protected by LINK_PASSWORD; empty turns it off (404).

The WhatsApp connection (Node, a separate process) writes its state to
LINK_STATUS_FILE: {"state": "starting" | "qr" | "linking" | "ready", "qr": ...}.
"""
import base64
import hmac
import io
import json
import time

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.utils.html import escape
from django.views.decorators.http import require_http_methods

MAX_TRIES = 5
LOCK_SECONDS = 15 * 60
# A QR code older than this has been replaced by WhatsApp already.
QR_MAX_AGE_SECONDS = 90


def _enabled():
    if not settings.LINK_PASSWORD:
        raise Http404


def _client_ip(request):
    forwarded = request.headers.get('X-Real-IP') if getattr(settings, 'SECURE_PROXY_SSL_HEADER', None) else None
    return forwarded or request.META.get('REMOTE_ADDR', '')


def _no_store(response):
    response['Cache-Control'] = 'no-store'
    response['X-Frame-Options'] = 'DENY'
    # no-referrer would make the browser send "Origin: null" and fail Django's CSRF check.
    response['Referrer-Policy'] = 'same-origin'
    return response


@require_http_methods(['GET', 'POST'])
def page(request):
    _enabled()
    error = ''
    if request.method == 'POST' and not request.session.get('link_ok'):
        key = f'link-tries:{_client_ip(request)}'
        tries = cache.get(key, 0)
        if tries >= MAX_TRIES:
            error = 'Too many wrong passwords. Try again in 15 minutes.'
        elif hmac.compare_digest(request.POST.get('password', '').encode(), settings.LINK_PASSWORD.encode()):
            request.session.cycle_key()
            request.session['link_ok'] = True
            cache.delete_many([key, f'{key}:since'])
        else:
            # The 15 minutes count from the first wrong try, not the latest:
            # its time is kept once (add does not overwrite) and every try
            # ends together with it. add then incr also keeps two tries at
            # the same moment from counting once.
            since = cache.get_or_set(f'{key}:since', time.time(), LOCK_SECONDS)
            left = max(1, int(since + LOCK_SECONDS - time.time()))
            cache.add(key, 0, left)
            try:
                cache.incr(key)
            except ValueError:  # ran out between add and incr
                cache.add(key, 1, left)
            cache.touch(key, left)
            error = 'Wrong password.'
    body = QR_PAGE if request.session.get('link_ok') else LOGIN_PAGE.format(
        csrf=escape(get_token(request)), error=escape(error))
    return _no_store(HttpResponse(PAGE.format(name=escape(settings.TAPFLASH.bot_name), body=body)))


def status(request):
    _enabled()
    if not request.session.get('link_ok'):
        return _no_store(JsonResponse({'state': 'locked'}, status=403))
    try:
        data = json.loads(settings.LINK_STATUS_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {'state': 'starting'}
    state = data.get('state', 'starting')
    reply = {'state': state}
    if state == 'qr':
        if time.time() * 1000 - data.get('at', 0) > QR_MAX_AGE_SECONDS * 1000:
            reply['state'] = 'starting'
        else:
            reply['qr'] = _qr_png(data.get('qr', ''))
    return _no_store(JsonResponse(reply))


def _qr_png(text):
    import qrcode

    image = qrcode.make(text, box_size=8, border=3)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Link {name} WhatsApp</title>
<style>
body{{margin:0;font-family:system-ui,sans-serif;background:#f2f4f3;color:#1c2b26;
display:flex;min-height:100vh;align-items:center;justify-content:center}}
main{{background:#fff;padding:28px 24px;border-radius:14px;max-width:380px;width:calc(100% - 32px);
box-shadow:0 2px 16px rgba(0,0,0,.08);text-align:center}}
h1{{font-size:20px;margin:0 0 8px}} p{{line-height:1.45;margin:8px 0}}
input,button{{font:inherit;padding:12px;border-radius:8px;width:100%;box-sizing:border-box}}
input{{border:1px solid #c5ccc9;margin:12px 0}} button{{border:0;background:#128c5e;color:#fff;font-weight:600}}
.err{{color:#b3261e}} img{{width:100%;max-width:300px;image-rendering:pixelated}}
.ok{{color:#128c5e;font-size:18px;font-weight:600}} ol{{text-align:left;padding-left:20px}}
</style></head><body><main><h1>Link {name} WhatsApp</h1>{body}</main></body></html>"""

LOGIN_PAGE = """<form method="post"><p>Enter the password you were given.</p>
<input type="hidden" name="csrfmiddlewaretoken" value="{csrf}">
<input type="password" name="password" autocomplete="current-password" required autofocus>
<button>Show the QR code</button><p class="err">{error}</p></form>"""

QR_PAGE = """<ol><li>Open WhatsApp on the bot's phone.</li>
<li>Tap <b>&#8942;</b> or Settings, then <b>Linked devices</b>, then <b>Link a device</b>.</li>
<li>Point the phone at this code.</li></ol>
<div id="box"><p>Loading...</p></div>
<script>
const box = document.getElementById('box');
let last = '';
async function check() {
  try {
    const res = await fetch('link/status', {cache: 'no-store'});
    const data = await res.json();
    if (data.state === 'locked') { location.reload(); return; }
    let html;
    if (data.state === 'qr') html = '<img alt="WhatsApp login QR code" src="' + data.qr + '"><p>The code changes by itself every few seconds.</p>';
    else if (data.state === 'linking') html = '<p>Scanned. Finishing the link...</p>';
    else if (data.state === 'ready') html = '<p class="ok">Linked. The bot is live.</p><p>You can close this page.</p>';
    else html = '<p>Getting a new code, please wait...</p>';
    if (html !== last) { box.innerHTML = html; last = html; }
  } catch (e) {}
  setTimeout(check, 3000);
}
check();
</script>"""
