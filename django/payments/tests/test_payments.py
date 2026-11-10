from datetime import timedelta
from decimal import Decimal

from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from bot.config import build_config
from payments.methods import payment_methods
from payments.mock import MockPayments
from payments.models import AccessPass, Payment


def charge(phone):
    return MockPayments().charge(
        user_id='254712345678', method='mpesa', phone=phone, amount=Decimal(100), currency='KES', facility_code='a'
    )


class MockPaymentTests(SimpleTestCase):
    def test_payments_succeed_with_a_reference(self):
        result = charge('+254712345678')
        self.assertTrue(result.ok)
        self.assertRegex(result.reference, r'^TF[0-9A-F]{10}$')
        self.assertNotEqual(charge('+254712345678').reference, result.reference)

    def test_card_payments_have_no_phone_and_succeed(self):
        self.assertTrue(charge(None).ok)

    def test_numbers_ending_in_0000_are_declined(self):
        result = charge('+254712340000')
        self.assertFalse(result.ok)
        self.assertIsNone(result.reference)
        self.assertEqual(result.reason, 'The payment was declined (test numbers ending in 0000 always fail).')

    def test_the_mock_says_it_is_a_test(self):
        self.assertEqual(MockPayments.name, 'mock')
        self.assertTrue(MockPayments.is_test_mode)


class PaymentMethodTests(SimpleTestCase):
    def test_methods_in_order_with_their_fees(self):
        methods = payment_methods(build_config({'CARD_FEE_PERCENT': '2.5'}))
        self.assertEqual(
            [(m.id, m.label, m.needs_phone, m.fee_percent) for m in methods],
            [
                ('mpesa', 'M-Pesa', True, Decimal(0)),
                ('airtel', 'Airtel Money', True, Decimal(0)),
                ('card', 'Card (Visa / Mastercard)', False, Decimal('2.5')),
                ('paypal', 'PayPal', False, Decimal('2.5')),
            ],
        )


def make_payment(**fields):
    return Payment.objects.create(**{
        'reference': 'TF0123456789', 'user_id': '254712345678', 'phone': '+254712345678', 'method': 'mpesa',
        'provider': 'mock', 'facility_code': 'demo-1', 'facility_name': 'City Mall Restroom (demo)',
        'price': Decimal(100), 'fee': Decimal(0), 'total': Decimal(100), 'currency': 'KES', 'status': 'succeeded',
        **fields,
    })


class AccessPassTests(TestCase):
    def test_a_pass_works_until_it_expires(self):
        now = timezone.now()
        access_pass = AccessPass.objects.create(
            payment=make_payment(), user_id='254712345678', otp='123456', expires_at=now + timedelta(minutes=30)
        )
        self.assertTrue(access_pass.is_active())
        self.assertTrue(access_pass.is_active(at=now + timedelta(minutes=29)))
        self.assertFalse(access_pass.is_active(at=now + timedelta(minutes=30)))
        self.assertEqual(list(AccessPass.objects.active()), [access_pass])
        self.assertEqual(list(AccessPass.objects.active(at=now + timedelta(hours=1))), [])

    def test_failed_payments_have_no_reference(self):
        make_payment(reference=None, status='failed', failure_reason='Declined')
        make_payment(reference=None, status='failed', failure_reason='Declined')
        self.assertEqual(Payment.objects.filter(reference=None).count(), 2)
        self.assertTrue(str(Payment.objects.first()).startswith('Failed payment'))

    def test_a_payment_with_a_pass_cannot_be_deleted(self):
        payment = make_payment()
        AccessPass.objects.create(payment=payment, user_id='254712345678', otp='123456', expires_at=timezone.now())
        with self.assertRaises(ProtectedError):
            payment.delete()
