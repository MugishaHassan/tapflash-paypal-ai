"""Septic tanks: a reading at 80% alerts the operator once; the order is paid with PayPal, then the
emptying company is paid with PayPal Payouts."""
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from bot.models import OutgoingReply
from ops import septic
from ops.models import EmptyingOrder, OperatorChat, SensorReading
from restrooms.models import Restroom


class FakePayPal:
    def __init__(self):
        self.calls = []

    def create_order(self, **kw):
        self.calls.append(('order', kw))
        return {'id': 'ORDER1', 'status': 'CREATED', 'approve_url': 'https://paypal.test/approve'}

    def capture_order(self, order_id):
        self.calls.append(('capture', order_id))
        return {'purchase_units': [{'payments': {'captures': [{'id': 'CAP1', 'status': 'COMPLETED'}]}}]}

    def create_payout(self, **kw):
        self.calls.append(('payout', kw))
        return {'batch_id': 'BATCH1', 'status': 'PENDING'}


@override_settings(SENSOR_TOKEN='sensor-secret', EMPTYING_PRICE_USD='25.00', EMPTYING_MAX_USD='60.00')
class SepticTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.market = Restroom.objects.create(code='city-market', name='City Market', latitude=-1.28, longitude=36.82,
                                              payout_email='op@example.com')
        OperatorChat.objects.create(restroom=self.market, user_id='web:op1')

    def test_a_full_tank_alerts_the_operator_once_with_days_left(self):
        septic.record(self.market, 60, sandbox=True, now=self.now - timedelta(days=4))
        order = septic.record(self.market, 84, sandbox=True, now=self.now, base_url='https://tf.test/')
        self.assertEqual(order.days_left, 1.8)       # 24 points in 4 days, 11 to go
        self.assertIsNone(septic.record(self.market, 90, sandbox=True, now=self.now), 'one open order per tank')
        alert = json.dumps(OutgoingReply.objects.get(user_id='web:op1').replies)
        self.assertIn('84% full, about 1.8 days left', alert)
        self.assertIn(f'/empty/{order.token}', alert)

    def test_the_operator_pays_with_paypal_and_the_company_is_paid_out(self):
        order = septic.record(self.market, 88, sandbox=True, now=self.now)
        paypal = FakePayPal(); checkout = SimpleNamespace(client=paypal)
        self.assertEqual(septic.start_payment(order, checkout, 'https://tf.test/'), 'https://paypal.test/approve')
        septic.finish_payment(order, checkout)
        order.refresh_from_db()
        self.assertEqual((order.status, order.capture_id, order.provider_batch_id), ('paid', 'CAP1', 'BATCH1'))
        self.assertEqual([c[0] for c in paypal.calls], ['order', 'capture', 'payout'])
        septic.finish_payment(order, checkout)   # a second return from PayPal changes nothing
        self.assertEqual(len(paypal.calls), 3)

    def test_the_price_limit_is_enforced_in_code(self):
        order = septic.record(self.market, 90, sandbox=True, now=self.now)
        order.price = Decimal('75.00'); order.save()
        with self.assertRaises(ValueError):
            septic.start_payment(order, SimpleNamespace(client=FakePayPal()), 'https://tf.test/')

    def test_the_sensor_api_needs_the_token_and_the_page_works(self):
        body = json.dumps({'restroom': 'city-market', 'kind': 'septic_level', 'value': 91})
        self.assertEqual(self.client.post('/api/sensors', body, content_type='application/json').status_code, 403)
        res = self.client.post('/api/sensors', body, content_type='application/json', HTTP_X_SENSOR_TOKEN='sensor-secret')
        self.assertEqual(res.json(), {'ok': True, 'alert': True})
        self.assertFalse(SensorReading.objects.get().sandbox)
        order = EmptyingOrder.objects.get()
        self.assertContains(self.client.get(f'/empty/{order.token}'), 'Order emptying, pay with PayPal')

    def test_the_sandbox_button_is_for_staff(self):
        self.assertEqual(self.client.post('/ops/sensor-sandbox', '{"restroom": "city-market"}',
                                          content_type='application/json').status_code, 302)
        self.client.force_login(User.objects.create_user('boss', password='pw-for-tests', is_staff=True))
        res = self.client.post('/ops/sensor-sandbox', '{"restroom": "city-market", "value": 85}', content_type='application/json')
        self.assertIn('Alert sent to the operator', res.json()['message'])
        self.assertTrue(SensorReading.objects.get().sandbox)
        self.assertEqual(self.client.get('/ops/septic.json').json()['tanks'][0]['level'], 85)
