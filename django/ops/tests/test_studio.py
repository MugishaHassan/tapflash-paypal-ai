from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class StudioPageTests(TestCase):
    def test_visitors_are_sent_to_the_login(self):
        response = self.client.get(reverse('ops-studio'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('gate-login'), response['Location'])

    def test_staff_get_the_page_with_its_bundle_and_data_url(self):
        user = get_user_model().objects.create_user('operator', password='x', is_staff=True)
        self.client.force_login(user)
        response = self.client.get(reverse('ops-studio'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ops/studio/studio.js')
        self.assertContains(response, f'data-url="{reverse("ops-data")}"')

    def test_scripts_only_come_from_this_site(self):
        user = get_user_model().objects.create_user('operator', password='x', is_staff=True)
        self.client.force_login(user)
        policy = self.client.get(reverse('ops-studio'))['Content-Security-Policy']
        self.assertIn("script-src 'self'", policy)
        self.assertNotIn('unsafe-eval', policy)
        self.assertNotIn('http', policy)

    def test_the_dashboard_links_to_it(self):
        user = get_user_model().objects.create_user('operator', password='x', is_staff=True)
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse('ops-dashboard')), reverse('ops-studio'))
