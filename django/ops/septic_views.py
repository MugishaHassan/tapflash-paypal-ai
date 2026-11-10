"""Sensor API (any sensor), sensor sandbox (test readings), operator alert link, and the emptying order page."""
import json

from django.conf import settings
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from bot.handler import default_engine
from payments.paypal import PayPalError
from restrooms.models import Restroom

from . import septic
from .models import EmptyingOrder, OperatorChat, SensorReading
from .views import _judge_limit, staff_member_required


def _body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return None


@csrf_exempt
@require_POST
def sensor_api(request):
    """POST {"restroom": "<code>", "kind": "septic_level", "value": 82.5} with the X-Sensor-Token header."""
    if not settings.SENSOR_TOKEN or request.headers.get('X-Sensor-Token') != settings.SENSOR_TOKEN:
        return JsonResponse({'error': 'Unknown sensor.'}, status=403)
    body = _body(request)
    if not body or body.get('kind') != SensorReading.Kind.SEPTIC:
        return JsonResponse({'error': 'Send JSON like {"restroom": "city-market", "kind": "septic_level", "value": 82}.'}, status=400)
    restroom = Restroom.objects.filter(code=str(body.get('restroom', ''))[:64]).first()
    try:
        value = float(body.get('value'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'value must be a number.'}, status=400)
    if restroom is None:
        return JsonResponse({'error': 'Unknown restroom.'}, status=404)
    order = septic.record(restroom, value, sandbox=False, base_url=request.build_absolute_uri('/'))
    return JsonResponse({'ok': True, 'alert': bool(order)})


@staff_member_required
@require_POST
def sensor_sandbox(request):
    """The dashboard's sensor sandbox: a test reading for a restroom's septic tank."""
    if (limited := _judge_limit(request)) is not None:
        return limited
    body = _body(request) or {}
    restroom = get_object_or_404(Restroom, code=str(body.get('restroom', ''))[:64])
    try:
        value = float(body.get('value', 85))
    except (TypeError, ValueError):
        value = 85.0
    order = septic.record(restroom, value, sandbox=True, base_url=request.build_absolute_uri('/'))
    told = order is not None and OperatorChat.objects.filter(restroom=restroom).exists()
    return JsonResponse({'message': f'Sandbox reading: {restroom.name} septic tank {value:.0f}% full. '
                         + ('Alert sent to the operator\'s chat.' if told else 'Alert opened.' if order else 'No alert needed.')})


@csrf_exempt
@require_POST
def operator_link(request):
    """Web demo chat only: this chat gets a restroom's operator alerts (on WhatsApp, an operator links the same way)."""
    if not settings.TAPFLASH.web_chat:
        raise Http404
    body = _body(request) or {}
    user_id = str(body.get('userId', ''))[:80]
    if not user_id:
        return JsonResponse({'error': 'userId is missing.'}, status=400)
    restroom = (Restroom.objects.filter(code=str(body.get('restroom', ''))[:64]).first()
                or Restroom.objects.filter(code='city-market').first() or Restroom.objects.order_by('pk').first())
    if restroom is None:
        return JsonResponse({'error': 'No restrooms yet.'}, status=404)
    OperatorChat.objects.update_or_create(restroom=restroom, defaults={'user_id': f'web:{user_id}'})
    return JsonResponse({'message': f'This chat now gets operator alerts for {restroom.name}.'})


def emptying(request, token):
    order = get_object_or_404(EmptyingOrder.objects.select_related('restroom'), token=token)
    return render(request, 'ops/emptying.html', {'order': order, 'bot_name': settings.TAPFLASH.bot_name,
                                                 'open': order.status in septic.OPEN, 'error': request.GET.get('error', '')})


@require_POST
def emptying_pay(request, token):
    order = get_object_or_404(EmptyingOrder, token=token)
    try:
        url = septic.start_payment(order, default_engine().checkout, request.build_absolute_uri('/'))
    except (ValueError, PayPalError) as error:
        return HttpResponseRedirect(f'/empty/{token}?error={str(error)[:120]}')
    return HttpResponseRedirect(url)


def emptying_paid(request, token):
    order = get_object_or_404(EmptyingOrder, token=token)
    checkout = default_engine().checkout
    if checkout is None and not request.GET.get('simulated'):
        raise Http404
    try:
        septic.finish_payment(order, checkout)
    except PayPalError as error:
        return HttpResponseRedirect(f'/empty/{token}?error={str(error)[:120]}')
    return HttpResponseRedirect(f'/empty/{token}')


@staff_member_required
def septic_data(request):
    rows = []
    for restroom in Restroom.objects.filter(is_active=True).order_by('name'):
        last = SensorReading.objects.filter(restroom=restroom, kind=SensorReading.Kind.SEPTIC).first()
        order = EmptyingOrder.objects.filter(restroom=restroom).first()
        rows.append({'code': restroom.code, 'restroom': restroom.name, 'level': round(last.value) if last else None,
                     'sandbox': bool(last and last.sandbox), 'linked': OperatorChat.objects.filter(restroom=restroom).exists(),
                     'order': order.get_status_display() if order else '', 'daysLeft': order.days_left if order else None,
                     'price': f'USD {order.price:.2f}' if order else '', 'ref': (order.capture_id or ('simulated' if order and order.simulated else '')) if order else '',
                     'provider': order.provider if order else ''})
    return JsonResponse({'tanks': rows})


def _report(request, code):
    from django.core.cache import cache

    from bot.ai.gemini import build_gemini

    from .reports import build
    restroom = get_object_or_404(Restroom, code=code)
    key = f'ops:report:{restroom.pk}'
    report = None if request.GET.get('fresh') else cache.get(key)
    if report is None:
        report = build(restroom, settings.TAPFLASH, checkout=default_engine().checkout, ai=build_gemini(settings.TAPFLASH))
        cache.set(key, report, 600)
    return restroom, report


@staff_member_required
def report_data(request):
    _, report = _report(request, str(request.GET.get('restroom', ''))[:64])
    return JsonResponse(report)


@staff_member_required
def statement(request, code):
    from django.utils import timezone
    restroom, report = _report(request, code)
    return render(request, 'ops/statement.html', {'r': report, 'restroom': restroom, 'bot_name': settings.TAPFLASH.bot_name,
                                                  'today': timezone.localdate()})
