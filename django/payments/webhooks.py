"""What PayPal tells us after the fact, through verified webhooks.

- PAYMENT.CAPTURE.DENIED / REVERSED: money we counted as paid never came, or
  was taken back (a chargeback). The visit is marked refunded by PayPal, its
  access code stops working, and the money comes off the operator's next
  payout the same way a refund does.
- PAYMENT.CAPTURE.REFUNDED: a refund made outside the bot (in PayPal's own
  dashboard) is recorded, so it too comes off the operator's next payout.
- PAYMENT.PAYOUTSBATCH.* : how a payout to an operator ended.

Each event can arrive more than once; handling it twice changes nothing.
"""
import logging
from decimal import Decimal, InvalidOperation

from .models import AccessPass, Payment, PayPalOrder, Payout, Refund

logger = logging.getLogger(__name__)

LOST = {'PAYMENT.CAPTURE.DENIED': 'PayPal denied the payment', 'PAYMENT.CAPTURE.REVERSED': 'PayPal reversed the payment'}


def handle_capture_lost(kind, resource, now):
    """Returns the PayPalOrder it changed, or None."""
    capture_id = str(resource.get('id', ''))[:40]
    order = PayPalOrder.objects.select_related('payment').filter(capture_id=capture_id).first() if capture_id else None
    payment = order.payment if order else None
    if payment is None or payment.status == Payment.Status.FAILED:
        return None
    already = sum((refund.amount for refund in payment.refunds.exclude(status=Refund.Status.FAILED)), 0)
    if already < order.amount:
        Refund.objects.create(
            payment=payment, amount=order.amount - already, currency=order.currency,
            status=Refund.Status.COMPLETED, provider_refund_id=capture_id, decided_by=Refund.DecidedBy.PAYPAL,
            category='reversal', reason=LOST[kind], notes=str(resource.get('status_details', ''))[:500],
            created_at=now,
        )
    payment.status = Payment.Status.REFUNDED
    payment.failure_reason = LOST[kind]
    payment.save(update_fields=['status', 'failure_reason'])
    AccessPass.objects.filter(payment=payment, expires_at__gt=now).update(expires_at=now)
    logger.warning('%s for %s (capture %s)', LOST[kind], payment.reference, capture_id)
    return order


def handle_refunded(resource, now):
    """A refund PayPal reports. Ours are already recorded (by their PayPal refund
    id); one made in PayPal's dashboard is added. Returns the new Refund or None."""
    refund_id = str(resource.get('id', ''))[:40]
    if not refund_id or Refund.objects.filter(provider_refund_id=refund_id).exists():
        return None
    up = next((item.get('href', '') for item in resource.get('links') or [] if item.get('rel') == 'up'), '')
    capture_id = up.rstrip('/').rsplit('/', 1)[-1][:40] if '/captures/' in up else ''
    order = PayPalOrder.objects.select_related('payment').filter(capture_id=capture_id).first() if capture_id else None
    if order is None or order.payment is None:
        return None
    try:
        amount = Decimal(str((resource.get('amount') or {}).get('value', '0')))
    except InvalidOperation:
        return None
    payment = order.payment
    refund = Refund.objects.create(
        payment=payment, amount=amount, currency=order.currency,
        status=Refund.Status.COMPLETED if resource.get('status') == 'COMPLETED' else Refund.Status.PENDING,
        provider_refund_id=refund_id, decided_by=Refund.DecidedBy.STAFF, category='paypal_dashboard',
        reason='Refunded in PayPal', notes='Made in the PayPal dashboard, not in TapFlash.', created_at=now,
    )
    if amount >= order.amount and payment.status == Payment.Status.SUCCEEDED:
        payment.status = Payment.Status.REFUNDED
        payment.save(update_fields=['status'])
        AccessPass.objects.filter(payment=payment, expires_at__gt=now).update(expires_at=now)
    return refund


def handle_payout_item(resource):
    """PAYMENT.PAYOUTS-ITEM.*: whether the one receiver of a payout got the money."""
    batch_id = str(resource.get('payout_batch_id', ''))[:40]
    status = str(resource.get('transaction_status', ''))[:20]
    if not (batch_id and status):
        return 0
    changed = Payout.objects.filter(batch_id=batch_id).exclude(status=status).update(status=status, error='')
    from ops.payouts import SKIPPED_STATUSES, release
    if status in SKIPPED_STATUSES:
        for payout in Payout.objects.filter(batch_id=batch_id):
            release(payout)
    return changed


def handle_payout_batch(resource):
    header = resource.get('batch_header') or {}
    batch_id, status = str(header.get('payout_batch_id', ''))[:40], str(header.get('batch_status', ''))[:20]
    if not (batch_id and status):
        return 0
    changed = Payout.objects.filter(batch_id=batch_id).exclude(status=status).update(status=status, error='')
    if status in ('FAILED', 'DENIED', 'CANCELED'):
        # Nothing was paid: the visits, refunds and cleaning bonuses are owed again.
        from ops.payouts import release
        for payout in Payout.objects.filter(batch_id=batch_id):
            release(payout)
    return changed
