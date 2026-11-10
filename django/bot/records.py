"""Problem reports and failed payments, for the staff to see in the admin.

Saving one must never break the conversation: an error is logged and the
chat goes on.
"""
import logging
from contextlib import contextmanager

from django.db import transaction

from payments.passes import record_payment
from restrooms.models import Restroom

from .models import ProblemReport

logger = logging.getLogger(__name__)


@contextmanager
def _saving(what):
    try:
        with transaction.atomic():
            yield
    except Exception:
        logger.exception('Could not save a %s', what)


def save_report(*, user_id, phone, facility, step, message, at, payment=None):
    """The saved report, or None if it could not be saved."""
    report = None
    with _saving('problem report'):
        report = ProblemReport.objects.create(
            user_id=user_id,
            phone=phone or '',
            restroom=Restroom.objects.filter(code=facility.code).first() if facility else None,
            facility_code=facility.code if facility else '',
            facility_name=facility.name if facility else '',
            step=step or '',
            message=message,
            payment=payment,
            created_at=at,
        )
    return report


def note_triage(report, decision, refund, held_back):
    """Write the AI's review on the report, for staff."""
    if report is None:
        return
    lines = [f"AI triage: {decision['category']}, severity {decision['severity']}/5, "
             f"advised refund: {decision['refund']}."]
    if decision['staff_note']:
        lines.append(decision['staff_note'])
    if refund:
        lines.append(f'Refunded {refund.amount} {refund.currency} automatically ({refund.provider_refund_id}).')
    elif held_back:
        lines.append(f'Not refunded automatically: {held_back}. Please check.')
    with _saving('triage note'):
        report.staff_notes = '\n'.join(lines)
        if decision['severity'] >= 4 or held_back:
            report.status = ProblemReport.Status.LOOKING
        report.save(update_fields=['staff_notes', 'status', 'resolved_at'])


def save_failed_payment(*, user_id, draft, provider, currency, reason, at):
    with _saving('failed payment'):
        record_payment(user_id=user_id, draft=draft, provider=provider, currency=currency, at=at, failure_reason=reason)
