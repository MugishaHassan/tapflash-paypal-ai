"""The door check: the attendant scans the visitor's QR code or types the code."""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from bot.models import Conversation, OutgoingReply
from bot.config import build_config
from ops.models import Attendant
from payments.models import AccessPass, Payment, Refund
from payments.refunds import refund_visit
from restrooms.models import Restroom


class GateTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.market = Restroom.objects.create(code='city-market', name='City Market', latitude=-1.28, longitude=36.82)
        self.other = Restroom.objects.create(code='railways', name='Railways', latitude=-1.29, longitude=36.83)
        self.user = User.objects.create_user('amina', password='pw-for-tests')
        Attendant.objects.create(user=self.user, restroom=self.market)
        self.client.force_login(self.user)
        self.count = 0

    def paid_pass(self, restroom=None, otp='123456', expires_in=30, status='succeeded'):
        restroom = restroom or self.market
        self.count += 1
        payment = Payment.objects.create(
            reference=f'TF{self.count:010d}', user_id=f'web:v{self.count}', method='paypal', provider='paypal',
            restroom=restroom, facility_code=restroom.code, facility_name=restroom.name, price=Decimal(100),
            fee=Decimal(1), total=Decimal(101), currency='KES', status=status, created_at=self.now - timedelta(minutes=5),
        )
        return AccessPass.objects.create(payment=payment, user_id=payment.user_id, otp=otp,
                                         expires_at=self.now + timedelta(minutes=expires_in))

    def qr(self, access_pass):
        p = access_pass
        return f'TAPFLASH-ACCESS|{p.payment.reference}|{p.payment.facility_code}|{p.otp}|2026-11-07T10:00:00Z'

    def check(self, **payload):
        return self.client.post('/gate/check', json.dumps(payload), content_type='application/json').json()

    def test_a_valid_qr_lets_them_in_once_and_tells_the_visitor(self):
        access_pass = self.paid_pass()
        Conversation.objects.create(user_id=access_pass.user_id, state={'channel': 'bridge'})
        first = self.check(qr=self.qr(access_pass))
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['title'], 'Let them in')
        self.assertIn('KES 101', first['detail'])
        access_pass.refresh_from_db()
        self.assertEqual(access_pass.checked_in_by, 'amina')
        told = OutgoingReply.objects.get(user_id=access_pass.user_id)
        self.assertEqual(told.channel, 'bridge')
        self.assertIn('checked in at *City Market*', json.dumps(told.replies))

        again = self.check(qr=self.qr(access_pass))
        self.assertEqual((again['ok'], again['title']), (False, 'Already used'))

    def test_a_simulated_payment_does_not_open_the_door_once_real_payments_are_on(self):
        from ops.gate import check_in
        access_pass = self.paid_pass(otp='777777')
        Payment.objects.filter(pk=access_pass.payment_id).update(provider='mock')
        refused = check_in(self.market, code='777777', by='amina', accept_simulated=False)
        self.assertEqual((refused.ok, refused.title), (False, 'Test payment'))
        allowed = check_in(self.market, code='777777', by='amina', accept_simulated=True)
        self.assertTrue(allowed.ok)
        self.assertIn('(simulated)', allowed.detail)

    def test_the_typed_code_works_too(self):
        self.paid_pass(otp='654321')
        self.assertTrue(self.check(code='654 321')['ok'])

    def test_refused_passes(self):
        cases = [
            (self.paid_pass(restroom=self.other, otp='111111'), 'Wrong restroom'),
            (self.paid_pass(otp='222222', expires_in=-1), 'Expired'),
            (self.paid_pass(otp='333333', status='refunded'), 'Refunded'),
        ]
        for access_pass, title in cases:
            with self.subTest(title):
                result = self.check(qr=self.qr(access_pass))
                self.assertEqual((result['ok'], result['title']), (False, title))
        self.assertEqual(self.check(qr='hello')['title'], 'Not a TapFlash pass')
        self.assertEqual(self.check(code='999999')['title'], 'Not a TapFlash pass')
        self.assertFalse(AccessPass.objects.filter(checked_in_at__isnull=False).exists())

    def test_only_attendants_and_staff_can_check(self):
        self.client.logout()
        self.assertEqual(self.client.get('/gate/').status_code, 302)
        User.objects.create_user('visitor', password='pw-for-tests')
        self.client.login(username='visitor', password='pw-for-tests')
        self.assertEqual(self.client.post('/gate/check', '{"code": "123456"}', content_type='application/json')
                         .status_code, 403)

    def test_staff_choose_the_restroom(self):
        staff = User.objects.create_user('ops', password='pw-for-tests', is_staff=True)
        self.client.force_login(staff)
        access_pass = self.paid_pass(restroom=self.other, otp='444444')
        page = self.client.get('/gate/?restroom=railways')
        self.assertContains(page, 'Railways')
        self.assertTrue(self.check(code='444444')['ok'])

    def test_the_login_page_and_dashboard_show_entrances(self):
        self.client.logout()
        self.assertContains(self.client.get('/gate/login/'), 'access codes')
        # The dashboard sends people to TapFlash's own login page, not Django's admin one.
        self.assertEqual(self.client.get('/ops/')['Location'], '/gate/login/?next=/ops/')
        access_pass = self.paid_pass()
        self.client.force_login(self.user)
        self.check(qr=self.qr(access_pass))
        self.client.force_login(User.objects.create_user('boss', password='pw-for-tests', is_staff=True))
        data = self.client.get('/ops/data').json()
        self.assertTrue(data['payments'][0]['entered'])
        self.assertEqual(data['summary']['entered'], 1)

    def test_no_automatic_full_refund_after_the_visitor_was_let_in(self):
        access_pass = self.paid_pass()
        self.check(qr=self.qr(access_pass))
        config = build_config({})
        decision = {'refund': 'full', 'category': 'closed', 'reply': 'Sorry', 'staff_note': ''}
        refund, why = refund_visit(payment=access_pass.payment, report=None, decision=decision, checkout=None,
                                   config=config, now=self.now)
        self.assertIsNone(refund)
        self.assertIn('checked in at the door', why)
        partial = dict(decision, refund='partial')
        refund, _ = refund_visit(payment=access_pass.payment, report=None, decision=partial, checkout=None,
                                 config=config, now=self.now)
        self.assertEqual(refund.amount, Decimal('50.50'))
        self.assertEqual(Refund.objects.count(), 1)


class LimitTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_the_gate_login_locks_after_eight_wrong_passwords(self):
        User.objects.create_user('amina', password='right-password-1')
        for _ in range(8):
            self.client.post('/gate/login/', {'username': 'amina', 'password': 'wrong'})
        locked = self.client.post('/gate/login/', {'username': 'amina', 'password': 'right-password-1'})
        self.assertContains(locked, 'Too many wrong passwords')

    def test_the_web_chat_slows_down_a_flood(self):
        from dataclasses import replace

        from django.conf import settings
        with self.settings(CHAT_MESSAGES_PER_MINUTE=3, TAPFLASH=replace(settings.TAPFLASH, web_chat=True)):
            codes = [self.client.post('/api/message', json.dumps({'userId': 'u', 'text': 'hi'}),
                                      content_type='application/json').status_code for _ in range(5)]
        self.assertEqual(codes[-1], 429)
        self.assertEqual(codes.count(429), 2)


class JudgePageAndAnyRestroomTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.market = Restroom.objects.create(code='city-market', name='City Market', latitude=-1.28, longitude=36.82)

    def paid_pass(self, code, name, otp):
        payment = Payment.objects.create(
            reference=f'TF{otp}', user_id=f'web:{otp}', method='paypal', provider='paypal', facility_code=code,
            facility_name=name, price=Decimal(100), fee=Decimal(1), total=Decimal(101), currency='KES',
            status='succeeded', created_at=self.now)
        return AccessPass.objects.create(payment=payment, user_id=payment.user_id, otp=otp,
                                         expires_at=self.now + timedelta(minutes=30))

    def test_judges_get_in_with_one_click_and_check_in_at_any_restroom(self):
        with self.settings(JUDGE_ONE_CLICK=True, DEMO_BUYER_EMAIL='buyer@personal.example.com'):
            page = self.client.get('/judges/')
            self.assertContains(page, 'buyer@personal.example.com')
            self.assertEqual(self.client.post('/judges/enter/gate')['Location'], '/gate/')
        self.assertContains(self.client.get('/gate/'), 'Any restroom')
        # A pass for a demo restroom that is not in the database still checks in.
        self.paid_pass('demo-city-mall', 'City Mall Restroom (demo)', '135790')
        result = self.client.post('/gate/check', '{"code": "135790"}', content_type='application/json').json()
        self.assertTrue(result['ok'], result)
        self.assertIn('City Mall Restroom (demo)', result['detail'])
        self.assertEqual(self.client.get('/ops/').status_code, 200)
        self.assertEqual(self.client.get('/admin/payments/payment/').status_code, 403)

    def test_one_click_is_off_unless_switched_on(self):
        self.assertContains(self.client.get('/judges/'), 'Devpost testing instructions')
        self.assertEqual(self.client.post('/judges/enter/ops').status_code, 404)


class PaypalOnlyTests(TestCase):
    def test_paypal_only_offers_paypal_first_and_no_mobile_money(self):
        from dataclasses import replace

        from django.conf import settings

        from payments.methods import payment_methods
        methods = payment_methods(replace(settings.TAPFLASH, paypal_only=True))
        self.assertEqual([m.id for m in methods], ['paypal', 'card'])


class JudgeMoneyLimitTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_the_shared_judge_login_has_a_daily_cap_on_money_actions(self):
        with self.settings(JUDGE_ONE_CLICK=True, JUDGE_MONEY_ACTIONS_PER_DAY=2):
            self.client.post('/judges/enter/ops')
            codes = [self.client.post('/ops/payout', '{"restroom": "nowhere"}', content_type='application/json').status_code
                     for _ in range(3)]
        self.assertEqual(codes, [404, 404, 429])
