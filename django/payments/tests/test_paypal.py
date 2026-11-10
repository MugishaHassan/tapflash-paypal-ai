"""Paying with PayPal: the REST client, the checkout and the chat around it.

FakePayPal stands in for PayPal's sandbox: orders start waiting for the
buyer, approve() is the buyer paying on PayPal's page, and capturing gives a
capture id."""
import json
from dataclasses import replace
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from bot.config import build_config
from bot.models import OutgoingReply
from bot.state import Step
from bot.tests.helpers import ChatTester, last_menu, texts
from payments.checkout import PayPalCheckout, to_paypal_amount
from payments.models import Payment, PayPalOrder
from payments.paypal import PayPalClient, PayPalError

ENV = {'PUBLIC_URL': 'https://bot.example.com'}


class FakePayPal:
    live = False

    def __init__(self):
        self.orders = {}
        self.created = []
        self.captures = 0

    def create_order(self, **fields):
        order_id = f'ORDER{len(self.orders) + 1}'
        self.created.append(fields)
        self.orders[order_id] = {'id': order_id, 'status': 'PAYER_ACTION_REQUIRED', 'amount': fields['amount']}
        return {'id': order_id, 'status': 'PAYER_ACTION_REQUIRED',
                'approve_url': f'https://www.sandbox.paypal.com/checkoutnow?token={order_id}'}

    def approve(self, order_id):
        self.orders[order_id]['status'] = 'APPROVED'

    def get_order(self, order_id):
        return self.orders[order_id]

    def capture_order(self, order_id):
        self.captures += 1
        order = self.orders[order_id]
        order.update({
            'status': 'COMPLETED',
            'payer': {'email_address': 'buyer@example.com', 'name': {'given_name': 'Amina', 'surname': 'Otieno'}},
            'purchase_units': [{'payments': {'captures': [{'id': f'CAP-{order_id}', 'status': 'COMPLETED'}]}}],
        })
        return order

    def refund_capture(self, capture_id, **fields):
        return {'id': f'REF-{capture_id}', 'status': 'COMPLETED'}


def chat_with_paypal():
    fake = FakePayPal()
    checkout = PayPalCheckout(fake, build_config(ENV))
    return ChatTester(ENV, user='web:user-1', checkout=checkout), fake


def uses(t):
    """The pages answer with the test chat's engine (and its fake PayPal)."""
    return mock.patch('bot.handler.default_engine', return_value=t.engine)


def reach_paypal_link(t, method='4'):
    t.say('hi')
    t.say('1')  # the most clean
    t.share_location()
    t.say('1')  # the first restroom
    t.say('1')  # pay
    t.say(method)  # 3 card, 4 PayPal
    return t.say('1')  # pay with PayPal


