import hashlib
import hmac
import json
import math
import time
from dataclasses import replace
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from bot.config import build_config
from bot.engine import Engine
from bot.models import IncomingMessage
from bot.replies import Image, Menu, Option, Text
from channels.checks import whatsapp_settings
from channels.cloud_api import GraphClient, interactive_for, to_incoming, valid_signature

META = replace(
    settings.TAPFLASH, whatsapp_token='t', whatsapp_phone_number_id='1', whatsapp_verify_token='v',
    whatsapp_app_secret='s', webhook_processing='inline', images=False,
)
PLAIN_ENGINE = Engine(build_config({}))


def sign(body, secret='s'):
    return 'sha256=' + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def delivery(*messages):
    return json.dumps({'entry': [{'changes': [{'value': {'messages': list(messages)}}]}]})


def text_message(message_id, body='hi', sender='254712345678', age=0):
    return {'id': message_id, 'from': sender, 'timestamp': str(int(time.time() - age)), 'type': 'text',
            'text': {'body': body}}


class MenusTests(TestCase):
    def test_three_short_choices_become_reply_buttons(self):
        menu = Menu('Ready?', [Option('1', 'Pay'), Option('2', 'Exit')])
        interactive = interactive_for(menu)
        self.assertEqual(interactive['type'], 'button')
        self.assertEqual(interactive['action']['buttons'][0], {'type': 'reply', 'reply': {'id': '1', 'title': 'Pay'}})

    def test_more_or_longer_choices_become_a_list_keeping_long_wording(self):
        menu = Menu('Pick one', [Option('1', 'Pay'), Option('2', 'Exit'), Option('3', 'Choose a different restroom')])
        interactive = interactive_for(menu)
        self.assertEqual(interactive['type'], 'list')
        row = interactive['action']['sections'][0]['rows'][2]
        self.assertLessEqual(len(row['title']), 24)
        self.assertTrue(row['title'].endswith('…'))
        self.assertEqual(row['description'], 'Choose a different restroom')
        self.assertNotIn('description', interactive['action']['sections'][0]['rows'][0])

    def test_more_than_ten_choices_fall_back_to_text(self):
        options = [Option(str(n), f'Option {n}') for n in range(11)]
        self.assertIsNone(interactive_for(Menu('x', options)))


class IncomingTests(TestCase):
    def test_meta_messages_become_incoming_messages(self):
        incoming = to_incoming({'from': '254712345678', 'type': 'text', 'text': {'body': 'hi'}})
        self.assertEqual((incoming.user_id, incoming.phone, incoming.type, incoming.text),
                         ('254712345678', '+254712345678', 'text', 'hi'))
        tap = to_incoming({'from': '254712345678', 'type': 'interactive',
                           'interactive': {'type': 'list_reply', 'list_reply': {'id': '3', 'title': 'x'}}})
        self.assertEqual(tap.text, '3')
        button = to_incoming({'from': '254712345678', 'type': 'interactive',
                              'interactive': {'button_reply': {'id': '1'}}})
        self.assertEqual(button.text, '1')
        location = to_incoming({'from': '254712345678', 'type': 'location',
                                'location': {'latitude': -1.28, 'longitude': 36.82}})
        self.assertEqual((location.location.lat, location.location.lng), (-1.28, 36.82))
        bad = to_incoming({'from': '254712345678', 'type': 'location', 'location': {'latitude': None}})
        self.assertTrue(math.isnan(bad.location.lat))
        self.assertEqual(to_incoming({'from': '254712345678', 'type': 'sticker'}).type, 'other')
        self.assertIsNone(to_incoming({'from': 'bad', 'type': 'text'}))

    def test_signatures_are_checked_against_the_app_secret(self):
        body = b'{"a":1}'
        signature = 'sha256=' + hmac.new(b's3cret', body, hashlib.sha256).hexdigest()
        self.assertTrue(valid_signature(body, signature, 's3cret'))
        self.assertFalse(valid_signature(body, signature, 'other'))
        self.assertFalse(valid_signature(body, None, 's3cret'))


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self.reason = 'Unauthorized'
        self.data = data if data is not None else {'id': 'media-1'}

    def json(self):
        return self.data


