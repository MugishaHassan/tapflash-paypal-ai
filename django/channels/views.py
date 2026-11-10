import hmac
import json
import logging
import threading
from pathlib import Path

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import connections
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from bot import outbox
from bot.handler import default_engine, handle_message

from . import inbox
from .cloud_api import GraphClient, messages_in, valid_signature
from tapflash import limits

from .web_chat import MAX_BODY_BYTES, MAX_PHOTO_BODY_BYTES, SECURITY_HEADERS, to_incoming, to_page_reply

logger = logging.getLogger(__name__)


PAGE = Path(__file__).resolve().parent / 'pages' / 'chat.html'
MAX_WEBHOOK_BYTES = 1024 * 1024


SIMULATED_LINE = b'Test chat. Payments are simulated.'
PAYPAL_LINE = b'Demo chat. PayPal sandbox, no real money.'


def home(request):
    """The test chat page while it is on, otherwise a sign of life."""
    config = settings.TAPFLASH
    if not config.web_chat:
        return HttpResponse(f'{config.bot_name} is running.', content_type='text/plain; charset=utf-8')
    page = PAGE.read_bytes()
    if default_engine().checkout:
        # Card and PayPal are real PayPal (sandbox) payments; only mobile money is simulated.
        page = page.replace(SIMULATED_LINE, PAYPAL_LINE)
    response = HttpResponse(page, content_type='text/html; charset=utf-8')
    response['Cache-Control'] = 'no-cache'
    return _secure(response)


def _secure(response):
    for name, value in SECURITY_HEADERS.items():
        response[name] = value
    return response


def _json(status, data):
    response = JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})
    response['Cache-Control'] = 'no-store'
    return _secure(response)


def _has_bridge_token(request):
    """Does the request carry "Authorization: Bearer <BRIDGE_TOKEN>"? The
    token is compared in constant time and never logged."""
    token = settings.BRIDGE_TOKEN
    if not token:
        return False
    given = request.headers.get('Authorization', '')
    scheme, _, value = given.partition(' ')
    return scheme.lower() == 'bearer' and hmac.compare_digest(value.strip().encode(), token.encode())


def _too_big(request, limit):
    try:
        if int(request.META.get('CONTENT_LENGTH') or 0) > limit:
            return True
        return len(request.body) > limit
    except (ValueError, RequestDataTooBig):
        return True


def api_outbox(request):
    """Replies the bot sent on its own (the receipt after paying on PayPal's
    page), each given out once. The web chat asks with ?userId=<its id>; the
    WhatsApp Web bridge, with its token, gets those of all its users."""
    if request.method != 'GET':
        return _json(405, {'error': 'Use GET'})
    if _has_bridge_token(request):
        waiting = outbox.collect(channel='bridge')
        return _json(200, {'messages': [
            {'userId': outbox.channel_user_id(user_id), 'replies': replies} for user_id, replies in waiting
        ]})
    if not settings.TAPFLASH.web_chat:
        return _json(403, {'error': 'Forbidden'})
    user = request.GET.get('userId', '').strip()[:64]
    if not user:
        return _json(400, {'error': 'userId is missing'})
    waiting = outbox.collect(channel='web', user_id=f'web:{user}')
    return _json(200, {'replies': [reply for _, replies in waiting for reply in replies]})


@csrf_exempt
def api_message(request):
    """One chat message as JSON in, its replies as JSON out."""
    bridge = _has_bridge_token(request)
    if not (settings.TAPFLASH.web_chat or bridge):
        return _json(403, {'error': 'Forbidden'})
    if request.method != 'POST':
        return _json(405, {'error': 'Use POST'})
    # One address can't flood the chat (and use up the free AI quota for everyone).
    if not bridge and limits.hit(f'chat-rate:{limits.client_ip(request)}', 60) > settings.CHAT_MESSAGES_PER_MINUTE:
        return _json(429, {'error': 'Too many messages. Wait a minute and try again.'})
    # Only JSON, so a web page on another site can't post here with a plain form.
    if request.content_type.lower() != 'application/json':
        return _json(415, {'error': 'Send JSON (Content-Type: application/json)'})
    if _too_big(request, MAX_PHOTO_BODY_BYTES):
        return _json(413, {'error': 'Message too large'})
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except ValueError:
        return _json(400, {'error': 'Invalid JSON'})
    # A message is an object: null, arrays, text and numbers are not.
    if not isinstance(body, dict):
        return _json(400, {'error': 'Send a JSON object'})
    # Only a photo may be big.
    if body.get('type') != 'image' and len(request.body) > MAX_BODY_BYTES:
        return _json(413, {'error': 'Message too large'})

    replies = []

    def collect(reply):
        page_reply = to_page_reply(reply)
        if page_reply:
            replies.append(page_reply)

    # The bridge runs next to the server, so only the web chat's own address
    # is one a buyer's browser can come back to (the bridge uses PUBLIC_URL).
    if bridge:
        incoming = to_incoming(body, channel='bridge')
    else:
        incoming = to_incoming(body, channel='web', base_url=request.build_absolute_uri('/').rstrip('/'))
    try:
        handle_message(incoming, collect)
    except Exception:
        logger.exception('Error while handling a web chat message')
        return _json(500, {'error': 'Something went wrong'})
    return _json(200, {'replies': replies})


@csrf_exempt
def webhook(request):
    config = settings.TAPFLASH
    if request.method == 'GET':
        # Meta checks the webhook once, when it is saved in the app dashboard.
        ok = (
            config.whatsapp_verify_token
            and request.GET.get('hub.mode') == 'subscribe'
            and hmac.compare_digest(request.GET.get('hub.verify_token', '').encode(), config.whatsapp_verify_token.encode())
        )
        if not ok:
            return HttpResponse('Forbidden', status=403, content_type='text/plain')
        return HttpResponse(request.GET.get('hub.challenge', ''), content_type='text/plain')
    if request.method != 'POST':
        return HttpResponse(status=405)

    if _too_big(request, MAX_WEBHOOK_BYTES):
        return HttpResponse(status=413)
    raw = request.body
    if not valid_signature(raw, request.headers.get('X-Hub-Signature-256'), config.whatsapp_app_secret):
        return HttpResponse(status=401)
    try:
        payload = json.loads(raw.decode('utf-8'))
    except ValueError:
        return HttpResponse(status=200)  # nothing to answer; Meta must not retry it

    users = inbox.claim(messages_in(payload))
    if users:
        client = GraphClient(config)
        if config.webhook_processing == 'inline':
            _answer(users, client)
        else:
            # Answer Meta at once; replying to the customer happens afterwards.
            threading.Thread(target=_answer_in_thread, args=(users, client), daemon=True).start()
    return HttpResponse(status=200)


def _answer(users, client):
    for user_id in users:
        inbox.answer_user(user_id, client)


def _answer_in_thread(users, client):
    try:
        _answer(users, client)
    except Exception:
        logger.exception('Could not answer WhatsApp messages')
    finally:
        connections.close_all()
