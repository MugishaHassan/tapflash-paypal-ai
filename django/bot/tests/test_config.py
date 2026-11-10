import os
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from bot.config import build_config, load_env_file


class BuildConfigTests(SimpleTestCase):
    def test_defaults(self):
        config = build_config({})
        self.assertEqual(config.bot_name, 'TapFlash')
        self.assertEqual(config.currency, 'KES')
        self.assertEqual(config.country_code, '254')
        self.assertEqual(config.timezone, 'Africa/Nairobi')
        self.assertEqual(config.default_price, Decimal(100))
        self.assertEqual(config.card_fee_percent, Decimal(1))
        self.assertEqual((config.max_results, config.max_distance_km), (4, 20))
        self.assertTrue(config.demo_restrooms_near_user)
        self.assertEqual((config.access_minutes, config.session_timeout_minutes), (30, 30))
        self.assertTrue(config.images and config.map_images)
        self.assertEqual(config.whatsapp_token, '')
        self.assertEqual(config.graph_api_version, 'v23.0')
        self.assertEqual(config.webhook_processing, 'thread')
        self.assertFalse(config.web_chat)

    def test_an_invalid_timezone_is_rejected_at_startup(self):
        for timezone in ['Nairobi', 'Africa', '../etc/passwd']:
            with self.subTest(timezone=timezone):
                with self.assertRaisesMessage(ImproperlyConfigured, 'is not a valid time zone'):
                    build_config({'TIMEZONE': timezone})
        self.assertEqual(build_config({'TIMEZONE': 'Africa/Dar_es_Salaam'}).timezone, 'Africa/Dar_es_Salaam')

    def test_country_code(self):
        self.assertEqual(build_config({'COUNTRY_CODE': '+255'}).country_code, '255')
        for bad in ['2545', 'ke', '\u0662\u0665\u0664']:
            with self.subTest(bad=bad), self.assertRaisesMessage(ImproperlyConfigured, 'COUNTRY_CODE must be 1-3 digits'):
                build_config({'COUNTRY_CODE': bad})

    def test_numbers(self):
        config = build_config({'DEFAULT_PRICE': '150.50', 'CARD_FEE_PERCENT': '1e1', 'MAX_DISTANCE_KM': '2.5'})
        self.assertEqual(str(config.default_price), '150.5')
        self.assertEqual(str(config.card_fee_percent), '10')
        self.assertEqual(config.max_distance_km, 2.5)
        self.assertEqual(f'{build_config({"MAX_DISTANCE_KM": "20.0"}).max_distance_km}', '20')

        for name, value, message in [
            ('DEFAULT_PRICE', 'free', 'DEFAULT_PRICE must be a number, got "free"'),
            ('CARD_FEE_PERCENT', 'NaN', 'CARD_FEE_PERCENT must be a number'),
            ('MAX_DISTANCE_KM', 'Infinity', 'MAX_DISTANCE_KM must be a number'),
            ('MAX_RESULTS', '2.5', 'MAX_RESULTS must be a whole number, got "2.5"'),
            ('ACCESS_MINUTES', 'half an hour', 'ACCESS_MINUTES must be a whole number'),
        ]:
            with self.subTest(name=name), self.assertRaisesMessage(ImproperlyConfigured, message):
                build_config({name: value})

    def test_switches(self):
        for word in ['1', 'true', 'YES', ' on ']:
            self.assertTrue(build_config({'MAP_IMAGES': 'false', 'IMAGES': word}).images)
        for word in ['0', 'false', 'no', 'off', 'nope']:
            self.assertFalse(build_config({'IMAGES': word}).images)
        self.assertTrue(build_config({'IMAGES': '  '}).images, 'blank means the default')

    def test_the_web_chat_follows_debug_unless_set(self):
        self.assertTrue(build_config({'DEBUG': 'true'}).web_chat)
        self.assertFalse(build_config({'DEBUG': 'true', 'WEB_CHAT': 'false'}).web_chat)
        self.assertTrue(build_config({'WEB_CHAT': 'true'}).web_chat)

    def test_webhook_processing(self):
        self.assertEqual(build_config({'WEBHOOK_PROCESSING': 'Inline'}).webhook_processing, 'inline')
        with self.assertRaisesMessage(ImproperlyConfigured, 'WEBHOOK_PROCESSING must be thread or inline'):
            build_config({'WEBHOOK_PROCESSING': 'later'})

    def test_whatsapp_keys_are_trimmed(self):
        config = build_config({'WHATSAPP_TOKEN': ' abc \n', 'WHATSAPP_APP_SECRET': 'secret'})
        self.assertEqual((config.whatsapp_token, config.whatsapp_app_secret), ('abc', 'secret'))


class EnvFileTests(SimpleTestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for name in ['TAPFLASH_TEST_FIRST', 'TAPFLASH_TEST_SECOND', 'TAPFLASH_TEST_SET']:
            self.addCleanup(os.environ.pop, name, None)

    def test_a_file_saved_with_a_windows_bom_still_loads(self):
        env_file = self.dir / '.env'
        env_file.write_bytes('\ufeffTAPFLASH_TEST_FIRST=yes\nTAPFLASH_TEST_SECOND=pa${ss}word\n'.encode())
        load_env_file(env_file)
        self.assertEqual(os.environ['TAPFLASH_TEST_FIRST'], 'yes')
        self.assertEqual(os.environ['TAPFLASH_TEST_SECOND'], 'pa${ss}word', 'secrets are taken as written')

    def test_the_real_environment_wins(self):
        env_file = self.dir / '.env'
        env_file.write_text('TAPFLASH_TEST_SET=from the file\n', encoding='utf-8')
        os.environ['TAPFLASH_TEST_SET'] = 'from the host'
        load_env_file(env_file)
        self.assertEqual(os.environ['TAPFLASH_TEST_SET'], 'from the host')

    def test_a_missing_file_is_fine(self):
        load_env_file(self.dir / 'missing.env')
