import json
import time

from django.core.cache import cache
from django.test import TestCase, override_settings

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(LINK_PASSWORD='right password', CACHES=LOCMEM)
class LinkPageTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(LINK_PASSWORD='')
    def test_off_without_a_password(self):
        self.assertEqual(self.client.get('/link').status_code, 404)
        self.assertEqual(self.client.get('/link/status').status_code, 404)

    def test_status_is_locked_before_the_password(self):
        response = self.client.get('/link/status')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_right_password_shows_the_qr(self):
        response = self.client.post('/link', {'password': 'right password'})
        self.assertContains(response, 'Linked devices')
        self.assertEqual(self.client.get('/link/status').status_code, 200)

    def test_too_many_wrong_passwords_lock_out_even_the_right_one(self):
        for _ in range(5):
            self.assertContains(self.client.post('/link', {'password': 'wrong'}), 'Wrong password.')
        response = self.client.post('/link', {'password': 'right password'})
        self.assertContains(response, 'Too many wrong passwords.')
        self.assertEqual(self.client.get('/link/status').status_code, 403)

    def test_old_qr_is_not_shown(self, tmp=None):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'link.json'
            with self.settings(LINK_STATUS_FILE=file):
                self.client.post('/link', {'password': 'right password'})
                file.write_text(json.dumps({'state': 'qr', 'qr': 'abc', 'at': time.time() * 1000}))
                self.assertTrue(self.client.get('/link/status').json()['qr'].startswith('data:image/png'))
                file.write_text(json.dumps({'state': 'qr', 'qr': 'abc', 'at': 0}))
                self.assertEqual(self.client.get('/link/status').json(), {'state': 'starting'})


class LockoutWindowTests(TestCase):
    """With the file cache production uses, shared by the gunicorn workers."""

    def test_the_lockout_counts_from_the_first_wrong_try(self):
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as folder, override_settings(
            LINK_PASSWORD='right password',
            CACHES={'default': {'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache', 'LOCATION': folder}},
        ):
            start = time.time()
            with mock.patch('time.time', return_value=start):
                for _ in range(4):
                    self.client.post('/link', {'password': 'wrong'})
            # 14 minutes on, a fifth wrong try locks the page until 15 minutes
            # after the first, not 15 minutes after this one.
            with mock.patch('time.time', return_value=start + 14 * 60):
                self.client.post('/link', {'password': 'wrong'})
                self.assertContains(self.client.post('/link', {'password': 'right password'}), 'Too many')
            with mock.patch('time.time', return_value=start + 15 * 60 + 5):
                self.assertContains(self.client.post('/link', {'password': 'right password'}), 'Linked devices')
