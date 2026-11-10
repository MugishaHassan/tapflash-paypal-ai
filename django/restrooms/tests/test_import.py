import io
import json
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import CommandError, call_command
from django.db import DatabaseError
from django.test import TestCase, override_settings
from PIL import Image

from restrooms.models import Restroom

SAMPLE = settings.BASE_DIR / 'restrooms' / 'sample_restrooms.json'
NODE_DATA = settings.BASE_DIR.parent / 'data' / 'restrooms.json'


class ImportRestroomsTests(TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.file = self.dir / 'restrooms.json'

    def write(self, content):
        self.file.write_text(content if isinstance(content, str) else json.dumps(content), encoding='utf-8')

    def run_import(self, file=None, **options):
        out, err = io.StringIO(), io.StringIO()
        call_command('import_restrooms', str(file or self.file), stdout=out, stderr=err, **options)
        return out.getvalue(), err.getvalue()

    def assert_import_fails(self, message):
        with self.assertRaisesMessage(CommandError, message):
            self.run_import()
        self.assertEqual(Restroom.objects.count(), 0, 'nothing is saved')

    def test_the_bundled_sample_is_valid(self):
        out, _ = self.run_import(SAMPLE)
        self.assertIn('Imported 5 restrooms from sample_restrooms.json: 5 new, 0 updated.', out)
        market = Restroom.objects.get(code='nbi-city-market')
        self.assertEqual(market.name, 'City Market Restroom (sample)')
        self.assertEqual(market.price, Decimal(100))
        self.assertEqual(market.opening_hours, '6:00 AM – 9:00 PM')
        self.assertTrue(market.is_active)

    def test_the_node_data_file_is_valid(self):
        if not NODE_DATA.exists():
            self.skipTest('the Node version is not next to this folder')
        self.run_import(NODE_DATA)
        self.assertGreaterEqual(Restroom.objects.count(), 4)

    def test_importing_again_updates_and_keeps_the_active_switch(self):
        self.write([{'id': 'a', 'name': 'A', 'lat': 1, 'lng': 2}])
        self.run_import()
        Restroom.objects.filter(code='a').update(is_active=False)
        self.write([{'id': 'a', 'name': 'A renamed', 'lat': 1, 'lng': 2}, {'id': 'b', 'name': 'B', 'lat': 1, 'lng': 2}])
        out, _ = self.run_import()
        self.assertIn('2 restrooms', out)
        self.assertIn('1 new, 1 updated', out)
        a = Restroom.objects.get(code='a')
        self.assertEqual(a.name, 'A renamed')
        self.assertFalse(a.is_active)

    def test_missing_fields_get_defaults(self):
        self.write([{'id': 'a', 'name': 'A', 'lat': 1, 'lng': 2}])
        out, _ = self.run_import()
        self.assertIn('Imported 1 restroom from', out)
        a = Restroom.objects.get(code='a')
        self.assertIsNone(a.price, 'the default price applies')
        self.assertEqual((a.area, a.opening_hours, a.photo.name), ('', '', ''))

    def test_a_file_with_a_windows_bom_still_loads(self):
        self.file.write_text('\ufeff' + json.dumps([{'id': 'a', 'name': 'A', 'lat': 1, 'lng': 2}]), encoding='utf-8')
        self.run_import()
        self.assertEqual(Restroom.objects.count(), 1)

    def test_a_missing_file(self):
        with self.assertRaisesMessage(CommandError, 'not found'):
            self.run_import(self.dir / 'missing.json')

    def test_bad_json(self):
        self.write('{ not json')
        self.assert_import_fails('Could not read')

    def test_not_a_list(self):
        self.write({'id': 'a'})
        self.assert_import_fails('must contain a JSON array of restrooms')

    def test_bad_coordinates(self):
        for lat, lng in [('x', 2), (None, 2), (91, 2), (1, 181)]:
            with self.subTest(lat=lat, lng=lng):
                self.write([{'id': 'a', 'name': 'A', 'lat': lat, 'lng': lng}])
                self.assert_import_fails('restrooms.json entry #1: "lat" and "lng" must be valid numbers')

    def test_a_number_too_big_for_a_float(self):
        self.write('[{"id": "a", "name": "A", "lat": 1%s, "lng": 2}]' % ('0' * 400))
        self.assert_import_fails('entry #1: "lat" and "lng" must be valid numbers')

    def test_duplicate_id(self):
        self.write([{'id': 'a', 'name': 'A', 'lat': 1, 'lng': 2}, {'id': 'a', 'name': 'B', 'lat': 1, 'lng': 2}])
        self.assert_import_fails('entry #2: duplicate id "a"')

    def test_missing_id_or_name(self):
        self.write([{'name': 'A', 'lat': 1, 'lng': 2}])
        self.assert_import_fails('entry #1: "id" is required')
        self.write([{'id': 'a', 'lat': 1, 'lng': 2}])
        self.assert_import_fails('entry #1: "name" is required')

    def test_bad_price(self):
        for price in ['100', -1, True]:
            with self.subTest(price=price):
                self.write([{'id': 'a', 'name': 'A', 'lat': 1, 'lng': 2, 'price': price}])
                self.assert_import_fails('"price" must be a number')

    def test_rules_of_the_admin_apply_too(self):
        self.write([{'id': 'a', 'name': 'A', 'lat': 1, 'lng': 2, 'hours': 'weekdays only'}])
        self.assert_import_fails('entry #1: "hours": These opening hours can\'t be read.')
        self.write([{'id': 'not a slug', 'name': 'A', 'lat': 1, 'lng': 2}])
        self.assert_import_fails('entry #1: "id":')

    def test_one_bad_entry_saves_nothing(self):
        self.write([{'id': 'a', 'name': 'A', 'lat': 1, 'lng': 2}, {'id': 'b', 'name': 'B', 'lat': 'x', 'lng': 2}])
        self.assert_import_fails('entry #2')

    def test_photos_come_from_the_images_folder(self):
        (self.dir / 'images').mkdir()
        Image.new('RGB', (8, 6), 'teal').save(self.dir / 'images' / 'a.png')
        self.write([
            {'id': 'a', 'name': 'A', 'lat': 1, 'lng': 2, 'image': 'a.png'},
            {'id': 'b', 'name': 'B', 'lat': 1, 'lng': 2, 'image': 'missing.png'},
        ])
        with override_settings(MEDIA_ROOT=self.dir / 'media'):
            _, err = self.run_import()
            a = Restroom.objects.get(code='a')
            self.assertTrue(a.photo.name.startswith('restrooms/a'))
            self.assertTrue(Path(a.photo.path).is_file())
        self.assertIn('entry #2: image not found', err)
        self.assertEqual(Restroom.objects.get(code='b').photo.name, '')

    def test_a_file_that_is_not_a_picture_is_left_out(self):
        (self.dir / 'images').mkdir()
        (self.dir / 'images' / 'notes.png').write_text('not a picture')
        self.write([{'id': 'a', 'name': 'A', 'lat': 1, 'lng': 2, 'image': 'notes.png'}])
        with override_settings(MEDIA_ROOT=self.dir / 'media'):
            out, err = self.run_import()
        self.assertIn('1 new', out)
        self.assertIn('entry #1:', err)
        self.assertIn('notes.png is not a picture, imported without a photo', err)
        self.assertEqual(Restroom.objects.get(code='a').photo.name, '')
        self.assertFalse((self.dir / 'media').exists())

    def test_a_failed_import_leaves_no_photo_files(self):
        (self.dir / 'images').mkdir()
        Image.new('RGB', (8, 6), 'teal').save(self.dir / 'images' / 'a.png')
        self.write([
            {'id': 'a', 'name': 'A', 'lat': 1, 'lng': 2, 'image': 'a.png'},
            {'id': 'b', 'name': 'B', 'lat': 1, 'lng': 2},
        ])
        save = Restroom.objects.update_or_create

        def fail_on_b(code, defaults):
            if code == 'b':
                raise DatabaseError('the disk is full')
            return save(code=code, defaults=defaults)

        with override_settings(MEDIA_ROOT=self.dir / 'media'),                 mock.patch.object(Restroom.objects, 'update_or_create', side_effect=fail_on_b):
            with self.assertRaises(DatabaseError):
                self.run_import()
        self.assertFalse(Restroom.objects.exists())
        self.assertFalse((self.dir / 'media').exists())