class GraphClientTests(TestCase):
    def setUp(self):
        self.http = mock.Mock()
        self.http.post.return_value = FakeResponse()
        self.client_ = GraphClient(META, session=self.http)

    def payloads(self):
        return [c.kwargs.get('json') for c in self.http.post.call_args_list]

    def test_text_menu_and_picture(self):
        picture = Image(b'png', 'image/png', 'route-map.png')
        self.client_.send('2547', Text('hello'))
        self.client_.send('2547', Menu('Ready?', [Option('1', 'Pay')], image=picture))
        self.client_.send('2547', picture.with_caption('x' * 2000))
        calls = self.http.post.call_args_list
        self.assertEqual(calls[0].args[0], 'https://graph.facebook.com/v23.0/1/messages')
        self.assertEqual(calls[0].kwargs['headers'], {'Authorization': 'Bearer t'})
        self.assertEqual(self.payloads()[0]['text'], {'body': 'hello', 'preview_url': True})
        # The menu's picture is uploaded and sent first, then the buttons.
        self.assertTrue(calls[1].args[0].endswith('/media'))
        self.assertEqual(calls[1].kwargs['files']['file'], ('route-map.png', b'png', 'image/png'))
        self.assertEqual(self.payloads()[2]['image'], {'id': 'media-1'})
        self.assertEqual(self.payloads()[3]['interactive']['type'], 'button')
        self.assertEqual(len(self.payloads()[5]['image']['caption']), 1024)

    def test_too_many_choices_are_sent_as_text(self):
        self.client_.send('2547', Menu('Pick', [Option(str(n), f'Option {n}') for n in range(11)]))
        self.assertIn('*10.* Option 10', self.payloads()[0]['text']['body'])

    def test_meta_errors_are_reported(self):
        self.http.post.return_value = FakeResponse(401, {'error': {'message': 'Bad token'}})
        with self.assertRaisesMessage(RuntimeError, 'Meta API 401: Bad token'):
            self.client_.send('2547', Text('hello'))


@override_settings(TAPFLASH=META)
@mock.patch('bot.handler.default_engine', lambda: PLAIN_ENGINE)
class WebhookTests(TestCase):
    def post(self, body, signature=None):
        return self.client.post('/webhook', body, content_type='application/json',
                                headers={'X-Hub-Signature-256': signature or sign(body)})

    def test_verification(self):
        ok = self.client.get('/webhook', {'hub.mode': 'subscribe', 'hub.verify_token': 'v', 'hub.challenge': '42'})
        self.assertEqual((ok.status_code, ok.content), (200, b'42'))
        wrong = self.client.get('/webhook', {'hub.mode': 'subscribe', 'hub.verify_token': 'x', 'hub.challenge': '42'})
        self.assertEqual(wrong.status_code, 403)

    @mock.patch('channels.views.GraphClient')
    def test_delivery_is_answered_once_and_retries_are_dropped(self, graph):
        sent = graph.return_value.send
        body = delivery(text_message('m1'))
        self.assertEqual(self.post(body).status_code, 200)
        self.assertEqual(self.post(body).status_code, 200)  # Meta retry
        self.assertEqual(self.post(body, 'sha256=bad').status_code, 401)
        self.assertEqual(IncomingMessage.objects.get().status, 'done')
        replies = [c.args for c in sent.call_args_list]
        self.assertTrue(replies)
        self.assertTrue(all(to == '254712345678' for to, _ in replies))
        self.assertIn('TapFlash', replies[0][1].text)
        greeting_count = len(replies)
        self.post(delivery(text_message('m2', '1')))
        self.assertGreater(len(sent.call_args_list), greeting_count)

    @mock.patch('channels.views.GraphClient')
    def test_old_bad_and_unsigned_messages_are_ignored(self, graph):
        self.post(delivery(text_message('old', age=6 * 60), text_message('bad', sender='x')))
        self.assertEqual(self.post('not json').status_code, 200)
        self.assertEqual(self.client.post('/webhook', delivery(text_message('m3')), content_type='application/json')
                         .status_code, 401)
        self.assertFalse(IncomingMessage.objects.exists())
        graph.return_value.send.assert_not_called()

    @mock.patch('channels.views.GraphClient')
    def test_a_failed_send_is_logged_and_the_message_is_still_done(self, graph):
        graph.return_value.send.side_effect = RuntimeError('Meta API 500: down')
        with self.assertLogs('bot.handler', 'ERROR'):
            self.post(delivery(text_message('m4')))
        self.assertEqual(IncomingMessage.objects.get().status, 'done')

    def test_thread_mode_answers_meta_first(self):
        with override_settings(TAPFLASH=replace(META, webhook_processing='thread')), \
                mock.patch('channels.views.threading.Thread') as thread:
            self.assertEqual(self.post(delivery(text_message('m5'))).status_code, 200)
        thread.return_value.start.assert_called_once()
        self.assertEqual(IncomingMessage.objects.get().status, 'pending')


