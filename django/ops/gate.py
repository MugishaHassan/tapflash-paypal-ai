"""Checking visitors in at the restroom door.

The attendant scans the visitor's access QR code (or types the 6-digit code)
on /gate/. The pass must be for this restroom, paid and not refunded, still
valid, and not used before: each pass lets one person in once, so a shared
screenshot doesn't work twice. The visitor is told in the chat, and the
entrance shows on the operators' dashboard.
"""
import re
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from bot.formatting import format_money
from payments.paypal import PayPalError
from payments.models import PayPalOrder, AccessPass, Payment

QR_PREFIX = 'TAPFLASH-ACCESS'
SIMULATED = 'mock'
CODE = re.compile(r'^\d{6}$')


@dataclass(frozen=True)
class GateResult:
    ok: bool
    title: str
    detail: str
    access_pass: AccessPass | None = None


def _when(moment):
    return f'{timezone.localtime(moment):%H:%M}'


def find_pass(restroom, *, qr='', code='', now):
    """The AccessPass the QR code or the typed code stands for, or None. restroom None
    (staff at "any restroom") looks at every restroom."""
    rows = AccessPass.objects.select_related('payment')
    if qr:
        parts = qr.strip().split('|')
        if len(parts) < 4 or parts[0] != QR_PREFIX:
            return None
        return rows.filter(payment__reference=parts[1][:40], otp=parts[3][:6]).first()
    code = re.sub(r'\D', '', code or '')
    if not CODE.match(code):
        return None
    # Codes are only unique enough within a restroom: prefer one still valid and unused.
    rows = rows.filter(otp=code)
    if restroom is not None:
        rows = rows.filter(payment__facility_code=restroom.code)
    found = list(rows.order_by('-issued_at')[:5])
    return next((p for p in found if p.expires_at > now and not p.checked_in_at), found[0] if found else None)


def check_in(restroom, *, qr='', code='', by, now=None, accept_simulated=True):
    """restroom: where the attendant stands, or None for staff checking in at any restroom
    (the pass then says which one). accept_simulated: whether a simulated (test) mobile money payment opens the door.
    Off once real payments are on, so a test payment is never free entry."""
    now = now or timezone.now()
    found = find_pass(restroom, qr=qr, code=code, now=now)
    if found is None:
        return GateResult(False, 'Not a TapFlash pass', 'This code was not found. Ask the visitor to open the '
                                                         'access code in their chat (reply *pass*).')
    payment = found.payment
    if restroom is not None and payment.facility_code != restroom.code:
        return GateResult(False, 'Wrong restroom', f'This pass is for {payment.facility_name}, not {restroom.name}.',
                          found)
    if payment.status == Payment.Status.REFUNDED:
        return GateResult(False, 'Refunded', 'This visit was refunded, so the pass no longer works.', found)
    if payment.status != Payment.Status.SUCCEEDED:
        return GateResult(False, 'Not paid', 'This visit was not paid.', found)
    simulated = payment.provider == SIMULATED
    if simulated and not accept_simulated:
        return GateResult(False, 'Test payment', 'This visit was paid in test mode (simulated mobile money), '
                                                 'not with real money.', found)
    if found.checked_in_at:
        return GateResult(False, 'Already used', f'This pass was used at {_when(found.checked_in_at)}'
                                                 f'{" by " + found.checked_in_by if found.checked_in_by else ""}.',
                          found)
    if found.expires_at <= now:
        return GateResult(False, 'Expired', f'This pass ended at {_when(found.expires_at)}.', found)
    took = ''
    order = PayPalOrder.objects.filter(payment=payment).first()
    if order and order.authorization_id and not order.capture_id:
        # "Pay when the door opens": PayPal held the money at payment; the scan takes it.
        from bot.handler import default_engine
        checkout = default_engine().checkout
        if checkout is not None:
            try:
                with transaction.atomic():
                    checkout.take_held(payment)
                took = ' PayPal took the held payment now.'
            except PayPalError:
                return GateResult(False, 'Payment not taken', 'PayPal could not take the held payment. Ask the '
                                                              'visitor to pay again in the chat.', found)
    with transaction.atomic():
        # Only one scan can win, even if two phones scan the same code at once.
        won = AccessPass.objects.filter(pk=found.pk, checked_in_at__isnull=True).update(
            checked_in_at=now, checked_in_by=by[:150])
    if not won:
        found.refresh_from_db()
        return GateResult(False, 'Already used', f'This pass was used at {_when(found.checked_in_at)}.', found)
    found.checked_in_at, found.checked_in_by = now, by[:150]
    paid = format_money(payment.total, payment.currency)
    how = f'{payment.get_method_display()} (simulated)' if simulated else payment.get_method_display()
    where = f'{payment.facility_name}: p' if restroom is None else 'P'
    return GateResult(True, 'Let them in', f'{where}aid {paid} by {how} at '
                                           f'{_when(payment.created_at)}. Valid until {_when(found.expires_at)}.{took}',
                      found)


def visitor_text(result, restroom_name):
    return (f'You are checked in at *{restroom_name}* ({_when(result.access_pass.checked_in_at)}). '
            'Something wrong inside? Reply here to report it.')
