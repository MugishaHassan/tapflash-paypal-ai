"""What each restroom operator has earned since their last payout, and
sending it to their PayPal account with PayPal Payouts.

Every paid visit counts, minus what was refunded, times the operator's share
(80% by default). Each visit and refund is linked to the payout that covered
it, so nothing is counted twice or missed; a refund made after the visit was
already paid out is taken off the next payout. With PayPal set up, only money PayPal really captured
counts (simulated mobile money visits are left out); without PayPal, payouts
are simulated and every visit counts, so the dashboard still works in a demo.

Sending is safe to repeat: the Payout row is saved (and the restroom locked)
before PayPal is called, and its sender_batch_id is also PayPal's request id,
so a double click, a second admin or a retry after a timeout never pays twice.
"""
import secrets
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.db import transaction

from payments.models import Payment, PayPalOrder, Payout, Refund
from payments.paypal import PayPalError
from restrooms.models import Restroom

CENT = Decimal('0.01')
COUNTED = (Payment.Status.SUCCEEDED, Payment.Status.REFUNDED)
# A batch or payout item whose money never reached the receiver: what it covered is owed again.
SKIPPED_STATUSES = ('FAILED', 'DENIED', 'CANCELED', 'RETURNED', 'BLOCKED', 'REFUNDED')
UNFINISHED = ('PENDING', 'PROCESSING', 'UNCLAIMED', 'ONHOLD')
SIMULATED_PROVIDER = 'mock'


class PayoutError(Exception):
    """A payout that can't be sent, with the reason to show staff."""


@dataclass(frozen=True)
class Earnings:
    visits: int
    gross: Decimal  # in PAYPAL_CURRENCY
    refunded: Decimal  # includes refunds of visits paid out earlier
    owed: Decimal
    since: datetime | None


def in_paypal_currency(amount, currency, config):
    if currency == config.paypal_currency:
        return Decimal(amount)
    return (Decimal(amount) / config.paypal_rate).quantize(CENT, ROUND_HALF_UP)


def _refund_value(refunds, config):
    return sum((in_paypal_currency(refund.amount, refund.currency, config)
                for refund in refunds if refund.status != Refund.Status.FAILED), Decimal(0))


def payment_value(payment, config):
    """(taken, refunded) for one payment, in PAYPAL_CURRENCY."""
    if payment.provider == 'tfpass':
        from payments.tfpass import for_reference, visit_value
        found = for_reference(payment.reference)
        taken = visit_value(found) if found else Decimal(0)
        return taken, min(_refund_value(payment.refunds.all(), config), taken)
    try:
        order = payment.paypal_order
    except PayPalOrder.DoesNotExist:
        order = None
    taken = order.amount if order else in_paypal_currency(payment.total, payment.currency, config)
    return taken, min(_refund_value(payment.refunds.all(), config), taken)


def last_payout(restroom):
    return (restroom.payouts.filter(kind=Payout.Kind.EARNINGS).exclude(status__in=SKIPPED_STATUSES)
            .order_by('-created_at', '-pk').first())


def _unsettled(restroom, real_only):
    """Visits and refunds no payout has covered yet."""
    payments = (Payment.objects.filter(restroom=restroom, status__in=COUNTED, payout__isnull=True)
                .select_related('paypal_order').prefetch_related('refunds'))
    refunds = (Refund.objects.filter(payment__restroom=restroom, payout__isnull=True)
               .exclude(status=Refund.Status.FAILED).select_related('payment__paypal_order'))
    # Money PayPal only holds (the pass was not scanned yet) is not the operator's yet.
    payments = payments.exclude(paypal_order__authorization_id__gt='', paypal_order__capture_id='')
    if real_only:
        payments = payments.exclude(provider=SIMULATED_PROVIDER)
        refunds = refunds.exclude(payment__provider=SIMULATED_PROVIDER)
    return payments, refunds


def only_real_money(checkout, config):
    """With PayPal on, operators are paid only for money PayPal captured, unless a
    sandbox demo says to count simulated visits too (PAYOUTS_COUNT_SIMULATED)."""
    return bool(checkout) and not config.payouts_count_simulated


def earnings(restroom, config, *, real_only=False, rows=None):
    """What the operator is owed now. real_only: count only money PayPal captured.

    Every visit and refund is counted by exactly one payout: the one it is
    linked to. A refund of a visit paid out earlier comes off the next one."""
    last = last_payout(restroom)
    payments, refunds = rows or _unsettled(restroom, real_only)
    visits, gross = 0, Decimal(0)
    for payment in payments:
        visits += 1
        gross += payment_value(payment, config)[0]
    back = {}
    for refund in refunds:
        back.setdefault(refund.payment_id, [refund.payment, []])[1].append(refund)
    refunded = Decimal(0)
    for payment, given in back.values():
        refunded += min(_refund_value(given, config), payment_value(payment, config)[0])
    share = Decimal(restroom.operator_share_percent) / 100
    owed = ((gross - refunded) * share).quantize(CENT, ROUND_DOWN)
    return Earnings(visits, gross, refunded, max(owed, Decimal(0)), last.created_at if last else None)


def release(payout):
    """A payout that never went out: its visits and refunds are owed again, and
    an approved cleaning can be paid again."""
    Payment.objects.filter(payout=payout).update(payout=None)
    Refund.objects.filter(payout=payout).update(payout=None)
    from .models import CleaningCheck
    CleaningCheck.objects.filter(payout=payout).update(payout=None)