@override_settings(TAPFLASH=replace(settings.TAPFLASH, web_chat=True))
class ChatPaysWithPayPalTests(TestCase):
    def test_the_summary_shows_what_paypal_charges_in_dollars(self):
        t, _ = chat_with_paypal()
        t.say('hi'), t.say('1'), t.share_location(), t.say('1'), t.say('1')
        out = texts(t.say('4'))
        self.assertIn('PayPal charges *USD', out)
        self.assertIn('Pay with PayPal', out)

    def test_paying_sends_a_paypal_link_and_waits(self):
        t, fake = chat_with_paypal()
        out = texts(reach_paypal_link(t))
        self.assertIn('https://www.sandbox.paypal.com/checkoutnow?token=ORDER1', out)
        self.assertIn('I have paid', out)
        self.assertIn('sandbox', out)
        order = PayPalOrder.objects.get()
        self.assertEqual((order.status, order.currency, order.channel), ('created', 'USD', 'web'))
        self.assertEqual(order.amount, to_paypal_amount(order.local_total, Decimal(129)))
        sent = fake.created[0]
        self.assertEqual(sent['return_url'], 'https://bot.example.com/paypal/return')
        self.assertEqual(sent['cancel_url'], 'https://bot.example.com/paypal/cancel')
        self.assertEqual(sent['landing_page'], 'NO_PREFERENCE')
        self.assertFalse(Payment.objects.exists())

    def test_a_card_opens_paypal_guest_checkout(self):
        t, fake = chat_with_paypal()
        out = texts(reach_paypal_link(t, method='3'))
        self.assertIn("You don't need a PayPal account", out)
        self.assertEqual(fake.created[0]['landing_page'], 'GUEST_CHECKOUT')

    def test_i_have_paid_before_paying_shows_the_link_again(self):
        t, fake = chat_with_paypal()
        reach_paypal_link(t)
        out = texts(t.say('1'))
        self.assertIn("hasn't confirmed", out)
        self.assertIn('checkoutnow?token=ORDER1', out)
        self.assertEqual(fake.captures, 0)

    def test_i_have_paid_after_paying_captures_and_starts_the_journey(self):
        t, fake = chat_with_paypal()
        reach_paypal_link(t)
        fake.approve('ORDER1')
        out = texts(t.say('I have paid'))
        self.assertIn('Payment received. Reference *CAP-ORDER1*', out)
        payment = Payment.objects.get()
        self.assertEqual((payment.provider, payment.method, payment.reference), ('paypal', 'paypal', 'CAP-ORDER1'))
        order = PayPalOrder.objects.get()
        self.assertEqual((order.status, order.payment, order.payer_email), ('completed', payment, 'buyer@example.com'))
        self.assertEqual(order.payer_name, 'Amina Otieno')

    def test_coming_back_from_paypal_pays_once_and_pushes_the_receipt(self):
        t, fake = chat_with_paypal()
        reach_paypal_link(t)
        fake.approve('ORDER1')
        with uses(t):
            page = self.client.get('/paypal/return', {'token': 'ORDER1'})
        self.assertContains(page, 'Payment received')
        self.assertContains(page, 'Back to the chat')
        # The chat gets the receipt without asking, once.
        waiting = self.client.get('/api/outbox', {'userId': 'user-1'}).json()['replies']
        self.assertIn('Payment received', json.dumps(waiting))
        self.assertEqual(self.client.get('/api/outbox', {'userId': 'user-1'}).json()['replies'], [])
        # Coming back twice (a reload) takes no second payment.
        with uses(t):
            self.client.get('/paypal/return', {'token': 'ORDER1'})
        self.assertEqual((fake.captures, Payment.objects.count()), (1, 1))

    def test_the_chat_moves_on_and_a_late_approval_takes_no_money(self):
        t, fake = chat_with_paypal()
        reach_paypal_link(t)
        t.say('2')  # pay another way
        fake.approve('ORDER1')
        with uses(t):
            page = self.client.get('/paypal/return', {'token': 'ORDER1'})
        self.assertContains(page, 'Payment not taken')
        self.assertEqual(fake.captures, 0)
        self.assertEqual(PayPalOrder.objects.get().status, 'cancelled')

    def test_cancelling_on_paypal_brings_back_the_payment_methods(self):
        t, _ = chat_with_paypal()
        reach_paypal_link(t)
        with uses(t):
            page = self.client.get('/paypal/cancel', {'token': 'ORDER1'})
        self.assertContains(page, 'Payment cancelled')
        waiting = json.dumps(self.client.get('/api/outbox', {'userId': 'user-1'}).json()['replies'])
        self.assertIn('Nothing was charged', waiting)
        self.assertIn('M-Pesa', waiting)
        self.assertEqual(PayPalOrder.objects.get().status, 'cancelled')

    def test_an_unknown_order_is_refused(self):
        self.assertEqual(self.client.get('/paypal/return', {'token': 'NOPE'}).status_code, 404)

    def test_paypal_down_while_creating_the_order_offers_another_way(self):
        t, fake = chat_with_paypal()
        with mock.patch.object(fake, 'create_order', side_effect=PayPalError('down')):
            out = texts(reach_paypal_link(t))
        self.assertIn('Payment failed', out)
        self.assertIn('Choose another payment method', out)

    def test_without_paypal_settings_cards_stay_simulated(self):
        t = ChatTester()
        out = texts(t.pay_by_card())
        self.assertIn('Payment received', out)
        self.assertFalse(PayPalOrder.objects.exists())


@override_settings(BRIDGE_TOKEN='bridge-secret')
class BridgeOutboxTests(TestCase):
    def test_the_bridge_collects_replies_for_whatsapp_ids(self):
        OutgoingReply.objects.create(user_id='web:254712345678@c.us', channel='bridge',
                                     replies=[{'kind': 'text', 'text': 'Paid'}])
        OutgoingReply.objects.create(user_id='web:browser-1', channel='web', replies=[{'kind': 'text', 'text': 'x'}])
        answer = self.client.get('/api/outbox', HTTP_AUTHORIZATION='Bearer bridge-secret').json()
        self.assertEqual(answer, {'messages': [{'userId': '254712345678@c.us',
                                                'replies': [{'kind': 'text', 'text': 'Paid'}]}]})
        again = self.client.get('/api/outbox', HTTP_AUTHORIZATION='Bearer bridge-secret').json()
        self.assertEqual(again, {'messages': []})


class FakeResponse:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs))
        return FakeResponse(200, {'access_token': 'token-1', 'expires_in': 32400})

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.answers.pop(0)


