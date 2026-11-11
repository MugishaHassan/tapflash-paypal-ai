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


import dataclasses
import json
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings

from ops import studio_views


def with_key(key='test-key'):
    return override_settings(TAPFLASH=dataclasses.replace(settings.TAPFLASH, gemini_api_key=key))


class StudioAssistantTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('operator', password='x', is_staff=True)
        self.url = reverse('ops-studio-assistant')
        self.turn = {'contents': [{'role': 'user', 'parts': [{'text': 'Refunds by restroom'}]}], 'apiKey': 'ignored'}

    def post(self, body=None):
        return self.client.post(self.url, json.dumps(body if body is not None else self.turn), content_type='application/json')

    def test_only_staff_can_ask(self):
        self.assertEqual(self.post().status_code, 302)

    def test_it_says_so_when_no_key_is_set(self):
        self.client.force_login(self.user)
        with with_key(''):
            self.assertEqual(self.post().status_code, 503)

    def test_it_forwards_the_turn_with_the_servers_key_and_returns_the_answer(self):
        self.client.force_login(self.user)
        answer = {'candidates': [{'content': {'parts': [{'text': 'Done.'}]}}], 'usageMetadata': {'totalTokenCount': 9}, 'secret': 'x'}
        reply = SimpleNamespace(status_code=200, json=lambda: answer, text='')
        with with_key('server-key'), mock.patch.object(studio_views.requests, 'post', return_value=reply) as sent:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['candidates'][0]['content']['parts'][0]['text'], 'Done.')
        self.assertNotIn('secret', response.json())
        self.assertEqual(sent.call_args.kwargs['headers']['x-goog-api-key'], 'server-key')
        self.assertEqual(set(sent.call_args.kwargs['json']), {'contents'})      # nothing else is passed on
        self.assertNotIn('server-key', response.content.decode())

    def test_a_request_that_is_not_a_turn_is_refused(self):
        self.client.force_login(self.user)
        with with_key():
            self.assertEqual(self.post({'prompt': 'hi'}).status_code, 400)

    def test_a_busy_model_gives_a_plain_message(self):
        self.client.force_login(self.user)
        reply = SimpleNamespace(status_code=429, json=lambda: {}, text='quota')
        with with_key(), mock.patch.object(studio_views.requests, 'post', return_value=reply):
            response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertIn('busy', response.json()['error'])

    def test_each_user_has_an_hourly_limit(self):
        self.client.force_login(self.user)
        reply = SimpleNamespace(status_code=200, json=lambda: {'candidates': []}, text='')
        with with_key(), mock.patch.object(studio_views, 'AI_TURNS_PER_HOUR', 2), \
                mock.patch.object(studio_views.requests, 'post', return_value=reply):
            self.assertEqual([self.post().status_code for _ in range(3)], [200, 200, 429])

    def test_the_page_offers_the_assistant_only_when_a_key_is_set(self):
        self.client.force_login(self.user)
        with with_key():
            self.assertContains(self.client.get(reverse('ops-studio')), 'data-assistant=')
        with with_key(''):
            self.assertNotContains(self.client.get(reverse('ops-studio')), 'data-assistant=')