def _no_answer(error):
    # No answer at all, or PayPal's own error: the payout may still have gone.
    return error.status is None or error.status >= 500


def _send(checkout, payout, config):
    """Ask PayPal to send one saved payout. Safe to call again for the same row."""
    restroom_name = payout.restroom.name if payout.restroom else 'your restroom'
    bonus = payout.kind == Payout.Kind.CLEANING
    return checkout.client.create_payout(
        sender_batch_id=payout.sender_batch_id,
        subject=f'Your {config.bot_name} {"cleaning bonus" if bonus else "earnings"}',
        message=f'Thank you for keeping {restroom_name} clean.',
        items=[{'receiver': payout.receiver, 'amount': payout.amount, 'currency': payout.currency,
                'note': payout.note, 'sender_item_id': payout.sender_batch_id}],
    )


def pay_out(restroom, *, checkout, config, now):
    """Send the operator what they are owed. Raises PayoutError."""
    if not restroom.payout_email:
        raise PayoutError(f"Add the PayPal email of {restroom.name}'s operator first (in the admin).")
    with transaction.atomic():
        # Locks the restroom, so two clicks at once can't both see money owed.
        restroom = Restroom.objects.select_for_update().get(pk=restroom.pk)
        payments, refunds = _unsettled(restroom, only_real_money(checkout, config))
        payments, refunds = list(payments), list(refunds)
        due = earnings(restroom, config, rows=(payments, refunds))
        if due.owed < CENT:
            raise PayoutError(f'{restroom.name} has nothing to pay out since the last payout.')
        number = restroom.payouts.filter(kind=Payout.Kind.EARNINGS).count() + 1
        sender_batch_id = f'tf-{restroom.pk}-{number}-{secrets.token_hex(3)}'
        note = f'{config.bot_name}: your share of {due.visits} paid visit{"s" if due.visits != 1 else ""} at {restroom.name}'
        payout = Payout.objects.create(
            sender_batch_id=sender_batch_id, kind=Payout.Kind.EARNINGS, restroom=restroom,
            receiver=restroom.payout_email, amount=due.owed, currency=config.paypal_currency, visits=due.visits,
            note=note[:255], status='PENDING' if checkout else 'SIMULATED', created_at=now,
        )
        Payment.objects.filter(pk__in=[row.pk for row in payments]).update(payout=payout)
        Refund.objects.filter(pk__in=[row.pk for row in refunds]).update(payout=payout)
    return send(payout, checkout=checkout, config=config)


def send(payout, *, checkout, config):
    """Send a saved Payout row (simulated without PayPal). Raises PayoutError;
    a refused payout is marked FAILED and what it covered is owed again."""
    if not checkout:
        payout.batch_id = f'SIM-{secrets.token_hex(4).upper()}'
        payout.status = 'SIMULATED'
        payout.save(update_fields=['batch_id', 'status'])
        return payout
    try:
        result = _send(checkout, payout, config)
    except PayPalError as error:
        if _no_answer(error):
            # No answer: PayPal may have the batch. The row stays PENDING (so
            # nothing is owed twice) and refresh_pending sends it again with the
            # same request id, which PayPal answers without paying twice.
            payout.error = str(error)[:255]
            payout.save(update_fields=['error'])
            raise PayoutError('PayPal did not answer. The payout is saved as pending and will be '
                              'checked again; do not send it a second time.') from None
        payout.status, payout.error = 'FAILED', str(error)[:255]
        payout.save(update_fields=['status', 'error'])
        release(payout)
        raise PayoutError(f'PayPal refused the payout: {error}') from None
    payout.batch_id, payout.status = result['batch_id'], result['status'] or 'PENDING'
    payout.error = ''
    payout.save(update_fields=['batch_id', 'status', 'error'])
    return payout


def payout_status(batch):
    """The status that says whether the receiver got the money: the item's own
    (UNCLAIMED, RETURNED, ...) once the batch is done, else the batch's."""
    status = batch.get('batch_header', {}).get('batch_status', '')
    items = batch.get('items') or []
    item = items[0].get('transaction_status', '') if len(items) == 1 else ''
    return item if status == 'SUCCESS' and item else status


def refresh_pending(checkout, config, limit=10):
    """Ask PayPal how unfinished payouts are doing, and resend ones PayPal never answered."""
    if not checkout:
        return
    # Payouts still on their way first, then the oldest-checked unclaimed ones, so a few
    # unclaimed payouts never stop newer ones from being checked.
    moving = list(Payout.objects.filter(status__in=('PENDING', 'PROCESSING')).select_related('restroom')
                  .order_by('created_at')[:limit])
    waiting = list(Payout.objects.filter(status__in=('UNCLAIMED', 'ONHOLD')).select_related('restroom')
                   .order_by('-created_at')[:max(limit - len(moving), 0)])
    unfinished = moving + waiting
    for payout in unfinished:
        try:
            if not payout.batch_id:
                result = _send(checkout, payout, config)
                payout.batch_id, status = result['batch_id'], result['status'] or 'PENDING'
            else:
                status = payout_status(checkout.client.get_payout(payout.batch_id))
        except PayPalError as error:
            if not payout.batch_id and not _no_answer(error) and error.issue != 'DUPLICATE_REQUEST_ID':
                payout.status, payout.error = 'FAILED', str(error)[:255]
                payout.save(update_fields=['status', 'error'])
                release(payout)
            continue
        if status:
            payout.status = status
        payout.error = ''
        payout.save(update_fields=['batch_id', 'status', 'error'])
        if payout.status in SKIPPED_STATUSES:
            release(payout)
