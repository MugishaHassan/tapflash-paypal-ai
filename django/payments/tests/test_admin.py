from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from payments.models import AccessPass

from .test_payments import make_payment


class PaymentAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_superuser('boss', 'boss@example.com', 'test-only-password')
        now = timezone.now()
        cls.paid = make_payment(reference='TFAAAAAAAAAA', fee=Decimal('1.50'), total=Decimal('101.50'))
        cls.current = AccessPass.objects.create(
            payment=cls.paid, user_id='254712345678', otp='111111', expires_at=now + timedelta(minutes=30)
        )
        cls.old = AccessPass.objects.create(
            payment=make_payment(reference='TFBBBBBBBBBB'), user_id='254712345678', otp='222222',
            issued_at=now - timedelta(days=1), expires_at=now - timedelta(hours=23),
        )
        cls.failed = make_payment(reference=None, status='failed', failure_reason='Declined', phone='+254712340000')

    def setUp(self):
        self.client.force_login(self.boss)

    def test_payments_list_shows_money_with_the_currency(self):
        page = self.client.get(reverse('admin:payments_payment_changelist'))
        self.assertContains(page, 'KES 101.50')
        self.assertContains(page, 'TFAAAAAAAAAA')

    def test_payments_can_be_searched_and_filtered(self):
        url = reverse('admin:payments_payment_changelist')
        page = self.client.get(url, {'q': '+254712340000'})
        self.assertEqual(list(page.context['cl'].result_list), [self.failed])
        page = self.client.get(url, {'status__exact': 'succeeded'})
        self.assertEqual(len(page.context['cl'].result_list), 2)

    def test_a_payment_links_to_its_pass(self):
        page = self.client.get(reverse('admin:payments_payment_change', args=[self.paid.pk]))
        self.assertContains(page, reverse('admin:payments_accesspass_change', args=[self.current.pk]))
        self.assertContains(page, 'Access code 111111')

    def test_money_records_are_read_only(self):
        self.assertEqual(self.client.get(reverse('admin:payments_payment_add')).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:payments_accesspass_add')).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:payments_payment_delete', args=[self.paid.pk])).status_code, 403)
        page = self.client.get(reverse('admin:payments_payment_change', args=[self.paid.pk]))
        self.assertNotContains(page, 'name="total"')

    def test_passes_can_be_filtered_by_whether_they_still_work(self):
        url = reverse('admin:payments_accesspass_changelist')
        self.assertEqual(list(self.client.get(url, {'active': 'yes'}).context['cl'].result_list), [self.current])
        self.assertEqual(list(self.client.get(url, {'active': 'no'}).context['cl'].result_list), [self.old])
        self.assertEqual(list(self.client.get(url, {'q': 'TFBBBBBBBBBB'}).context['cl'].result_list), [self.old])
        self.assertEqual(list(self.client.get(url, {'q': '111111'}).context['cl'].result_list), [self.current])
