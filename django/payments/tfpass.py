"""The prepaid TapFlash Pass: the visitor buys several visits with one PayPal payment (PayPal's fee is paid once,
not on every small visit); each visit then uses one, and a refunded visit is given back."""
import logging
import secrets
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import VisitPass

logger = logging.getLogger(__name__)
PREFIX = 'TFP'


def visits_left(user_id):
    return sum(VisitPass.objects.filter(user_id=user_id, status=VisitPass.Status.PAID).values_list('visits_left', flat=True))


def use_visit(user_id):
    """Take one visit from the oldest paid pass; returns the reference for the visit, or None."""
    with transaction.atomic():
        found = (VisitPass.objects.select_for_update().filter(user_id=user_id, status=VisitPass.Status.PAID, visits_left__gt=0)
                 .order_by('created_at').first())
        if found is None:
            return None
        VisitPass.objects.filter(pk=found.pk).update(visits_left=F('visits_left') - 1)
        used = found.visits_total - found.visits_left + 1
        return f'{PREFIX}{found.pk}-{used}-{secrets.token_hex(2).upper()}'


def for_reference(reference):
    """The pass a visit reference came from (TFP<pk>-<n>-<x>)."""
    if not (reference or '').startswith(PREFIX):
        return None
    try:
        return VisitPass.objects.filter(pk=int(reference[len(PREFIX):].split('-')[0])).first()
    except ValueError:
        return None


def visit_value(found):
    return (found.price / found.visits_total).quantize(Decimal('0.01'))


def give_back(reference):
    """A refunded pass visit returns to the pass."""
    found = for_reference(reference)
    if found:
        VisitPass.objects.filter(pk=found.pk, visits_left__lt=F('visits_total')).update(visits_left=F('visits_left') + 1)
    return found


def offer(user_id, channel, config):
    return VisitPass.objects.create(user_id=user_id, channel=channel, visits_total=config.tfpass_visits,
                                    price=Decimal(config.tfpass_price), currency=config.paypal_currency,
                                    token=secrets.token_urlsafe(16))


def start_payment(found, checkout, base_url):
    base = (settings.TAPFLASH.public_url or base_url).rstrip('/')
    if checkout is None:
        return f'{base}/tfpass/{found.token}/paid?simulated=1'
    created = checkout.client.create_order(
        amount=found.price, currency=found.currency, description=f'TapFlash Pass: {found.visits_total} visits',
        reference=f'TFPASS-{found.pk}', custom_id=f'tfpass:{found.pk}', return_url=f'{base}/tfpass/{found.token}/paid',
        cancel_url=f'{base}/tfpass/{found.token}', brand_name=settings.TAPFLASH.bot_name, request_id=f'tf-pass-{found.token}')
    found.paypal_order_id = created['id']
    found.save(update_fields=['paypal_order_id'])
    return created['approve_url']


def finish_payment(found, checkout, now=None):
    """Capture the purchase and credit the visits. Safe to repeat."""
    from bot import outbox
    from bot.replies import Text
    if found.status == VisitPass.Status.PAID:
        return found
    if checkout is not None:
        captured = checkout.client.capture_order(found.paypal_order_id)
        capture = next((c for unit in captured.get('purchase_units', []) for c in unit.get('payments', {}).get('captures', [])), {})
        if capture.get('status') not in ('COMPLETED', 'PENDING'):
            from .paypal import PayPalError
            raise PayPalError(f'PayPal did not complete the payment ({capture.get("status", "no capture")}).')
        found.capture_id = capture.get('id', '')[:40]
        fee = capture.get('seller_receivable_breakdown', {}).get('paypal_fee', {}).get('value')
        found.paypal_fee = Decimal(fee) if fee else None
    found.status, found.visits_left, found.paid_at = VisitPass.Status.PAID, found.visits_total, now or timezone.now()
    found.save(update_fields=['status', 'visits_left', 'paid_at', 'capture_id', 'paypal_fee'])
    try:
        outbox.notify(found.user_id, [Text(f'Your *TapFlash Pass* is ready: {found.visits_total} visits, paid with PayPal. '
                                           'Choose *TapFlash Pass* when you pay for a visit.')])
    except Exception:
        logger.exception('Could not tell %s about the pass', found.user_id)
    return found
