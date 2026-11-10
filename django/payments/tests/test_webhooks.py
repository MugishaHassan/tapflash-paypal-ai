import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from ops.payouts import earnings, pay_out
from payments.models import AccessPass, Payment, PayPalOrder, Payout, Refund
from restrooms.models import Restroom


def verified_engine(valid=True):
    client = SimpleNamespace(verify_webhook=lambda **kwargs: valid)
    return SimpleNamespace(checkout=SimpleNamespace(client=client))


class WebhookTests(TestCase):
    def setUp(self):
        self.config = replace(settings.TAPFLASH, paypal_currency='USD', paypal_rate=Decimal(100))
        self.now = timezone.now()
        self.restroom = Restroom.objects.create(code='city-market', name='City Market', latitude=-1.28,
                                                longitude=36.82, payout_email='op@example.com')
        self.payment = Payment.objects.create(
            reference='CAP-1', user_id='web:u1', method='paypal', provider='paypal', restroom=self.restroom,
            facility_code='city-market', facility_name='City Market', price=Decimal(100), fee=Decimal(0),
            total=Decimal(100), currency='KES', status='succeeded', created_at=self.now - timedelta(hours=2),
        )
        PayPalOrder.objects.create(
            order_id='ORDER1', payment=self.payment, user_id='web:u1', facility_code='city-market',
            facility_name='City Market', local_total=Decimal(100), local_currency='KES', amount=Decimal('1.00'),
            currency='USD', approve_url='https://www.sandbox.paypal.com/checkoutnow?token=ORDER1',
            status='completed', capture_id='CAP-1',
        )
        AccessPass.objects.create(payment=self.payment, user_id='web:u1', otp='123456',
                                  expires_at=self.now + timedelta(minutes=30))

    def post(self, event, valid=True):
        with mock.patch('channels.paypal_views.default_engine', return_value=verified_engine(valid)), \
             self.settings(TAPFLASH=replace(self.config, paypal_webhook_id='WH-1')):
            return self.client.post('/paypal/webhook', json.dumps(event), content_type='application/json')

    def test_a_reversed_capture_ends_the_pass_and_comes_off_the_next_payout(self):
        pay_out(self.restroom, checkout=None, config=self.config, now=self.now - timedelta(hours=1))
        event = {'event_type': 'PAYMENT.CAPTURE.REVERSED', 'resource': {'id': 'CAP-1', 'status': 'REVERSED'}}
        self.assertEqual(self.post(event).status_code, 200)
        self.assertEqual(self.post(event).status_code, 200)  # PayPal sends it again
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.REFUNDED)
        refund = Refund.objects.get()
        self.assertEqual((refund.amount, refund.decided_by), (Decimal('1.00'), Refund.DecidedBy.PAYPAL))
        self.assertFalse(AccessPass.objects.active().exists())
        self.assertEqual(earnings(self.restroom, self.config).refunded, Decimal('1.00'))

    def test_a_denied_capture_after_a_partial_refund_takes_only_the_rest(self):
        Refund.objects.create(payment=self.payment, amount=Decimal('0.50'), currency='USD', status='completed',
                              reason='Dirty')
        self.post({'event_type': 'PAYMENT.CAPTURE.DENIED', 'resource': {'id': 'CAP-1'}})
        self.assertEqual(sorted(r.amount for r in Refund.objects.all()), [Decimal('0.50'), Decimal('0.50')])

    def test_a_payout_batch_event_updates_the_payout(self):
        Payout.objects.create(sender_batch_id='tf-1-1', batch_id='BATCH9', kind='earnings', restroom=self.restroom,
                              receiver='op@example.com', amount=Decimal(1), status='PENDING')
        self.post({'event_type': 'PAYMENT.PAYOUTSBATCH.SUCCESS',
                   'resource': {'batch_header': {'payout_batch_id': 'BATCH9', 'batch_status': 'SUCCESS'}}})
        self.assertEqual(Payout.objects.get().status, 'SUCCESS')

    def test_an_unverified_event_changes_nothing(self):
        response = self.post({'event_type': 'PAYMENT.CAPTURE.REVERSED', 'resource': {'id': 'CAP-1'}}, valid=False)
        self.assertEqual(response.status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)

    def test_a_refund_made_in_the_paypal_dashboard_is_recorded_once(self):
        event = {'event_type': 'PAYMENT.CAPTURE.REFUNDED', 'resource': {
            'id': 'REF-9', 'status': 'COMPLETED', 'amount': {'value': '1.00', 'currency_code': 'USD'},
            'links': [{'rel': 'up', 'href': 'https://api-m.sandbox.paypal.com/v2/payments/captures/CAP-1'}]}}
        self.post(event)
        self.post(event)
        refund = Refund.objects.get()
        self.assertEqual((refund.amount, refund.provider_refund_id), (Decimal('1.00'), 'REF-9'))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.REFUNDED)
        self.assertFalse(AccessPass.objects.active().exists())
