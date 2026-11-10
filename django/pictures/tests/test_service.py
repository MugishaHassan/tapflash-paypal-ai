import builtins
import tempfile
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest import mock

from django.test import TestCase
from PIL import Image as PILImage

from bot.config import build_config
from bot.tests.helpers import ChatTester
from pictures.service import Pictures, build_pictures
from pictures.tests.test_maps import FakeNetwork

CONFIG = build_config({})


class BuildTests(TestCase):
    def test_pictures_off(self):
        self.assertIsNone(build_pictures(replace(CONFIG, images=False), Path('.'), Path('.')))

    def test_a_broken_image_library_means_text_only(self):
        real_import = builtins.__import__

        def no_pillow(name, *args, **kwargs):
            if name == 'PIL' or name.startswith('PIL.') or name.endswith('cards'):
                raise ImportError('Illegal instruction')
            return real_import(name, *args, **kwargs)

        with mock.patch('builtins.__import__', no_pillow), self.assertLogs('pictures.service', 'ERROR'):
            self.assertIsNone(build_pictures(CONFIG, Path('.'), Path('.')))

    def test_maps_can_be_turned_off(self):
        pictures = build_pictures(replace(CONFIG, map_images=False), Path('.'), Path('.'))
        self.assertIsNone(pictures.route_map(None, None))
        self.assertIsNone(pictures.route_preview(None, None))


class WholeChatTests(TestCase):
    def test_a_paid_chat_produces_real_pictures(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        pictures = Pictures(CONFIG, Path(folder.name), Path(folder.name), http_get=FakeNetwork())
        chat = ChatTester(pictures=pictures)
        chat.say('hi')
        [menu] = chat.share_location()
        self.assertTrue(all(option.icon for option in menu.options if not option.secondary))
        [pay] = chat.say('1')
        self.assertEqual(pay.image.filename, 'route-preview.png')
        [methods] = chat.say('1')
        self.assertEqual(methods.options[0].icon.filename, 'icon-mpesa.png')
        chat.say('3')  # card
        paid = chat.say('1')
        names = [getattr(reply, 'filename', None) or getattr(getattr(reply, 'image', None), 'filename', None)
                 for reply in paid]
        self.assertIn('route-map.png', names)
        self.assertTrue(any(name and name.startswith('restroom-') for name in names))
        chat.say('1')  # arrived
        [access] = chat.say('1')  # unlock
        self.assertTrue(access.image.filename.startswith('access-'))
        for image in (pay.image, access.image):
            self.assertEqual(PILImage.open(BytesIO(image.data)).format, 'PNG')