class PayPalClientTests(SimpleTestCase):
    def test_an_order_has_no_shipping_and_pays_now_and_reuses_the_token(self):
        http = FakeHttp(
            FakeResponse(201, {'id': 'O1', 'status': 'PAYER_ACTION_REQUIRED',
                               'links': [{'rel': 'payer-action', 'href': 'https://pay/O1'}]}),
            FakeResponse(200, {'id': 'O1', 'status': 'APPROVED'}),
        )
        client = PayPalClient('id', 'secret', http=http)
        order = client.create_order(amount=Decimal('0.78'), currency='USD', description='Pass', reference='nbi-1',
                                    custom_id='tf-1', return_url='https://b/r', cancel_url='https://b/c',
                                    brand_name='TapFlash', request_id='tf-1')
        self.assertEqual(order, {'id': 'O1', 'status': 'PAYER_ACTION_REQUIRED', 'approve_url': 'https://pay/O1'})
        method, url, sent = http.calls[1]
        self.assertTrue(url.startswith('https://api-m.sandbox.paypal.com/v2/checkout/orders'))
        body = json.loads(sent['data'])
        context = body['payment_source']['paypal']['experience_context']
        self.assertEqual((context['shipping_preference'], context['user_action']), ('NO_SHIPPING', 'PAY_NOW'))
        self.assertEqual(body['purchase_units'][0]['amount'], {'currency_code': 'USD', 'value': '0.78'})
        self.assertEqual(sent['headers']['PayPal-Request-Id'], 'tf-1')
        client.get_order('O1')
        self.assertEqual(sum(1 for call in http.calls if call[1].endswith('/oauth2/token')), 1)

    def test_capturing_twice_sends_the_same_request_id(self):
        http = FakeHttp(FakeResponse(201, {'status': 'COMPLETED'}), FakeResponse(201, {'status': 'COMPLETED'}))
        client = PayPalClient('id', 'secret', http=http)
        client.capture_order('O1')
        client.capture_order('O1')
        ids = [call[2]['headers']['PayPal-Request-Id'] for call in http.calls if call[0] == 'POST' and 'capture' in call[1]]
        self.assertEqual(ids, ['capture-O1', 'capture-O1'])

    def test_a_refusal_keeps_paypals_reason_code(self):
        http = FakeHttp(FakeResponse(422, {'name': 'UNPROCESSABLE_ENTITY', 'details': [
            {'issue': 'INSTRUMENT_DECLINED', 'description': 'The card was declined.'}]}, {'PayPal-Debug-Id': 'dbg1'}))
        with self.assertRaises(PayPalError) as caught:
            PayPalClient('id', 'secret', http=http).capture_order('O1')
        self.assertEqual((caught.exception.issue, caught.exception.debug_id), ('INSTRUMENT_DECLINED', 'dbg1'))

    def test_a_webhook_is_checked_with_the_event_byte_for_byte(self):
        http = FakeHttp(FakeResponse(200, {'verification_status': 'SUCCESS'}))
        raw = b'{"id":"WH-1","event_type":"CHECKOUT.ORDER.APPROVED","resource":{"id":"O1"}}'
        headers = {'PAYPAL-AUTH-ALGO': 'SHA256withRSA', 'PAYPAL-CERT-URL': 'https://api.paypal.com/cert',
                   'PAYPAL-TRANSMISSION-ID': 't1', 'PAYPAL-TRANSMISSION-SIG': 'sig',
                   'PAYPAL-TRANSMISSION-TIME': '2026-11-06T10:00:00Z'}
        client = PayPalClient('id', 'secret', http=http)
        self.assertTrue(client.verify_webhook(headers=headers, raw_body=raw, webhook_id='WH-ID'))
        sent = http.calls[-1][2]['data']
        self.assertIn('"webhook_event": ' + raw.decode(), sent)
        self.assertEqual(json.loads(sent)['webhook_id'], 'WH-ID')
        self.assertFalse(client.verify_webhook(headers={}, raw_body=raw, webhook_id='WH-ID'))

    def test_amounts_in_kes_become_dollars_and_never_zero(self):
        self.assertEqual(to_paypal_amount(Decimal(101), Decimal(129)), Decimal('0.78'))
        self.assertEqual(to_paypal_amount(Decimal('0.2'), Decimal(129)), Decimal('0.01'))


class PaypalOnlyChatTests(TestCase):
    def test_paypal_is_the_first_choice_and_opens_paypal(self):
        env = {**ENV, 'PAYPAL_ONLY': 'true'}
        fake = FakePayPal()
        t = ChatTester(env, user='web:user-1', checkout=PayPalCheckout(fake, build_config(env)))
        out = texts(reach_paypal_link(t, method='1'))
        self.assertIn('https://www.sandbox.paypal.com/checkoutnow?token=ORDER1', out)
        self.assertNotIn('M-Pesa', out)
