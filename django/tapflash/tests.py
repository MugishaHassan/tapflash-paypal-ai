import json
import os
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from dotenv import dotenv_values

from bot.models import Conversation, IncomingMessage, OutgoingReply, ProblemReport
from payments.models import AccessPass, Payment, PayPalOrder, Payout, Refund, VisitPass
from restrooms.models import Restroom

OUR_APPS = ('restrooms', 'payments', 'bot')


class AdminSiteTests(TestCase):
    """Every page of the admin opens for a superuser, with data in every table."""

    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_superuser('boss', 'boss@example.com', 'test-only-password')
        market = Restroom.objects.create(
            code='nbi-city-market', name='City Market Restroom', area='Nairobi CBD', latitude=-1.2833,
            longitude=36.8219, price=Decimal(100), opening_hours='6:00 AM – 9:00 PM',
        )
        payment = Payment.objects.create(
            reference='TF0123456789', user_id='254712345678', phone='+254712345678', method='mpesa', provider='mock',
            restroom=market, facility_code=market.code, facility_name=market.name, price=Decimal(100),
            fee=Decimal(0), total=Decimal(100), currency='KES', status='succeeded',
        )
        AccessPass.objects.create(
            payment=payment, user_id='254712345678', otp='123456', expires_at=timezone.now() + timedelta(minutes=30)
        )
        ProblemReport.objects.create(user_id='254712345678', restroom=market, message='The restroom is closed')
        Conversation.objects.create(user_id='254712345678', status='active', step='navigating', state={})
        IncomingMessage.objects.create(wa_message_id='wamid.1', user_id='254712345678', payload={'type': 'text'})
        PayPalOrder.objects.create(
            order_id='5O190127TN364715T', user_id='254712345678', facility_code=market.code,
            facility_name=market.name, local_total=Decimal(101), local_currency='KES', amount=Decimal('0.78'),
            approve_url='https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T', payment=payment,
        )
        Refund.objects.create(payment=payment, amount=Decimal(100), currency='KES', status='completed',
                              reason='The restroom was closed')
        VisitPass.objects.create(user_id='web:2547', visits_total=20, visits_left=19, price=Decimal(12), token='t1', status='paid')
        Payout.objects.create(sender_batch_id='tf-earnings-1', kind='earnings', restroom=market,
                              receiver='operator@example.com', amount=Decimal('5.00'), visits=8)
        OutgoingReply.objects.create(user_id='254712345678', channel='bridge', replies=[])

    def setUp(self):
        self.client.force_login(self.boss)

    def test_every_model_is_in_the_admin(self):
        for label in OUR_APPS:
            for model in apps.get_app_config(label).get_models():
                with self.subTest(model=model.__name__):
                    self.assertTrue(admin.site.is_registered(model))

    def test_no_list_is_grouped_by_date(self):
        # date_hierarchy needs MySQL's time zone tables, which shared hosting
        # often lacks: the lists would fail there. The date filter replaces it.
        for model, model_admin in admin.site._registry.items():
            if model._meta.app_label in OUR_APPS:
                with self.subTest(model=model.__name__):
                    self.assertIsNone(model_admin.date_hierarchy)

    def test_every_list_search_and_object_page_loads(self):
        for label in OUR_APPS:
            for model in apps.get_app_config(label).get_models():
                name = f'admin:{label}_{model._meta.model_name}'
                obj = model.objects.get()
                with self.subTest(model=model.__name__):
                    self.assertEqual(self.client.get(reverse(f'{name}_changelist')).status_code, 200)
                    self.assertEqual(self.client.get(reverse(f'{name}_changelist'), {'q': '2547'}).status_code, 200)
                    self.assertEqual(self.client.get(reverse(f'{name}_change', args=[obj.pk])).status_code, 200)

    def test_the_admin_home_page(self):
        page = self.client.get(reverse('admin:index'))
        self.assertContains(page, f'{settings.TAPFLASH.bot_name} admin')
        self.assertContains(page, 'Chats and problem reports')
        self.assertContains(page, 'Access passes')
        self.assertContains(page, 'Problem reports')
        self.assertTrue(reverse('admin:index').startswith(f'/{settings.ADMIN_PATH}/'))


