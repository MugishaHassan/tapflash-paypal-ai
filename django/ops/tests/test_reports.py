"""Income reports: built from PayPal payments, checked with PayPal Transaction Search, summarised by Gemini."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from ops.reports import build
from payments.models import Payment
from restrooms.models import Restroom


class FakeSearch:
    def search_transactions(self, *, start, end, page=1):
        return {'transaction_details': [{'transaction_info': {'transaction_amount': {'value': '0.78', 'currency_code': 'USD'}}},
                                        {'transaction_info': {'transaction_amount': {'value': '-1.00', 'currency_code': 'USD'}}}]}


class FakeGemini:
    def decide(self, *, system, prompt, functions, images=()):
        assert functions[0]['name'] == 'write_summary' and 'busiestDay' in prompt
        return 'write_summary', {'summary': 'Fridays at 5 pm are busiest. Keep a cleaner on shift then.'}


class ReportTests(TestCase):
    def setUp(self):
        self.market = Restroom.objects.create(code='city-market', name='City Market', latitude=-1.28, longitude=36.82,
                                              operator_name='Traders Sacco')
        now = timezone.now()
        for i in range(3):
            Payment.objects.create(reference=f'TF{i}', user_id=f'web:{i}', method='mpesa', provider='mock', restroom=self.market,
                                   facility_code='city-market', facility_name='City Market', price=Decimal(100), fee=Decimal(0),
                                   total=Decimal(100), currency='KES', status='succeeded', created_at=now - timedelta(days=i))

    def test_the_report_counts_visits_checks_paypal_and_asks_gemini(self):
        report = build(self.market, settings.TAPFLASH, checkout=SimpleNamespace(client=FakeSearch()), ai=FakeGemini())
        self.assertEqual(report['visits'], 3)
        self.assertEqual(len(report['days']), 14)
        self.assertEqual(report['paypal'], {'ok': True, 'count': 2, 'in': '0.78', 'source': 'PayPal Transaction Search, last 31 days'})
        self.assertEqual(report['summary']['by'], 'Gemini')

    def test_without_ai_or_paypal_it_still_works_and_the_statement_renders(self):
        report = build(self.market, settings.TAPFLASH)
        self.assertEqual((report['summary']['by'], report['paypal']['ok']), ('TapFlash', False))
        self.client.force_login(User.objects.create_user('boss', password='pw-for-tests', is_staff=True))
        self.assertContains(self.client.get('/ops/statement/city-market'), 'Income statement')
        self.assertEqual(self.client.get('/ops/report.json?restroom=city-market').json()['visits'], 3)
