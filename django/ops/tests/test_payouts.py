from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from ops.payouts import PayoutError, earnings, pay_out, refresh_pending
from payments.models import Payment, PayPalOrder, Payout, Refund
from payments.paypal import PayPalError
from restrooms.models import Restroom


class FakeClient:
    def __init__(self, fail=None):
        self.fail = fail
        self.sent = []

    def create_payout(self, **kwargs):
        self.sent.append(kwargs)
        if self.fail:
            error, self.fail = self.fail, None
            raise error
        return {'batch_id': f'BATCH{len(self.sent)}', 'status': 'PENDING'}

    def get_payout(self, batch_id):
        return {'batch_header': {'batch_status': 'SUCCESS'}}


def checkout_with(client):
    return SimpleNamespace(client=client)


class PayoutTests(TestCase):
    def setUp(self):
        self.config = replace(settings.TAPFLASH, paypal_currency='USD', paypal_rate=Decimal(100))
        self.restroom = Restroom.objects.create(
            code='city-market', name='City Market', latitude=-1.28, longitude=36.82,
            payout_email='operator@example.com', operator_share_percent=80,
        )
        self.now = timezone.now()
        self.count = 0

    def visit(self, *, provider='paypal', usd='1.00', kes='100', ago=timedelta(hours=1)):
        self.count += 1
        payment = Payment.objects.create(
            reference=f'TF{self.count:010d}', user_id=f'u{self.count}', method='paypal' if provider == 'paypal' else 'mpesa',
            provider=provider, restroom=self.restroom, facility_code=self.restroom.code,
            facility_name=self.restroom.name, price=Decimal(kes), fee=Decimal(0), total=Decimal(kes), currency='KES',
            status='succeeded', created_at=self.now - ago,
        )
        if provider == 'paypal':
            PayPalOrder.objects.create(order_id=f'ORDER{self.count}', payment=payment, amount=Decimal(usd),
                                       currency='USD', user_id=payment.user_id, local_total=Decimal(kes),
                                       local_currency='KES', facility_code=self.restroom.code,
                                       facility_name=self.restroom.name,
                                       approve_url=f'https://www.sandbox.paypal.com/checkoutnow?token={self.count}')
        return payment

    def refund(self, payment, usd, at):
        return Refund.objects.create(payment=payment, amount=Decimal(usd), currency='USD', status='completed',
                                     reason='Dirty', created_at=at)

    def test_share_of_paid_visits_minus_refunds(self):
        first = self.visit(usd='1.00')
        self.visit(usd='1.50')
        self.refund(first, '0.50', self.now)
        due = earnings(self.restroom, self.config)
        self.assertEqual((due.visits, due.gross, due.refunded), (2, Decimal('2.50'), Decimal('0.50')))
        self.assertEqual(due.owed, Decimal('1.60'))

    def test_simulated_visits_count_only_without_paypal(self):
        self.visit(usd='1.00')
        self.visit(provider='mock', kes='200')
        self.assertEqual(earnings(self.restroom, self.config).owed, Decimal('2.40'))
        self.assertEqual(earnings(self.restroom, self.config, real_only=True).owed, Decimal('0.80'))

    def test_pays_out_once_then_nothing_is_owed(self):
        self.visit(usd='2.00')
        client = FakeClient()
        payout = pay_out(self.restroom, checkout=checkout_with(client), config=self.config, now=self.now)
        self.assertEqual((payout.amount, payout.batch_id, payout.status), (Decimal('1.60'), 'BATCH1', 'PENDING'))
        self.assertEqual(client.sent[0]['items'][0]['receiver'], 'operator@example.com')
        with self.assertRaises(PayoutError):
            pay_out(self.restroom, checkout=checkout_with(client), config=self.config, now=self.now)
        self.assertEqual(len(client.sent), 1)

    def test_refund_after_payout_is_taken_off_the_next_one(self):
        old = self.visit(usd='2.00', ago=timedelta(hours=3))
        pay_out(self.restroom, checkout=None, config=self.config, now=self.now - timedelta(hours=2))
        self.refund(old, '2.00', self.now - timedelta(hours=1))
        self.visit(usd='3.00', ago=timedelta(minutes=30))
        due = earnings(self.restroom, self.config)
        self.assertEqual(due.visits, 1)
        self.assertEqual(due.owed, Decimal('0.80'))  # (3.00 - 2.00) x 80%

    def test_no_answer_keeps_it_pending_and_resends_with_the_same_id(self):
        self.visit(usd='1.00')
        client = FakeClient(fail=PayPalError('Could not reach PayPal: Timeout'))
        with self.assertRaises(PayoutError):
            pay_out(self.restroom, checkout=checkout_with(client), config=self.config, now=self.now)
        payout = Payout.objects.get()
        self.assertEqual((payout.status, payout.batch_id), ('PENDING', ''))
        self.assertEqual(earnings(self.restroom, self.config, real_only=True).owed, Decimal(0))

        refresh_pending(checkout_with(client), self.config)
        payout.refresh_from_db()
        self.assertEqual(payout.batch_id, 'BATCH2')
        self.assertEqual(client.sent[0]['sender_batch_id'], client.sent[1]['sender_batch_id'])

    def test_refused_payout_is_failed_and_still_owed(self):
        self.visit(usd='1.00')
        client = FakeClient(fail=PayPalError('Receiver unregistered', status=422))
        with self.assertRaises(PayoutError):
            pay_out(self.restroom, checkout=checkout_with(client), config=self.config, now=self.now)
        self.assertEqual(Payout.objects.get().status, 'FAILED')
        self.assertEqual(earnings(self.restroom, self.config, real_only=True).owed, Decimal('0.80'))

    def test_operator_without_email(self):
        self.restroom.payout_email = ''
        self.visit()
        with self.assertRaises(PayoutError):
            pay_out(self.restroom, checkout=None, config=self.config, now=self.now)

    def test_a_visit_saved_while_a_payout_runs_is_paid_once_next_time(self):
        self.visit(usd='2.00', ago=timedelta(hours=1))
        pay_out(self.restroom, checkout=None, config=self.config, now=self.now)
        # Saved with an earlier time than the payout (it was in flight), but not covered by it.
        self.visit(usd='1.00', ago=timedelta(minutes=5))
        self.assertEqual(earnings(self.restroom, self.config).owed, Decimal('0.80'))
        second = pay_out(self.restroom, checkout=None, config=self.config, now=self.now)
        self.assertEqual(second.visits, 1)
        self.assertEqual(earnings(self.restroom, self.config).owed, Decimal(0))

    def test_a_paypal_server_error_is_treated_as_no_answer(self):
        self.visit(usd='1.00')
        client = FakeClient(fail=PayPalError('Internal error', status=503))
        with self.assertRaises(PayoutError):
            pay_out(self.restroom, checkout=checkout_with(client), config=self.config, now=self.now)
        self.assertEqual(Payout.objects.get().status, 'PENDING')
        self.assertEqual(earnings(self.restroom, self.config, real_only=True).owed, Decimal(0))

    def test_a_denied_batch_makes_its_visits_owed_again(self):
        from payments.webhooks import handle_payout_batch
        self.visit(usd='1.00')
        pay_out(self.restroom, checkout=checkout_with(FakeClient()), config=self.config, now=self.now)
        handle_payout_batch({'batch_header': {'payout_batch_id': 'BATCH1', 'batch_status': 'DENIED'}})
        self.assertEqual(earnings(self.restroom, self.config, real_only=True).owed, Decimal('0.80'))


class SeedResetTests(TestCase):
    def test_reset_keeps_real_visits_and_their_payouts(self):
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_demo', '--visits', '3', '--no-ai', stdout=StringIO())
        restroom = Restroom.objects.get(code='nbi-kenyatta-ave')
        real = Payment.objects.create(
            reference='REAL1', user_id='web:judge', method='paypal', provider='paypal', restroom=restroom,
            facility_code=restroom.code, facility_name=restroom.name, price=Decimal(100), fee=Decimal(1),
            total=Decimal(101), currency='KES', status='succeeded')
        config = replace(settings.TAPFLASH, paypal_currency='USD')
        payout = pay_out(restroom, checkout=None, config=config, now=timezone.now())
        call_command('seed_demo', '--reset', '--visits', '3', '--no-ai', stdout=StringIO())
        real.refresh_from_db()
        self.assertTrue(Payout.objects.filter(pk=payout.pk).exists())
        self.assertEqual(real.payout_id, payout.pk)
