import json
import math
from dataclasses import replace
from io import BytesIO
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from PIL import Image as PILImage

from bot.replies import Image, Menu, Option, Text
from channels.web_chat import to_incoming

WEB_ON = replace(settings.TAPFLASH, web_chat=True)
WEB_OFF = replace(settings.TAPFLASH, web_chat=False)
TOKEN = 'bridge-secret-token-1234567890'


def picture(width, height, kind='PNG'):
    buffer = BytesIO()
    PILImage.new('RGB', (width, height), '#2a7f86').save(buffer, kind)
    return buffer.getvalue()


class FakeHandler:
    """Stands in for bot.handler.handle_message: answers with self.answer(incoming, send)."""

    def __init__(self, answer=None):
        self.seen = []
        self.answer = answer or (lambda incoming, send: send(Text(f'You said {incoming.text or "a location"}')))

    def __call__(self, incoming, send, engine=None):
        self.seen.append(incoming)
        self.answer(incoming, send)


class WebChatTestCase(TestCase):
    def serve(self, answer=None):
        handler = FakeHandler(answer)
        patcher = mock.patch('channels.views.handle_message', handler)
        patcher.start()
        self.addCleanup(patcher.stop)
        return handler

    def post(self, body, content_type='application/json', **headers):
        data = body if isinstance(body, str) else json.dumps(body)
        return self.client.post('/api/message', data, content_type=content_type, headers=headers)


