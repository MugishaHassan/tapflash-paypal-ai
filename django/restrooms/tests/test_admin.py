import io
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from bot.config import build_config
from restrooms.admin import RestroomForm
from restrooms.models import Restroom


def image_file(name='photo.png', size=(40, 30), image_format='PNG'):
    buffer = io.BytesIO()
    Image.new('RGB', size, 'teal').save(buffer, image_format)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f'image/{image_format.lower()}')


def form_data(**fields):
    return {
        'name': 'City Market', 'code': 'city-market', 'area': 'Nairobi CBD', 'latitude': '-1.2833',
        'longitude': '36.8219', 'price': '', 'opening_hours': '6:00 AM – 9:00 PM', 'is_active': 'on', 'cleanliness': '0', 'operator_share_percent': '80', **fields,
    }


class RestroomAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_superuser('boss', 'boss@example.com', 'test-only-password')

    def setUp(self):
        self.client.force_login(self.boss)
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        media_settings = override_settings(MEDIA_ROOT=media)
        media_settings.enable()
        self.addCleanup(media_settings.disable)

    def test_add_a_restroom_with_a_photo(self):
        response = self.client.post(reverse('admin:restrooms_restroom_add'), {**form_data(), 'photo': image_file()})
        self.assertRedirects(response, reverse('admin:restrooms_restroom_changelist'))
        market = Restroom.objects.get(code='city-market')
        self.assertIsNone(market.price)
        self.assertTrue(market.photo.name.startswith('restrooms/photo'))

        page = self.client.get(reverse('admin:restrooms_restroom_change', args=[market.pk]))
        self.assertContains(page, market.photo.url)
        self.assertContains(page, 'https://www.google.com/maps/search/?api=1&amp;query=-1.2833,36.8219')
        listing = self.client.get(reverse('admin:restrooms_restroom_changelist'))
        self.assertContains(listing, f'<img src="{market.photo.url}"')

    def test_unreadable_opening_hours_are_refused(self):
        response = self.client.post(reverse('admin:restrooms_restroom_add'), form_data(opening_hours='weekdays only'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'These opening hours can')
        self.assertFalse(Restroom.objects.exists())

    def test_photos_over_8_mb_are_refused(self):
        # An uncompressed BMP just over 8 MB.
        big = image_file('big.bmp', size=(1800, 1700), image_format='BMP')
        self.assertGreater(big.size, 8 * 1024 * 1024)
        form = RestroomForm(form_data(), {'photo': big})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors['photo'], ['This photo is too big. Please use one under 8 MB.'])
        self.assertTrue(RestroomForm(form_data(), {'photo': image_file()}).is_valid())

    def test_an_empty_price_shows_the_default_price(self):
        with self.settings(TAPFLASH=build_config({'DEFAULT_PRICE': '120'})):
            page = self.client.get(reverse('admin:restrooms_restroom_add'))
        self.assertContains(page, 'placeholder="120"')

    def test_prices_and_the_active_switch_can_be_edited_in_the_list(self):
        market = Restroom.objects.create(code='city-market', name='City Market', latitude=-1.28, longitude=36.82)
        response = self.client.post(reverse('admin:restrooms_restroom_changelist'), {
            'form-TOTAL_FORMS': '1', 'form-INITIAL_FORMS': '1', 'form-0-id': str(market.pk),
            'form-0-price': '75', '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302)
        market.refresh_from_db()
        self.assertEqual(market.price, Decimal(75))
        self.assertFalse(market.is_active, 'an unticked box turns it off')

    def test_the_list_warns_while_demo_restrooms_are_offered(self):
        url = reverse('admin:restrooms_restroom_changelist')
        with self.settings(TAPFLASH=build_config({'DEMO_RESTROOMS_NEAR_USER': 'true'})):
            self.assertContains(self.client.get(url), 'The chat is in demo mode')
        with self.settings(TAPFLASH=build_config({'DEMO_RESTROOMS_NEAR_USER': 'false'})):
            self.assertNotContains(self.client.get(url), 'demo mode')

    def test_activate_and_deactivate_actions(self):
        a = Restroom.objects.create(code='a', name='A', latitude=1, longitude=2)
        b = Restroom.objects.create(code='b', name='B', latitude=1, longitude=2, is_active=False)
        url = reverse('admin:restrooms_restroom_changelist')

        response = self.client.post(url, {'action': 'deactivate', '_selected_action': [a.pk, b.pk]}, follow=True)
        self.assertContains(response, '2 restrooms deactivated.')
        self.assertFalse(Restroom.objects.filter(is_active=True).exists())

        response = self.client.post(url, {'action': 'activate', '_selected_action': [b.pk]}, follow=True)
        self.assertContains(response, '1 restroom activated.')
        self.assertEqual(list(Restroom.objects.filter(is_active=True)), [b])
