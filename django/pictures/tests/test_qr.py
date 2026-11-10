from io import BytesIO

from django.test import SimpleTestCase
from PIL import Image as PillowImage

from pictures.qr import qr_image


class QrTests(SimpleTestCase):
    def test_a_qr_code_is_a_black_and_white_png_of_about_512_pixels(self):
        image = qr_image('TAPFLASH-ACCESS|TF0123456789|demo-1|123456|2026-10-10T07:36:00.000Z', 'access.png')
        self.assertEqual((image.kind, image.mimetype, image.filename), ('image', 'image/png', 'access.png'))
        with PillowImage.open(BytesIO(image.data)) as picture:
            self.assertEqual(picture.format, 'PNG')
            self.assertEqual(picture.width, picture.height)
            self.assertTrue(480 <= picture.width <= 512, picture.width)
            self.assertEqual(sorted(color for _, color in picture.convert('L').getcolors()), [0, 255])

    def test_long_text_still_fits(self):
        image = qr_image('Restroom: ' + 'x' * 500, 'receipt.png')
        with PillowImage.open(BytesIO(image.data)) as picture:
            self.assertLessEqual(picture.width, 512)