@override_settings(TAPFLASH=WEB_ON)
class WebChatTests(WebChatTestCase):
    def test_serves_the_chat_page_with_security_headers(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/html'))
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        csp = response['Content-Security-Policy']
        for part in ("default-src 'none'", 'img-src data: blob:', "connect-src 'self'", "frame-ancestors 'none'"):
            self.assertIn(part, csp)
        self.assertIn(b'<title>TapFlash test chat</title>', response.content)

    def test_turns_a_browser_message_into_an_incoming_message(self):
        handler = self.serve()
        response = self.post({'userId': 'abc', 'phone': '+254712345678', 'type': 'text', 'text': '1'})
        self.assertEqual(response.json(), {'replies': [{'kind': 'text', 'text': 'You said 1'}]})
        self.assertEqual(response['Cache-Control'], 'no-store')
        first = handler.seen[0]
        self.assertEqual((first.user_id, first.phone, first.type, first.text),
                         ('web:abc', '+254712345678', 'text', '1'))
        self.assertIsNotNone(first.received_at)
        self.post({'userId': 'abc', 'type': 'location', 'lat': '-1.285', 'lng': 36.82})
        self.assertEqual((handler.seen[1].type, tuple(handler.seen[1].location)), ('location', (-1.285, 36.82)))

    def test_empty_null_or_true_coordinates_are_not_a_place(self):
        for lat, lng in [(None, None), ('', ''), (True, False), ([1], [2]), ('abc', 'x')]:
            incoming = to_incoming({'userId': 'abc', 'type': 'location', 'lat': lat, 'lng': lng})
            self.assertTrue(math.isnan(incoming.location.lat) and math.isnan(incoming.location.lng), (lat, lng))
        incoming = to_incoming({'type': 'location'})
        self.assertTrue(math.isnan(incoming.location.lat))
        self.assertEqual(incoming.user_id, 'web:anon')

    def test_long_ids_and_texts_are_cut(self):
        incoming = to_incoming({'userId': 'u' * 200, 'phone': '+' + '1' * 40, 'text': 'x' * 5000}, channel='bridge')
        self.assertEqual((len(incoming.user_id), len(incoming.phone), len(incoming.text)), (68, 20, 4096))

    def test_the_web_chat_cannot_claim_another_number(self):
        incoming = to_incoming({'userId': 'u', 'phone': '+254799999999', 'text': 'hi'})
        self.assertEqual(incoming.phone, '+254712345678')

    def test_a_body_that_is_not_a_json_object_is_refused(self):
        handler = self.serve()
        for body in ['null', '[]', '"hi"', '5', 'true']:
            response = self.post(body)
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(response.json(), {'error': 'Send a JSON object'})
        self.assertEqual(handler.seen, [])

    def test_sends_menus_as_tappable_choices(self):
        photo, badge, route = picture(360, 240), picture(128, 128), picture(800, 500, 'JPEG')

        def answer(incoming, send):
            send(Menu(
                'Here are the closest restrooms.',
                [
                    Option('1', 'City Market Restroom', '280 m, KES 100', icon=Image(photo, 'image/png', 'thumb.png')),
                    Option('2', 'M-Pesa', icon=Image(badge, 'image/png', 'icon-mpesa.png')),
                    Option('0', 'Back', secondary=True),
                ],
                image=Image(route, 'image/jpeg', 'route-preview.png'),
                input_placeholder='07XX XXX XXX',
            ))

        self.serve(answer)
        [menu] = self.post({'userId': 'u', 'type': 'text', 'text': 'hi'}).json()['replies']
        self.assertEqual((menu['kind'], menu['text']), ('menu', 'Here are the closest restrooms.'))
        self.assertTrue(menu['image'].startswith('data:image/jpeg;base64,'))
        self.assertEqual([menu['imageWidth'], menu['imageHeight'], menu['imageAlt']], [800, 500, 'Route preview'])
        self.assertEqual(menu['input'], {'placeholder': '07XX XXX XXX'})
        restroom, mpesa, back = menu['options']
        self.assertEqual(restroom['description'], '280 m, KES 100')
        self.assertTrue(restroom['icon'].startswith('data:image/png;base64,'))
        self.assertEqual([restroom['iconWidth'], restroom['iconHeight'], restroom['secondary']], [360, 240, False])
        self.assertEqual([mpesa['iconWidth'], mpesa['iconHeight'], mpesa['description']], [128, 128, ''])
        self.assertEqual(back, {'id': '0', 'title': 'Back', 'description': '', 'secondary': True})

    def test_sends_pictures_as_data_urls(self):
        qr, photo = picture(512, 512), picture(800, 500, 'JPEG')

        def answer(incoming, send):
            send(Image(qr, 'image/png', 'receipt-TF8E2258190E.png'))
            send(Image(photo, 'image/jpeg', 'restroom-city-market.jpg', 'Finding the shortest route'))
            send(Image(b'', 'image/png', 'missing.png', 'Text still arrives'))
            send(Image(b'', 'image/png', 'missing.png'))

        self.serve(answer)
        replies = self.post({'userId': 'u', 'type': 'text', 'text': 'pay'}).json()['replies']
        self.assertEqual(len(replies), 3)
        self.assertEqual({**replies[0], 'src': replies[0]['src'][:22]},
                         {'kind': 'image', 'src': 'data:image/png;base64,', 'width': 512, 'height': 512,
                          'alt': 'Receipt', 'caption': ''})
        self.assertEqual([replies[1]['width'], replies[1]['height']], [800, 500])
        self.assertEqual(replies[1]['caption'], 'Finding the shortest route')
        self.assertEqual(replies[2], {'kind': 'text', 'text': 'Text still arrives'})

    def test_refuses_requests_it_should_not_handle(self):
        self.serve()
        self.assertEqual(self.post('{"type":"text"}', content_type='text/plain').status_code, 415)
        self.assertEqual(self.post('{not json').status_code, 400)
        self.assertEqual(self.post({'userId': 'u', 'text': 'x' * (70 * 1024)}).status_code, 413)
        self.assertEqual(self.client.get('/api/message').status_code, 405)
        self.assertEqual(self.client.get('/nope').status_code, 404)
        self.assertEqual(self.client.post('/api/message/', '{}', content_type='application/json').status_code, 404)
        self.assertEqual(self.post({'userId': 'u', 'text': 'hi'}).status_code, 200)

    def test_reports_an_error_instead_of_hanging(self):
        def answer(incoming, send):
            raise RuntimeError('engine broke')

        self.serve(answer)
        with self.assertLogs(level='ERROR'):
            response = self.post({'userId': 'u', 'text': 'hi'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {'error': 'Something went wrong'})


@override_settings(TAPFLASH=WEB_ON)
class RealChatTests(TestCase):
    def test_a_whole_chat_through_the_api(self):
        from bot.config import build_config
        from bot.engine import Engine

        engine = Engine(build_config({}))
        with mock.patch('bot.handler.default_engine', lambda: engine):
            def say(**body):
                return self.client.post('/api/message', json.dumps({'userId': 'tab', **body}),
                                        content_type='application/json').json()['replies']

            self.assertIn('TapFlash', say(text='hi')[0]['text'])
            menu = say(type='location', lat=-1.285, lng=36.82)[-1]
            self.assertEqual(menu['kind'], 'menu')
            self.assertGreaterEqual(len(menu['options']), 2)
            # A double send of the same text within 300 ms gets no second answer.
            say(text='exit')


@override_settings(TAPFLASH=WEB_OFF, BRIDGE_TOKEN=TOKEN)
class BridgeTests(WebChatTestCase):
    def test_production_shows_only_a_sign_of_life(self):
        response = self.client.get('/')
        self.assertEqual(response.content, b'TapFlash is running.')

    def test_a_valid_token_is_accepted_with_the_web_chat_off(self):
        handler = self.serve()
        response = self.post({'userId': '254712345678@c.us', 'phone': '+254712345678', 'text': 'hi'},
                             Authorization=f'Bearer {TOKEN}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(handler.seen[0].user_id, 'web:254712345678@c.us')
        self.assertEqual(handler.seen[0].phone, '+254712345678')

    def test_a_wrong_or_missing_token_is_refused_and_never_logged(self):
        handler = self.serve()
        with self.assertNoLogs(level='DEBUG'):
            for headers in [{}, {'Authorization': 'Bearer wrong'}, {'Authorization': TOKEN},
                            {'Authorization': f'Basic {TOKEN}'}, {'Authorization': f'Bearer {TOKEN}x'}]:
                response = self.post({'userId': 'u', 'text': 'hi'}, **headers)
                self.assertEqual(response.status_code, 403, headers)
                self.assertEqual(response.json(), {'error': 'Forbidden'})
        self.assertEqual(handler.seen, [])

    def test_no_token_set_means_no_access(self):
        self.serve()
        with override_settings(BRIDGE_TOKEN=''):
            self.assertEqual(self.post({'text': 'hi'}, Authorization='Bearer ').status_code, 403)


class HeaderLineTests(TestCase):
    def test_the_header_says_paypal_sandbox_when_paypal_is_on(self):
        from types import SimpleNamespace
        from unittest import mock
        with self.settings(TAPFLASH=WEB_ON):
            with mock.patch('channels.views.default_engine', return_value=SimpleNamespace(checkout=object())):
                self.assertContains(self.client.get('/'), 'PayPal sandbox, no real money')
            with mock.patch('channels.views.default_engine', return_value=SimpleNamespace(checkout=None)):
                self.assertContains(self.client.get('/'), 'Payments are simulated')