@override_settings(TAPFLASH=META)
@mock.patch('bot.handler.default_engine', lambda: PLAIN_ENGINE)
class DrainInboxTests(TestCase):
    @mock.patch('channels.management.commands.drain_inbox.GraphClient')
    def test_pending_rows_are_answered_in_order_and_old_ones_given_up(self, graph):
        now = timezone.now()
        IncomingMessage.objects.create(wa_message_id='a', user_id='254700000001',
                                       payload=text_message('a', sender='254700000001'), received_at=now)
        IncomingMessage.objects.create(wa_message_id='b', user_id='254700000001',
                                       payload=text_message('b', 'exit', sender='254700000001'), received_at=now)
        IncomingMessage.objects.create(wa_message_id='c', user_id='254700000002',
                                       payload=text_message('c', sender='254700000002'),
                                       received_at=now - timedelta(minutes=11))
        call_command('drain_inbox', stdout=StringIO())
        rows = {row.wa_message_id: row for row in IncomingMessage.objects.all()}
        self.assertEqual([rows['a'].status, rows['b'].status], ['done', 'done'])
        self.assertEqual((rows['c'].status, rows['c'].error), ('failed', 'Too old to answer'))
        users = [c.args[0] for c in graph.return_value.send.call_args_list]
        self.assertEqual(set(users), {'254700000001'})


class CheckTests(TestCase):
    def test_missing_app_secret_is_warned_about(self):
        with override_settings(TAPFLASH=replace(META, whatsapp_app_secret='')):
            self.assertEqual([w.id for w in whatsapp_settings(None)], ['channels.W001'])
        with override_settings(TAPFLASH=META):
            self.assertEqual(whatsapp_settings(None), [])


class CloudPhotoTests(TestCase):
    """A cleaner's photo on the official WhatsApp Cloud API is downloaded from Meta."""

    def message(self):
        return {'from': '254712345678', 'id': 'wamid.1', 'timestamp': str(int(time.time())), 'type': 'image',
                'image': {'id': 'MEDIA1', 'mime_type': 'image/jpeg'}}

    def test_a_photo_message_is_fetched_before_it_is_answered(self):
        from channels.cloud_api import fetch_photo, to_incoming

        class Client:
            def download_media(self, media_id):
                self.asked = media_id
                return b'\xff\xd8photo', 'image/jpeg'

        client = Client()
        incoming = fetch_photo(to_incoming(self.message()), client)
        self.assertEqual(client.asked, 'MEDIA1')
        self.assertEqual((incoming.type, incoming.image, incoming.image_type, incoming.phone),
                         ('image', b'\xff\xd8photo', 'image/jpeg', '+254712345678'))

    def test_a_photo_meta_wont_give_is_answered_like_a_sticker(self):
        from channels.cloud_api import fetch_photo, to_incoming

        class Client:
            def download_media(self, media_id):
                raise RuntimeError('gone')

        self.assertEqual(fetch_photo(to_incoming(self.message()), Client()).type, 'other')

    def test_download_media_asks_meta_for_the_url_then_the_bytes(self):
        from channels.cloud_api import GraphClient

        class Response:
            def __init__(self, status, body=None, content=b''):
                self.status_code, self._body, self.content = status, body, content

            def json(self):
                return self._body

        calls = []

        class Http:
            def get(self, url, headers, timeout):
                calls.append((url, headers['Authorization']))
                if url.endswith('/MEDIA1'):
                    return Response(200, {'url': 'https://lookaside.example/x', 'mime_type': 'image/png', 'file_size': 9})
                return Response(200, content=b'\x89PNGdata')

        config = replace(build_config({}), whatsapp_token='tok', whatsapp_phone_number_id='123')
        data, mimetype = GraphClient(config, session=Http()).download_media('MEDIA1')
        self.assertEqual((data, mimetype), (b'\x89PNGdata', 'image/png'))
        self.assertTrue(calls[0][0].endswith('/MEDIA1'))
        self.assertEqual(calls[1], ('https://lookaside.example/x', 'Bearer tok'))