class UserIdTests(SimpleTestCase):
    def test_web_chat_user_ids_fit_every_table(self):
        # The web chat's ids are "web:" and up to 64 characters from the browser.
        longest = 'web:' + 'x' * 64
        for model in (Conversation, IncomingMessage, Payment, AccessPass, ProblemReport):
            with self.subTest(model=model.__name__):
                model._meta.get_field('user_id').clean(longest, None)


class LoggingTests(SimpleTestCase):
    def test_requests_for_other_host_names_are_refused_without_a_log_line(self):
        with self.assertNoLogs(level='WARNING'):
            response = self.client.get('/', headers={'host': 'probe.example'})
        self.assertEqual(response.status_code, 400)


class SettingsTests(SimpleTestCase):
    """Settings are read when Django starts, so each case starts a new Python."""

    def start_django(self, code='', **env):
        # Set here so the local .env file can't change the outcome.
        defaults = {'DJANGO_SETTINGS_MODULE': 'tapflash.settings', 'DEBUG': 'true', 'DB_ENGINE': 'sqlite'}
        environment = {**os.environ, **defaults, **env}
        script = f'import django\ndjango.setup()\n{code}'
        return subprocess.run(
            [sys.executable, '-c', script], cwd=settings.BASE_DIR, env=environment, capture_output=True, text=True,
            timeout=60,
        )

    def test_secret_key_is_required_when_debug_is_off(self):
        result = self.start_django(DEBUG='false', SECRET_KEY='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SECRET_KEY must be set when DEBUG is off', result.stderr)

    def test_a_copy_of_the_example_env_file_does_not_start_in_debug_mode(self):
        example = {name: value or '' for name, value in dotenv_values(settings.BASE_DIR / '.env.example').items()}
        self.assertEqual(example['DEBUG'], 'false')
        result = self.start_django(**example)
        self.assertIn('SECRET_KEY must be set when DEBUG is off', result.stderr)

    def test_production_settings_start(self):
        result = self.start_django(
            'from django.conf import settings\n'
            'print(settings.DEBUG, settings.ALLOWED_HOSTS, settings.SESSION_COOKIE_SECURE, settings.TIME_ZONE)',
            DEBUG='false', SECRET_KEY='x' * 50, ALLOWED_HOSTS='tapflash.example.com, www.tapflash.example.com',
            TIMEZONE='Africa/Kampala',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(), "False ['tapflash.example.com', 'www.tapflash.example.com'] True Africa/Kampala"
        )

    def test_mysql_uses_pymysql_with_utf8mb4(self):
        result = self.start_django(
            'import json\n'
            'from django.conf import settings\n'
            'from django.db.backends.mysql import base\n'
            'db = settings.DATABASES["default"]\n'
            'print(json.dumps([db["ENGINE"], db["NAME"], db["OPTIONS"]["charset"], db["CONN_MAX_AGE"],'
            ' db["CONN_HEALTH_CHECKS"], base.Database.__name__]))',
            DB_ENGINE='mysql', DB_NAME='tapflash', DB_USER='tapflash', DB_PASSWORD='secret',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            ['django.db.backends.mysql', 'tapflash', 'utf8mb4', 60, True, 'pymysql'],
        )

    def test_an_unknown_database_engine_is_rejected(self):
        result = self.start_django(DB_ENGINE='postgres')
        self.assertIn('DB_ENGINE must be sqlite or mysql, got "postgres"', result.stderr)

    def test_a_bad_bot_setting_stops_the_start(self):
        result = self.start_django(TIMEZONE='Nairobi')
        self.assertIn('TIMEZONE "Nairobi" is not a valid time zone', result.stderr)
