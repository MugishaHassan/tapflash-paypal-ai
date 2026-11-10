import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from bot.models import Conversation, OutgoingReply, ProblemReport
from payments.models import AccessPass, Payment, Refund


class StaffRefundTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('ops', password='pw-for-tests', is_staff=True)
        self.client.force_login(self.staff)
        now = timezone.now()
        self.payment = Payment.objects.create(
            reference='TF0000000001', user_id='web:u1', phone='+254700000001', method='mpesa', provider='mock',
            facility_code='city-market', facility_name='City Market', price=Decimal(100), fee=Decimal(0),
            total=Decimal(100), currency='KES', status='succeeded', created_at=now - timedelta(days=2),
        )
        AccessPass.objects.create(payment=self.payment, user_id='web:u1', otp='123456',
                                  expires_at=now + timedelta(minutes=30))
        self.report = ProblemReport.objects.create(user_id='web:u1', facility_code='city-market',
                                                   facility_name='City Market', message='Locked',
                                                   payment=self.payment)

    def refund(self, share='full', report=None):
        return self.client.post('/ops/refund', json.dumps({'report': report or self.report.pk, 'share': share}),
                                content_type='application/json')

    def test_staff_refund_outside_the_ai_window_and_the_user_is_told(self):
        response = self.refund()
        self.assertEqual(response.status_code, 200, response.content)
        refund = Refund.objects.get()
        self.assertEqual((refund.amount, refund.decided_by), (Decimal(100), Refund.DecidedBy.STAFF))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.REFUNDED)
        self.assertFalse(AccessPass.objects.active().exists())
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ProblemReport.Status.RESOLVED)
        told = OutgoingReply.objects.get(user_id='web:u1')
        self.assertIn('Refunded', json.dumps(told.replies))

    def test_a_second_refund_is_refused(self):
        self.refund(share='partial')
        response = self.refund()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Refund.objects.count(), 1)

    def test_the_dashboard_offers_the_refund_only_while_it_is_due(self):
        row = self.client.get('/ops/data').json()['reports'][0]
        self.assertTrue(row['refundable'])
        self.refund()
        row = self.client.get('/ops/data').json()['reports'][0]
        self.assertFalse(row['refundable'])
        self.assertEqual(row['refundBy'], 'Staff')

    def test_staff_only(self):
        self.client.logout()
        self.assertEqual(self.refund().status_code, 302)
        self.assertFalse(Refund.objects.exists())

    def test_a_whatsapp_bridge_user_is_told_on_whatsapp(self):
        Conversation.objects.create(user_id='web:u1', state={'channel': 'bridge'})
        self.refund()
        self.assertEqual(OutgoingReply.objects.get(user_id='web:u1').channel, 'bridge')


class EditRestroomTests(TestCase):
    def setUp(self):
        from restrooms.models import Restroom
        self.restroom = Restroom.objects.create(code='city-market', name='City Market', latitude=-1.28,
                                                longitude=36.82)
        self.client.force_login(User.objects.create_user('ops', password='pw-for-tests', is_staff=True))

    def edit(self, field, value):
        return self.client.post('/ops/restroom', json.dumps({'restroom': 'city-market', 'field': field, 'value': value}),
                                content_type='application/json')

    def test_email_and_share_can_be_changed_from_the_grid(self):
        self.assertEqual(self.edit('email', 'op@example.com').status_code, 200)
        self.assertEqual(self.edit('share', 70).status_code, 200)
        self.restroom.refresh_from_db()
        self.assertEqual((self.restroom.payout_email, self.restroom.operator_share_percent), ('op@example.com', 70))

    def test_bad_values_are_refused(self):
        self.assertEqual(self.edit('email', 'not an email').status_code, 400)
        self.assertEqual(self.edit('share', 101).status_code, 400)
        self.assertEqual(self.edit('share', 'x').status_code, 400)
        self.assertEqual(self.edit('price', 1).status_code, 400)
        self.restroom.refresh_from_db()
        self.assertEqual(self.restroom.operator_share_percent, 80)


class JudgeLoginTests(TestCase):
    def test_the_judge_login_opens_the_dashboard_but_not_the_admin_data(self):
        from io import StringIO

        from django.core.management import call_command
        out = StringIO()
        call_command('judge_login', stdout=out)
        password = out.getvalue().split('Password: ')[1].split()[0]
        self.assertTrue(self.client.login(username='judge', password=password))
        self.assertEqual(self.client.get('/ops/').status_code, 200)
        self.assertEqual(self.client.get('/admin/payments/payment/').status_code, 403)


class PeopleTests(TestCase):
    def setUp(self):
        from restrooms.models import Restroom
        Restroom.objects.create(code='city-market', name='City Market', latitude=-1.28, longitude=36.82)
        self.client.force_login(User.objects.create_user('ops', password='pw-for-tests', is_staff=True))

    def post(self, url, body):
        return self.client.post(url, json.dumps(body), content_type='application/json')

    def test_add_change_and_switch_off_a_cleaner(self):
        from ops.models import Cleaner
        response = self.post('/ops/cleaner', {'name': 'Achieng', 'phone': '0712 345 678',
                                              'email': 'achieng@example.com', 'restroom': 'city-market', 'bonus': '1.5'})
        self.assertEqual(response.status_code, 200, response.content)
        cleaner = Cleaner.objects.get()
        self.assertEqual((cleaner.phone, cleaner.bonus), ('+254712345678', Decimal('1.50')))
        self.assertEqual(self.post('/ops/cleaner', {'id': cleaner.pk, 'bonus': 2}).status_code, 200)
        self.assertEqual(self.post('/ops/cleaner', {'id': cleaner.pk, 'active': False}).status_code, 200)
        cleaner.refresh_from_db()
        self.assertEqual((cleaner.bonus, cleaner.is_active), (Decimal('2.00'), False))
        self.assertEqual(self.client.get('/ops/data').json()['cleaners'][0]['name'], 'Achieng')

    def test_bad_cleaners_are_refused(self):
        base = {'name': 'A', 'phone': '+254712345678', 'email': 'a@example.com', 'restroom': 'city-market'}
        for change in ({'email': 'nope'}, {'phone': '12'}, {'restroom': 'nowhere'}, {'bonus': '500'}, {'name': ''}):
            with self.subTest(change):
                self.assertEqual(self.post('/ops/cleaner', {**base, **change}).status_code, 400)

    def test_add_an_attendant_who_can_then_log_in_at_the_gate(self):
        response = self.post('/ops/attendant', {'username': 'Amina', 'restroom': 'city-market'})
        self.assertEqual(response.status_code, 200, response.content)
        password = response.json()['password']
        self.client.logout()
        self.assertTrue(self.client.login(username='amina', password=password))
        self.assertContains(self.client.get('/gate/'), 'City Market')
        self.assertEqual(self.client.get('/ops/').status_code, 302)  # not staff: no dashboard

    def test_remove_an_attendant(self):
        from ops.models import Attendant
        self.post('/ops/attendant', {'username': 'amina', 'restroom': 'city-market'})
        attendant = Attendant.objects.get()
        self.assertEqual(self.post('/ops/attendant', {'action': 'remove', 'id': attendant.pk}).status_code, 200)
        self.assertFalse(User.objects.get(username='amina').is_active)
        self.assertEqual(self.post('/ops/attendant', {'username': 'amina', 'restroom': 'city-market'}).status_code, 409)
