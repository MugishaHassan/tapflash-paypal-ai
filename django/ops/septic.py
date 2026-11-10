"""Septic tanks: any level sensor (or the sensor sandbox) sends readings; at 80% the operator gets an alert
in their chat with a link to order emptying, pays with PayPal, and TapFlash pays the emptying company with
PayPal Payouts. The limits are in code: an order only above the alert level, one open order per tank, and a
price cap."""
import logging
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from bot import outbox
from bot.replies import Text
from payments.paypal import PayPalError

from .models import EmptyingOrder, OperatorChat, SensorReading

logger = logging.getLogger(__name__)
ALERT_AT = 80.0
FULL_AT = 95.0
OPEN = (EmptyingOrder.Status.ALERTED, EmptyingOrder.Status.PAYING)


def days_left(restroom, level, now):
    """Days until the tank is full, from how fast it filled over the last two weeks (None if not rising)."""
    rows = list(SensorReading.objects.filter(restroom=restroom, kind=SensorReading.Kind.SEPTIC,
                                             at__gte=now - timedelta(days=14)).order_by('at').values_list('at', 'value'))
    if len(rows) < 2:
        return None
    (t0, v0), (t1, v1) = rows[0], rows[-1]
    span = (t1 - t0).total_seconds() / 86400
    if span <= 0 or v1 <= v0:
        return None
    return max(0.0, round((FULL_AT - level) / ((v1 - v0) / span), 1))


def record(restroom, value, *, sandbox, now=None, base_url=''):
    """Save a reading; at ALERT_AT or more, open an emptying order and alert the operator once."""
    now = now or timezone.now()
    value = max(0.0, min(100.0, float(value)))
    SensorReading.objects.create(restroom=restroom, kind=SensorReading.Kind.SEPTIC, value=value, sandbox=sandbox, at=now)
    if value < ALERT_AT or EmptyingOrder.objects.filter(restroom=restroom, status__in=OPEN).exists():
        return None
    order = EmptyingOrder.objects.create(
        restroom=restroom, level=value, days_left=days_left(restroom, value, now),
        provider=settings.EMPTYING_PROVIDER, provider_email=settings.TAPFLASH.demo_payout_email or restroom.payout_email,
        price=Decimal(settings.EMPTYING_PRICE_USD), token=secrets.token_urlsafe(16), created_at=now)
    alert(order, base_url)
    return order


def _tell(order, text):
    chat = OperatorChat.objects.filter(restroom=order.restroom).first()
    if chat is None:
        return False
    try:
        outbox.notify(chat.user_id, [Text(text)])
        return True
    except Exception:
        logger.exception('Could not alert the operator of %s', order.restroom)
        return False


def alert(order, base_url=''):
    left = f', about {order.days_left:g} days left' if order.days_left is not None else ''
    link = f'{(settings.TAPFLASH.public_url or base_url).rstrip("/")}/empty/{order.token}'
    return _tell(order, f'*Septic tank alert, {order.restroom.name}*\nThe tank is {order.level:.0f}% full{left}.\n\n'
                        f'Order emptying from {order.provider} for USD {order.price:.2f}, paid with PayPal:\n{link}')


def start_payment(order, checkout, base_url):
    """A PayPal order for the emptying; returns the URL where the operator approves it."""
    if order.status not in OPEN:
        raise ValueError('This order is closed.')
    if order.price > Decimal(settings.EMPTYING_MAX_USD):
        raise ValueError(f'The price is above the limit of USD {settings.EMPTYING_MAX_USD}.')
    base = (settings.TAPFLASH.public_url or base_url).rstrip('/')
    if checkout is None:
        return f'{base}/empty/{order.token}/paid?simulated=1'
    created = checkout.client.create_order(
        amount=order.price, currency=order.currency, description=f'Septic tank emptying, {order.restroom.name}'[:127],
        reference=f'EMPTY-{order.pk}', custom_id=f'empty:{order.pk}', return_url=f'{base}/empty/{order.token}/paid',
        cancel_url=f'{base}/empty/{order.token}', brand_name=settings.TAPFLASH.bot_name, request_id=f'tf-empty-{order.token}')
    order.paypal_order_id, order.status = created['id'], EmptyingOrder.Status.PAYING
    order.save(update_fields=['paypal_order_id', 'status'])
    return created['approve_url']


def finish_payment(order, checkout, now=None):
    """Capture the operator's payment, then pay the emptying company with PayPal Payouts. Safe to repeat."""
    now = now or timezone.now()
    if order.status == EmptyingOrder.Status.PAID:
        return order
    if checkout is None:
        order.simulated = True
    else:
        captured = checkout.client.capture_order(order.paypal_order_id)
        capture = next((c for unit in captured.get('purchase_units', []) for c in unit.get('payments', {}).get('captures', [])), {})
        if capture.get('status') != 'COMPLETED':
            raise PayPalError(f'PayPal did not complete the payment ({capture.get("status", "no capture")}).')
        order.capture_id = capture.get('id', '')[:40]
        if order.provider_email:
            try:
                batch = checkout.client.create_payout(
                    sender_batch_id=f'tf-empty-{order.pk}', subject=f'{settings.TAPFLASH.bot_name}: septic tank emptying',
                    message=f'Please empty the septic tank at {order.restroom.name}.',
                    items=[{'receiver': order.provider_email, 'amount': order.price, 'currency': order.currency,
                            'note': f'Emptying, {order.restroom.name}', 'sender_item_id': f'tf-empty-{order.pk}'}])
                order.provider_batch_id = batch['batch_id'][:40]
            except PayPalError:
                logger.exception('Emptying %s paid, but paying the company failed', order.pk)
    order.status, order.paid_at = EmptyingOrder.Status.PAID, now
    order.save(update_fields=['status', 'paid_at', 'capture_id', 'provider_batch_id', 'simulated'])
    _tell(order, f'Emptying booked for {order.restroom.name}: {order.provider} is paid USD {order.price:.2f}'
                 + (f' (PayPal {order.capture_id}).' if order.capture_id else ' (simulated).'))
    return order
