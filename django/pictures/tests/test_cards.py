import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.test import SimpleTestCase
from PIL import Image as PILImage

from payments.methods import PaymentMethod
from payments.passes import Pass
from pictures.cards import Cards
from pictures.drawing import FONTS, clean, fit_text, font, text_width
from pictures.qr import qr_image
from restrooms.facility import Facility

LONG_NAME = 'The Extraordinarily Long Named Public Convenience Facility at the Old Railway Station'


def open_png(image):
    picture = PILImage.open(BytesIO(image.data))
    picture.load()
    return picture


def facility(**fields):
    base = dict(code='city-market', name='City Market Restroom', area='CBD', lat=-1.284, lng=36.82,
                price=Decimal(100), distance_km=0.28)
    return Facility(**{**base, **fields})


def access_pass(**fields):
    base = dict(reference='TF8E2258190E', facility_code='city-market', facility_name='City Market Restroom',
                method_label='M-Pesa', phone='+254712345678', total=Decimal('101.50'), otp='123456',
                expires_at=datetime(2026, 10, 10, 7, 55, tzinfo=timezone.utc))
    return Pass(**{**base, **fields})


class TextTests(SimpleTestCase):
    def test_bundled_fonts_load(self):
        for name in ('Barlow-Regular.ttf', 'Barlow-Bold.ttf', 'OFL.txt'):
            self.assertTrue((FONTS / name).is_file(), name)
        self.assertEqual(font(20).getname(), ('Barlow', 'Regular'))
        self.assertEqual(font(20, bold=True).getname(), ('Barlow', 'Bold'))

    def test_clean_drops_emoji_and_control_characters(self):
        self.assertEqual(clean(' Café\x00 \U0001F6BB  <b>&\n'), 'Café <b>&')
        self.assertEqual(clean(None), '')

    def test_fit_text_cuts_between_words_with_an_ellipsis(self):
        self.assertEqual(fit_text('Short', 500, 20), 'Short')
        cut = fit_text(LONG_NAME, 300, 20)
        self.assertTrue(cut.endswith('…'))
        self.assertLessEqual(text_width(cut, 20), 300)
        self.assertTrue(LONG_NAME.startswith(cut[:-1]))
        self.assertNotEqual(cut[-2], ' ')


class CardsTests(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.media = Path(folder.name)
        self.cards = Cards('KES', 'Africa/Nairobi', self.media)

    def save_photo(self, name='photos/city.jpg', size=(1200, 900)):
        path = self.media / name
        path.parent.mkdir(parents=True, exist_ok=True)
        PILImage.new('RGB', size, '#cc3300').save(path, quality=100)
        return name

    def assertColor(self, pixel, expected, delta=6):
        for got, want in zip(pixel[:3], expected):
            self.assertAlmostEqual(got, want, delta=delta)

    def test_thumbnail_without_a_photo_is_the_placeholder(self):
        thumb = self.cards.restroom_thumb(facility())
        self.assertEqual(thumb.filename, 'thumb-city-market.png')
        picture = open_png(thumb)
        self.assertEqual((picture.size, picture.mode), ((360, 240), 'RGBA'))
        self.assertEqual(picture.getpixel((0, 0))[3], 0)  # rounded corner
        self.assertEqual(picture.getpixel((180, 120))[3], 255)

    def test_thumbnail_uses_the_photo(self):
        thumb = open_png(self.cards.restroom_thumb(facility(photo=self.save_photo())))
        self.assertColor(thumb.getpixel((180, 30)), (204, 51, 0))

    def test_a_broken_or_missing_photo_falls_back(self):
        (self.media / 'broken.jpg').write_bytes(b'not a picture')
        with self.assertLogs('pictures.cards', 'WARNING'):
            thumb = open_png(self.cards.restroom_thumb(facility(photo='broken.jpg')))
        self.assertGreater(thumb.getpixel((180, 30))[1], 150)  # the light placeholder
        open_png(self.cards.restroom_thumb(facility(photo='gone.jpg')))
        # A path outside MEDIA_ROOT is never read.
        self.assertIsNone(self.cards._photo_path(facility(photo='../../etc/passwd')))

    def test_restroom_photo_with_and_without_a_photo(self):
        plain = self.cards.restroom_photo(facility(name=LONG_NAME))
        self.assertEqual((plain.filename, open_png(plain).size), ('restroom-city-market.png', (800, 500)))
        photo = open_png(self.cards.restroom_photo(facility(photo=self.save_photo(size=(3000, 1000)))))
        self.assertEqual(photo.size, (800, 500))
        self.assertColor(photo.getpixel((400, 20)), (204, 51, 0))
        self.assertLess(photo.getpixel((790, 495))[0], 120)  # the dark fade

    def test_payment_icons(self):
        methods = [PaymentMethod(id, label, False, Decimal(0)) for id, label in
                   [('mpesa', 'M-Pesa'), ('airtel', 'Airtel Money'), ('card', 'Card'), ('paypal', 'PayPal'),
                    ('cash', 'cash')]]
        for method in methods:
            icon = self.cards.payment_icon(method)
            self.assertEqual((icon.filename, open_png(icon).size), (f'icon-{method.id}.png', (128, 128)))
        self.assertColor(open_png(self.cards.payment_icon(methods[0])).getpixel((64, 10)), (28, 124, 125))

    def test_a_custom_payment_logo_is_used(self):
        (self.media / 'payment-icons').mkdir()
        PILImage.new('RGB', (50, 50), '#0000ff').save(self.media / 'payment-icons' / 'mpesa.png')
        icon = open_png(self.cards.payment_icon(PaymentMethod('mpesa', 'M-Pesa', True, Decimal(0))))
        self.assertColor(icon.getpixel((64, 64)), (0, 0, 255))

    def test_access_card_with_without_and_with_a_bad_qr_code(self):
        qr = qr_image('TAPFLASH-ACCESS|TF8E2258190E', 'access.png').data
        with_qr = open_png(self.cards.access_card(access_pass(), qr))
        without = open_png(self.cards.access_card(access_pass(facility_name=LONG_NAME), None))
        with self.assertLogs('pictures.cards', 'WARNING'):
            bad = open_png(self.cards.access_card(access_pass(), b'not a png'))
        self.assertEqual(with_qr.width, 800)
        self.assertGreater(with_qr.height, without.height + 250)
        self.assertEqual(bad.size, without.size)
        self.assertEqual(self.cards.access_card(access_pass(), qr).filename, 'access-TF8E2258190E.png')
