"""Refunds the AI triage may give on its own, and the rules it can't bend.

The model only advises "full", "partial" or "none". Money goes back only
if the visit was paid and not refunded yet, the report came within
REFUND_WINDOW_MINUTES of paying, and the user had fewer than
MAX_REFUNDS_PER_DAY automatic refunds in the last 24 hours, counted by chat
user, by phone number and by PayPal account, so a new web chat id doesn't
reset the limit. Anything else is
left for staff in the admin. PayPal visits are refunded with PayPal's
Refunds API; simulated mobile money is refunded in simulation.
"""
import logging
import secrets
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q
from django.utils import timezone

from .models import AccessPass, Payment, PayPalOrder, Refund
from .paypal import PayPalError

logger = logging.getLogger(__name__)

CENT = Decimal('0.01')
SHARES = {'full': Decimal(1), 'partial': Decimal('0.5')}


def refund_visit(*, payment, report, decision, checkout, config, now):
    """Carry out the triage's decision within the rules.

    Returns (Refund, '') when money was sent back, or (None, why not) when
    it wasn't: the reason is noted on the report for staff."""
    share = SHARES.get(decision.get('refund'))
    if share is None:
        return None, ''
    if payment.status != Payment.Status.SUCCEEDED:
        return None, 'the visit was not paid or is already refunded'
    if Refund.objects.filter(payment=payment).exclude(status=Refund.Status.FAILED).exists():
        return None, 'the visit was already refunded'
    if now - payment.created_at > timedelta(minutes=config.refund_window_minutes):
        return None, f'reported more than {config.refund_window_minutes} minutes after paying'
    entered = AccessPass.objects.filter(payment=payment, checked_in_at__isnull=False).values_list(
        'checked_in_at', flat=True).first()
    if entered and share == 1:
        # The attendant let them in, so "I couldn't get in" needs a person to look at it.
        return None, f'the visitor was checked in at the door at {timezone.localtime(entered):%H:%M}'
    order = PayPalOrder.objects.filter(payment=payment).first()
    same_person = Q(payment__user_id=payment.user_id)
    if payment.phone:
        same_person |= Q(payment__phone=payment.phone)
    if order and order.payer_email:
        same_person |= Q(payment__paypal_order__payer_email__iexact=order.payer_email)
    recent = Refund.objects.filter(
        same_person, decided_by=Refund.DecidedBy.AI, created_at__gte=now - timedelta(days=1),
    ).exclude(status=Refund.Status.FAILED).distinct().count()
    if recent >= config.max_refunds_per_day:
        return None, 'the user already had an automatic refund today'

    return _give_back(payment=payment, report=report, share=share, checkout=checkout, config=config, now=now,
                      decided_by=Refund.DecidedBy.AI, category=decision.get('category', ''),
                      reason=decision.get('reply', ''), notes=decision.get('staff_note', ''))


def staff_refund(*, payment, report, share_name, checkout, config, now, staff_name=''):
    """A refund staff chose on the dashboard: no time window or daily limit,
    but still only for a paid visit that wasn't refunded yet."""
    share = SHARES.get(share_name)
    if share is None:
        return None, 'choose a full or a partial refund'
    if payment.status != Payment.Status.SUCCEEDED:
        return None, 'the visit was not paid or is already refunded'
    if Refund.objects.filter(payment=payment).exclude(status=Refund.Status.FAILED).exists():
        return None, 'the visit was already refunded'
    return _give_back(payment=payment, report=report, share=share, checkout=checkout, config=config, now=now,
                      decided_by=Refund.DecidedBy.STAFF, category='staff',
                      reason='A staff member refunded your visit.', notes=f'Refunded by {staff_name or "staff"}.')


def _give_back(*, payment, report, share, checkout, config, now, decided_by, category, reason, notes):
    order = PayPalOrder.objects.filter(payment=payment).first()
    if payment.provider == 'paypal' and order and checkout:
        amount = (order.amount * share).quantize(CENT, ROUND_HALF_UP)
        # A refund PayPal refused can be tried again (a new request id); one it
        # never answered keeps its id, so trying again can't refund twice.
        refused = Refund.objects.filter(payment=payment, status=Refund.Status.FAILED).count()
        request_id = f'refund-{payment.reference}' + (f'-{refused}' if refused else '')
        try:
            result = checkout.refund(payment, amount=amount, request_id=request_id,
                                     note=f'{config.bot_name}: refund for {payment.facility_name}')
        except PayPalError as error:
            logger.warning('PayPal refund for %s failed: %s', payment.reference, error)
            if error.status is not None:
                Refund.objects.create(
                    payment=payment, report=report, amount=amount, currency=order.currency,
                    status=Refund.Status.FAILED, decided_by=decided_by, category=category, reason=reason[:255],
                    notes=f'PayPal refused: {error.issue or error}'[:500], created_at=now,
                )
            return None, f'PayPal refused the refund ({error.issue or error})'
        status = Refund.Status.COMPLETED if result['status'] == 'COMPLETED' else Refund.Status.PENDING
        refund_id, currency = result['id'], order.currency
    elif payment.provider == 'tfpass':
        # A visit paid with the TapFlash Pass goes back to the pass.
        from .tfpass import for_reference, give_back, visit_value
        found = give_back(payment.reference)
        amount = visit_value(found) if found else Decimal(0)
        status, refund_id, currency = Refund.Status.COMPLETED, f'PASS-{payment.reference}'[:40], config.paypal_currency
    else:
        # Simulated payments are refunded in simulation too.
        amount = (payment.total * share).quantize(CENT, ROUND_HALF_UP)
        status, refund_id, currency = Refund.Status.COMPLETED, f'RF{secrets.token_hex(5).upper()}', payment.currency

    refund = Refund.objects.create(
        payment=payment, report=report, amount=amount, currency=currency, status=status,
        provider_refund_id=refund_id, decided_by=decided_by, category=category,
        reason=reason[:255], notes=notes, created_at=now,
    )
    if share == 1:
        payment.status = Payment.Status.REFUNDED
        payment.save(update_fields=['status'])
    return refund, ''
