"""/gate/: the attendant's phone at the restroom door. Scan the visitor's
access QR code with the camera, or type the 6-digit code; the server says
whether to let them in (see ops.gate)."""
import json
import logging
from functools import wraps

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.http import JsonResponse
from django.templatetags.static import static
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from bot import outbox
from tapflash import limits
from bot.handler import default_engine
from bot.replies import Text
from restrooms.models import Restroom

from .gate import check_in, visitor_text
from .models import Attendant

logger = logging.getLogger(__name__)

JSQR = 'ops/vendor/jsQR.js'  # served from this project's static files (jsQR 1.4.0)
CSP = '; '.join([
    "default-src 'none'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def _secure(response):
    response['Content-Security-Policy'] = CSP
    response['Referrer-Policy'] = 'same-origin'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'no-store'
    response['Permissions-Policy'] = 'camera=(self)'
    return response


def _restrooms_for(user):
    """The restrooms this login may check visitors in at."""
    if not user.is_authenticated:
        return Restroom.objects.none()
    if user.is_staff:
        return Restroom.objects.filter(is_active=True).order_by('name')
    attendant = Attendant.objects.select_related('restroom').filter(user=user).first()
    return Restroom.objects.filter(pk=attendant.restroom_id) if attendant else Restroom.objects.none()


def gate_login_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not _restrooms_for(request.user).exists():
            if request.method == 'POST':
                return JsonResponse({'error': 'Log in again.'}, status=403)
            return redirect(f"{settings.GATE_LOGIN_URL}?next={request.path}")
        return view(request, *args, **kwargs)
    return wrapped


MAX_WRONG_PASSWORDS = 8
LOCK_SECONDS = 15 * 60


class GateLogin(auth_views.LoginView):
    """Locked for 15 minutes after 8 wrong passwords from one address."""
    template_name = 'ops/gate_login.html'
    redirect_authenticated_user = False

    def _key(self):
        return f'gate-login:{limits.client_ip(self.request)}'

    def post(self, request, *args, **kwargs):
        if limits.count(self._key()) >= MAX_WRONG_PASSWORDS:
            form = self.get_form()
            return self.render_to_response(self.get_context_data(form=form, locked=True))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        limits.hit(self._key(), LOCK_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        limits.clear(self._key())
        return super().form_valid(form)

    def get_success_url(self):
        # Back to the page that asked for the login (the dashboard or the door check).
        target = self.get_redirect_url()
        return target if target in ('/ops/', '/gate/') or target.startswith(('/ops/', '/gate/')) else '/gate/'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bot_name'] = settings.TAPFLASH.bot_name
        return context

    def dispatch(self, request, *args, **kwargs):
        return _secure(super().dispatch(request, *args, **kwargs))


ANY = 'any'


def _current_restroom(request):
    """(restroom or None for "any restroom", the restrooms this login may choose from).
    Staff (and judges) start at "any restroom": the pass says where it is for."""
    allowed = _restrooms_for(request.user)
    code = request.GET.get('restroom') or request.session.get('gate_restroom') or (ANY if request.user.is_staff else '')
    if code == ANY and request.user.is_staff:
        request.session['gate_restroom'] = ANY
        return None, allowed
    restroom = allowed.filter(code=code).first() if code else None
    restroom = restroom or allowed.first()
    request.session['gate_restroom'] = restroom.code
    return restroom, allowed


@gate_login_required
@ensure_csrf_cookie
def gate(request):
    restroom, allowed = _current_restroom(request)
    return _secure(render(request, 'ops/gate.html', {
        'bot_name': settings.TAPFLASH.bot_name, 'restroom': restroom,
        'restrooms': allowed if request.user.is_staff else [], 'jsqr': static(JSQR),
        'who': request.user.get_full_name() or request.user.get_username(),
    }))


@gate_login_required
@require_POST
def gate_check(request):
    restroom, _ = _current_restroom(request)
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
        qr, code = str(body.get('qr') or '')[:300], str(body.get('code') or '')[:20]
    except (ValueError, AttributeError):
        return JsonResponse({'error': 'Send JSON like {"code": "123456"}.'}, status=400)
    if not (qr or code):
        return JsonResponse({'error': 'Scan a QR code or type the 6-digit code.'}, status=400)
    config = settings.TAPFLASH
    # Simulated payments open the door only while payments are simulated, or on a sandbox demo
    # that counts them (PAYOUTS_COUNT_SIMULATED).
    accept_simulated = not default_engine().checkout or config.payouts_count_simulated
    result = check_in(restroom, qr=qr, code=code, by=request.user.get_username(), accept_simulated=accept_simulated)
    if result.ok:
        try:
            name = restroom.name if restroom else result.access_pass.payment.facility_name
            outbox.notify(result.access_pass.user_id, [Text(visitor_text(result, name))])
        except Exception:
            logger.exception('Could not tell the visitor about their check-in')
    return JsonResponse({'ok': result.ok, 'title': result.title, 'detail': result.detail,
                         'restroom': restroom.name if restroom else 'Any restroom'})


def gate_logout(request):
    from django.contrib.auth import logout
    if request.method == 'POST':
        logout(request)
    return redirect(settings.GATE_LOGIN_URL)
