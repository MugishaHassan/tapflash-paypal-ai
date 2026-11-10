from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bot.models import Conversation, IncomingMessage, ProblemReport
from restrooms.models import Restroom


class ProblemReportTests(TestCase):
    def test_resolved_at_follows_the_status(self):
        report = ProblemReport.objects.create(user_id='254712345678', message='The restroom is closed')
        self.assertIsNone(report.resolved_at)
        report.status = ProblemReport.Status.RESOLVED
        report.save()
        resolved_at = report.resolved_at
        self.assertIsNotNone(resolved_at)
        report.save()
        self.assertEqual(report.resolved_at, resolved_at, 'saving again keeps the time')
        report.status = ProblemReport.Status.LOOKING
        report.save()
        self.assertIsNone(report.resolved_at)


class BotAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_superuser('boss', 'boss@example.com', 'test-only-password')
        market = Restroom.objects.create(code='city-market', name='City Market', latitude=-1.28, longitude=36.82)
        cls.report = ProblemReport.objects.create(
            user_id='254712345678', phone='+254712345678', restroom=market, facility_code='city-market',
            facility_name='City Market', step='navigating', message="I can't find the entrance",
        )
        cls.other = ProblemReport.objects.create(user_id='254700000001', message='The restroom is closed')
        cls.chat = Conversation.objects.create(
            user_id='254712345678', status=Conversation.Status.ACTIVE, step='navigating',
            last_seen=timezone.now(), state={'step': 'navigating', 'restroom': {'name': 'City Market'}},
        )
        cls.incoming = IncomingMessage.objects.create(
            wa_message_id='wamid.ABC', user_id='254712345678', payload={'type': 'text', 'text': {'body': 'hi'}}
        )

    def setUp(self):
        self.client.force_login(self.boss)

    def test_staff_can_change_only_the_status_and_notes_of_a_report(self):
        url = reverse('admin:bot_problemreport_change', args=[self.report.pk])
        response = self.client.post(url, {
            'status': 'resolved', 'staff_notes': 'Sign added at the gate', 'message': 'changed', 'phone': '0',
        })
        self.assertEqual(response.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'resolved')
        self.assertEqual(self.report.staff_notes, 'Sign added at the gate')
        self.assertIsNotNone(self.report.resolved_at)
        self.assertEqual(self.report.message, "I can't find the entrance")
        self.assertEqual(self.report.phone, '+254712345678')

    def test_report_actions(self):
        url = reverse('admin:bot_problemreport_changelist')
        both = [self.report.pk, self.other.pk]
        response = self.client.post(url, {'action': 'mark_looking', '_selected_action': both}, follow=True)
        self.assertContains(response, '2 problem reports marked as being looked into.')

        response = self.client.post(url, {'action': 'mark_resolved', '_selected_action': [self.report.pk]}, follow=True)
        self.assertContains(response, '1 problem report marked as resolved.')
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'resolved')
        self.assertIsNotNone(self.report.resolved_at)

        response = self.client.post(url, {'action': 'mark_resolved', '_selected_action': both}, follow=True)
        self.assertContains(response, '1 problem report marked as resolved.', msg_prefix='already resolved ones are left alone')

    def test_reports_cannot_be_added_by_staff(self):
        self.assertEqual(self.client.get(reverse('admin:bot_problemreport_add')).status_code, 403)

    def test_chats_and_messages_are_read_only(self):
        for name in ['conversation', 'incomingmessage']:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f'admin:bot_{name}_add')).status_code, 403)

        page = self.client.get(reverse('admin:bot_conversation_change', args=[self.chat.pk]))
        self.assertContains(page, '&quot;restroom&quot;: {')
        self.assertNotContains(page, 'name="step"')

        page = self.client.get(reverse('admin:bot_incomingmessage_change', args=[self.incoming.pk]))
        self.assertContains(page, '&quot;body&quot;: &quot;hi&quot;')

    def test_chats_and_messages_can_be_searched(self):
        page = self.client.get(reverse('admin:bot_conversation_changelist'), {'q': '254712345678'})
        self.assertEqual(list(page.context['cl'].result_list), [self.chat])
        page = self.client.get(reverse('admin:bot_incomingmessage_changelist'), {'q': 'wamid.ABC'})
        self.assertEqual(list(page.context['cl'].result_list), [self.incoming])
