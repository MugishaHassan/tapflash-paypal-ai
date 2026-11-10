from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from bot.state import PaymentDraft
from payments.methods import PaymentMethod
from payments.models import AccessPass, Payment
from payments.passes import Pass, PassStore, record_payment
from restrooms.facility import Facility
from restrooms.models import Restroom

NOW = datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc)
MPESA = PaymentMethod('mpesa', 'M-Pesa', needs_phone=True, fee_percent=Decimal(0))


def draft(code='demo-1', name='City Mall Restroom (demo)', phone='+254712345678'):
    facility = Facility(code=code, name=name, area='', lat=-1.28, lng=36.82, price=Decimal(100))
    return PaymentDraft(facility=facility, method=MPESA, phone=phone, price=Decimal(100), fee=Decimal(0),
                        total=Decimal(100))


class PassStoreTests(TestCase):
    def setUp(self):
        self.now = NOW
        self.passes = PassStore(lambda: self.now)

    def issue(self, reference, minutes, user='u', **draft_fields):
        return self.passes.issue(
            user_id=user, draft=draft(**draft_fields), reference=reference, provider='mock', currency='KES',
            otp='123456', issued_at=self.now, expires_at=self.now + timedelta(minutes=minutes),
        )

    def test_a_pass_is_saved_with_its_payment(self):
        issued = self.issue('TF0000000001', 36)
        self.assertEqual(issued, Pass(
            reference='TF0000000001', facility_code='demo-1', facility_name='City Mall Restroom (demo)',
            method_label='M-Pesa', phone='+254712345678', total=Decimal(100), otp='123456',
            expires_at=NOW + timedelta(minutes=36),
        ))
        payment = AccessPass.objects.get().payment
        self.assertEqual((payment.status, payment.provider, payment.created_at), ('succeeded', 'mock', NOW))
        self.assertIsNone(payment.restroom, 'demo restrooms have no row')
        self.assertEqual(self.passes.find('TF0000000001'), issued)
        self.assertIsNone(self.passes.find('TF9999999999'))

    def test_a_user_can_hold_several_passes_and_expired_ones_are_kept(self):
        first = self.issue('TF0000000001', 30, code='demo-1')
        self.now += timedelta(minutes=5)
        second = self.issue('TF0000000002', 60, code='demo-2')
        self.issue('TF0000000003', 60, user='someone else')
        self.assertEqual(self.passes.active('u'), [first, second], 'oldest first')

        self.now += timedelta(minutes=26)
        self.assertEqual(self.passes.active('u'), [second])
        self.assertFalse(self.passes.is_active(first))
        self.assertTrue(self.passes.is_active(second))
        self.assertFalse(self.passes.is_active(None))

        self.now += timedelta(hours=2)
        self.assertEqual(self.passes.active('u'), [])
        self.assertEqual(self.passes.latest('u'), second, 'the newest, even expired')
        self.assertIsNone(self.passes.latest('nobody'))

    def test_a_payment_for_a_restroom_in_the_database_links_to_it(self):
        restroom = Restroom.objects.create(code='nbi-kenyatta-ave', name='Kenyatta', latitude=-1.285, longitude=36.82)
        self.issue('TF0000000001', 30, code='nbi-kenyatta-ave', name='Kenyatta')
        self.assertEqual(Payment.objects.get().restroom, restroom)

    def test_a_failed_payment_is_recorded_without_a_reference(self):
        record_payment(user_id='u', draft=draft(phone=None), provider='mock', currency='KES', at=NOW,
                       failure_reason='Declined.')
        payment = Payment.objects.get()
        self.assertEqual((payment.status, payment.reference, payment.phone), ('failed', None, ''))
        self.assertEqual(payment.failure_reason, 'Declined.')
        self.assertFalse(AccessPass.objects.exists())
