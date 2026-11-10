from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from bot.config import build_config
from payments.models import Payment, Refund
from payments.refunds import refund_visit
from payments.tests.test_paypal import ENV

FULL = {'refund': 'full', 'category': 'closed', 'reply': 'Sorry.', 'staff_note': 'Locked.'}


class DailyLimitTests(TestCase):
    def setUp(self):
        self.config = build_config(ENV)
        self.now = timezone.now()
        self.count = 0

    def paid(self, user_id, phone=''):
        self.count += 1
        return Payment.objects.create(
            reference=f'TF{self.count:010d}', user_id=user_id, phone=phone, method='mpesa', provider='mock',
            facility_code='city-market', facility_name='City Market', price=Decimal(100), fee=Decimal(0),
            total=Decimal(100), currency='KES', status='succeeded', created_at=self.now,
        )

    def refund(self, payment):
        return refund_visit(payment=payment, report=None, decision=FULL, checkout=None, config=self.config,
                            now=self.now)

    def test_a_new_chat_id_with_the_same_phone_does_not_reset_the_limit(self):
        self.assertIsNotNone(self.refund(self.paid('web:a', '+254700000001'))[0])
        refund, why = self.refund(self.paid('web:b', '+254700000001'))
        self.assertIsNone(refund)
        self.assertIn('already had an automatic refund', why)
        self.assertEqual(Refund.objects.count(), 1)

    def test_different_people_each_get_one(self):
        self.assertIsNotNone(self.refund(self.paid('web:a', '+254700000001'))[0])
        self.assertIsNotNone(self.refund(self.paid('web:b', '+254700000002'))[0])
