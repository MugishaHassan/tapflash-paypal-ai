"""Access passes, kept apart from the chat so a pass survives "exit" or "hi".

A user can hold several (one per restroom paid for). Expired passes stay in
the database, so the chat can say when the last one ran out.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.utils import timezone

from restrooms.models import Restroom

from .models import AccessPass, Payment


@dataclass(frozen=True)
class Pass:
    """An access pass as the chat sees it: the AccessPass row and its payment."""
    reference: str
    facility_code: str
    facility_name: str
    method_label: str
    phone: str
    total: Decimal
    otp: str
    expires_at: datetime

    @classmethod
    def from_model(cls, access_pass):
        payment = access_pass.payment
        return cls(
            reference=payment.reference,
            facility_code=payment.facility_code,
            facility_name=payment.facility_name,
            method_label=payment.get_method_display(),
            phone=payment.phone,
            total=payment.total,
            otp=access_pass.otp,
            expires_at=access_pass.expires_at,
        )


def record_payment(*, user_id, draft, provider, currency, at, reference=None, failure_reason=''):
    """Save a payment attempt: paid when it has the provider's reference, failed otherwise.

    `draft` is the chat's PaymentDraft: facility, method, phone and amounts.
    """
    return Payment.objects.create(
        reference=reference,
        user_id=user_id,
        phone=draft.phone or '',
        method=draft.method.id,
        provider=provider,
        # Demo restrooms have no row, so only their code and name are kept.
        restroom=Restroom.objects.filter(code=draft.facility.code).first(),
        facility_code=draft.facility.code,
        facility_name=draft.facility.name,
        price=draft.price,
        fee=draft.fee,
        total=draft.total,
        currency=currency,
        status=Payment.Status.SUCCEEDED if reference else Payment.Status.FAILED,
        failure_reason=failure_reason,
        created_at=at,
    )


class PassStore:
    def __init__(self, clock=timezone.now):
        self.clock = clock

    def _rows(self, user_id):
        return AccessPass.objects.select_related('payment').filter(user_id=user_id)

    def issue(self, *, user_id, draft, reference, provider, currency, otp, issued_at, expires_at):
        """Save a successful payment and the pass it buys."""
        payment = record_payment(
            user_id=user_id, draft=draft, provider=provider, currency=currency, at=issued_at, reference=reference
        )
        access_pass = AccessPass.objects.create(
            payment=payment, user_id=user_id, otp=otp, issued_at=issued_at, expires_at=expires_at
        )
        return Pass.from_model(access_pass)

    def find(self, reference):
        access_pass = AccessPass.objects.select_related('payment').filter(payment__reference=reference).first()
        return Pass.from_model(access_pass) if access_pass else None

    @staticmethod
    def end(reference, at):
        """End a pass early, for a visit that was refunded."""
        AccessPass.objects.filter(payment__reference=reference, expires_at__gt=at).update(expires_at=at)

    @staticmethod
    def payment_for(reference):
        """The Payment row with this provider reference, or None."""
        return Payment.objects.filter(reference=reference).first() if reference else None

    def active(self, user_id):
        """The passes that still work, oldest first."""
        rows = self._rows(user_id).active(self.clock()).order_by('issued_at', 'pk')
        return [Pass.from_model(row) for row in rows]

    def latest(self, user_id):
        """The newest pass, even if it has expired."""
        access_pass = self._rows(user_id).order_by('-issued_at', '-pk').first()
        return Pass.from_model(access_pass) if access_pass else None

    def is_active(self, access_pass):
        return access_pass is not None and access_pass.expires_at > self.clock()
