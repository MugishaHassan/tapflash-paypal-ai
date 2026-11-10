"""The prepaid TapFlash Pass, and 'pay when the door opens' (PayPal authorize, then capture or void)."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.test import TestCase

from ops.payouts import payment_value
from payments import tfpass
from payments.checkout import PayPalCheckout
from payments.models import Payment, PayPalOrder, VisitPass


class FakePayPal:
    def __init__(self):
        self.calls = []

    def capture_order(self, order_id):
        self.calls.append(('capture', order_id))
        return {'purchase_units': [{'payments': {'captures': [{'id': 'CAP9', 'status': 'COMPLETED',
                                                                'seller_receivable_breakdown': {'paypal_fee': {'value': '0.91'}}}]}}]}

    def get_order(self, order_id):
        return {'status': 'APPROVED'}

    def authorize_order(self, order_id):
        self.calls.append(('authorize', order_id))
        return {'status': 'COMPLETED', 'payer': {}, 'purchase_units': [{'payments': {'authorizations': [{'id': 'AUTH1', 'status': 'CREATED'}]}}]}

    def capture_authorization(self, authorization_id, *, amount, currency, request_id):
        self.calls.append(('take', authorization_id, str(amount)))
        return {'id': 'CAP1', 'status': 'COMPLETED'}

    def void_authorization(self, authorization_id):
        self.calls.append(('void', authorization_id))
        return {}


def payment(reference, provider='paypal'):
    return Payment.objects.create(reference=reference, user_id='web:v', method='paypal', provider=provider, facility_code='m',
                                  facility_name='Market', price=Decimal(100), fee=Decimal(1), total=Decimal(101),
                                  currency='KES', status='succeeded')


class TapFlashPassTests(TestCase):
    def test_one_paypal_payment_buys_many_visits_and_a_refund_gives_one_back(self):
        config = replace(settings.TAPFLASH, tfpass_visits=20, tfpass_price='12.00')
        offer = tfpass.offer('web:v', 'web', config)
        self.assertIsNone(tfpass.use_visit('web:v'), 'an unpaid pass has no visits')
        offer.paypal_order_id = 'ORD'; offer.save()
        tfpass.finish_payment(offer, SimpleNamespace(client=FakePayPal()))
        offer.refresh_from_db()
        self.assertEqual((offer.visits_left, offer.paypal_fee, offer.capture_id), (20, Decimal('0.91'), 'CAP9'))
        reference = tfpass.use_visit('web:v')
        self.assertTrue(reference.startswith(f'TFP{offer.pk}-1-'))
        self.assertEqual(tfpass.visits_left('web:v'), 19)
        visit = payment(reference, provider='tfpass')
        self.assertEqual(payment_value(visit, config), (Decimal('0.60'), 0))
        tfpass.give_back(reference)
        self.assertEqual(tfpass.visits_left('web:v'), 20)


class PayWhenTheDoorOpensTests(TestCase):
    def setUp(self):
        self.paypal = FakePayPal()
        self.checkout = PayPalCheckout(self.paypal, replace(settings.TAPFLASH, paypal_hold=True))
        self.order = PayPalOrder.objects.create(order_id='O1', user_id='web:v', facility_code='m', facility_name='Market',
                                                local_total=Decimal(101), local_currency='KES', amount=Decimal('0.78'),
                                                currency='USD', approve_url='https://paypal.test')

    def test_held_at_payment_taken_at_the_door(self):
        result = self.checkout.finish('O1')
        self.assertEqual((result.ok, result.reference), (True, 'AUTH1'))
        self.order.refresh_from_db(); self.order.payment = payment('AUTH1'); self.order.save()
        self.assertEqual(self.checkout.take_held(self.order.payment), 'CAP1')
        self.assertEqual(self.checkout.take_held(self.order.payment), 'CAP1', 'a second scan takes nothing more')
        self.assertEqual([c[0] for c in self.paypal.calls], ['authorize', 'take'])

    def test_a_locked_door_releases_the_hold_and_a_partial_refund_takes_only_the_rest(self):
        self.checkout.finish('O1')
        self.order.refresh_from_db(); self.order.payment = payment('AUTH1'); self.order.save()
        self.assertEqual(self.checkout.refund(self.order.payment, amount=Decimal('0.78'), note='', request_id='r')['id'], 'VOID-AUTH1')
        self.assertEqual(self.paypal.calls[-1], ('void', 'AUTH1'))
        self.order.capture_id = ''; self.order.save()
        self.checkout.refund(self.order.payment, amount=Decimal('0.39'), note='', request_id='r2')
        self.assertEqual(self.paypal.calls[-1], ('take', 'AUTH1', '0.39'))
