"""Pictures sent in the chat: restroom thumbnails and photos, payment icons
and the access card. A port of the Node version's cards.js, drawn with Pillow.
"""
import logging
import re
from io import BytesIO
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageOps

from bot.formatting import format_money, format_time
from restrooms.demo import DEMO_PHOTO_PREFIX, DEMO_PHOTOS
from restrooms.geo import describe_distance

from .drawing import Canvas, clean, rounded_mask, slug, text_width, to_png

logger = logging.getLogger(__name__)

WIDTH = 800
PAD = 16  # grey margin around the white card
INSET = 28  # space between the card edge and its content
LEFT = PAD + INSET
RIGHT = WIDTH - PAD - INSET

BG = '#f0f2f5'
TEXT = '#111b21'
MUTED = '#667781'
GREEN = '#1C1E49'  # TapFlash navy (the name is historical)
BLUE = '#2A6F8F'  # values on the access card: a deep TapFlash cyan
LINE = '#e9edef'

THUMB_W, THUMB_H = 180, 120
ICON_SIZE = 64
QR_SIZE = 260
PHOTO_W, PHOTO_H = 800, 500
# Small pictures are sent at twice their size so they stay sharp on phones.
SMALL_SCALE = 2
SAFE_ID = re.compile('[A-Za-z0-9_-]+')


def load_photo(path, width, height):
    """A photo cropped to fill width x height, or None if it can't be read."""
    if not path:
        return None
    try:
        with PILImage.open(path) as source:
            source.draft('RGB', (width, height))  # big JPEGs decode at a smaller size
            photo = ImageOps.exif_transpose(source).convert('RGB')
        return ImageOps.fit(photo, (width, height), PILImage.LANCZOS)
    except Exception as error:
        logger.warning('Could not use image %s: %s', path, error)
        return None


def _gradient(width, height, start, end):
    """A diagonal gradient from the top-left colour to the bottom-right one."""
    down = PILImage.linear_gradient('L')
    corner = PILImage.blend(down, down.transpose(PILImage.Transpose.TRANSPOSE), 0.5).resize((width, height))
    return PILImage.composite(PILImage.new('RGB', (width, height), end), PILImage.new('RGB', (width, height), start), corner)


def _restroom_sign(canvas, x, y, scale):
    """Two simple figures on a sign plate, in a 120 x 100 box."""
    def at(px, py):
        return x + px * scale, y + py * scale

    def rect(px, py, w, h, r, fill):
        canvas.rect(*at(px, py), w * scale, h * scale, fill=fill, radius=r * scale)

    rect(0, 5, 120, 100, 18, (11, 59, 64, 36))
    rect(0, 0, 120, 100, 18, (255, 255, 255, 245))
    canvas.line([at(60, 17), at(60, 83)], '#b9d5d7', 2 * scale)
    teal = '#2a7f86'
    canvas.circle(*at(30, 26), 8.5 * scale, fill=teal)
    rect(19, 39, 22, 27, 8, teal)
    rect(22, 58, 7, 26, 3.5, teal)
    rect(31, 58, 7, 26, 3.5, teal)
    canvas.circle(*at(90, 26), 8.5 * scale, fill=teal)
    canvas.polygon([at(90, 37.5), at(84.5, 39), at(80, 46), at(73, 68), at(107, 68), at(100, 46), at(95.5, 39)], teal)
    rect(83, 66, 6, 18, 3, teal)
    rect(91, 66, 6, 18, 3, teal)


def placeholder(width, height, name='', caption='Photo coming soon', scale=2):
    """A soft tiled picture with a restroom sign, for restrooms without a photo."""
    canvas = Canvas(width, height, scale=scale)
    canvas.image.paste(_gradient(*canvas.image.size, '#bfe3df', '#d9dfe3'))
    tile = max(12, round(width / 10))
    for x in range(tile, width + 1, tile):
        canvas.line([(x, 0), (x, height)], (255, 255, 255, 97), 1.5, round_caps=False)
    for y in range(tile, height + 1, tile):
        canvas.line([(0, y), (width, y)], (255, 255, 255, 97), 1.5, round_caps=False)
    canvas.circle(width * 0.9, height * 0.08, height * 0.42, fill=(255, 255, 255, 56))
    canvas.circle(width * 0.08, height * 0.98, height * 0.36, fill=(255, 255, 255, 46))

    has_name = bool(clean(name))
    sign_h = height * (0.36 if has_name else 0.5)
    sign_scale = sign_h / 100
    name_size = round(height * 0.075)
    cap_size = round(height * 0.044) if has_name else max(10, round(height * 0.085))
    gap = height * (0.065 if has_name else 0.06)
    block = sign_h + gap + (name_size + height * 0.03 if has_name else 0) + cap_size
    top = (height - block) / 2
    _restroom_sign(canvas, (width - 120 * sign_scale) / 2, top, sign_scale)

    cursor = top + sign_h + gap
    if has_name:
        cursor += name_size * 0.8
        canvas.text(width / 2, cursor, name, name_size, bold=True, fill='#1b4f55', anchor='middle',
                    max_width=width - 80)
        cursor += name_size * 0.2 + height * 0.03
    cursor += cap_size * 0.8
    canvas.text(width / 2, cursor, caption, cap_size, fill='#4f7e83', anchor='middle')
    return canvas.image


def _badge(method, size):
    """A generic badge for a payment method (not anyone's logo artwork)."""
    canvas = Canvas(64, 64, scale=size / 64)
    white = '#ffffff'
    if method.id == 'mpesa':
        # Neutral colours and plain lettering: no one else's brand colours or wordmark.
        canvas.rect(0, 0, 64, 64, fill='#1C7C7D')
        canvas.text(32, 31, 'Mobile', 13, bold=True, fill=white, anchor='middle')
        canvas.text(32, 47, 'money', 13, bold=True, fill=white, anchor='middle')
    elif method.id == 'airtel':
        canvas.rect(0, 0, 64, 64, fill='#3E5A69')
        canvas.text(32, 31, 'Mobile', 13, bold=True, fill=white, anchor='middle')
        canvas.text(32, 47, 'money', 13, bold=True, fill=white, anchor='middle')
    elif method.id == 'card':
        canvas.rect(0, 0, 64, 64, fill='#223a66')
        canvas.rect(11, 18, 42, 29, fill=white, radius=5)
        canvas.rect(11, 24, 42, 7, fill='#223a66')
        canvas.rect(16, 37, 11, 5, fill='#9aa8c4', radius=2)
    elif method.id == 'paypal':
        canvas.rect(0, 0, 64, 64, fill='#003087')
        canvas.text(32, 38, 'PayPal', 15, bold=True, fill=white, anchor='middle')
    else:
        canvas.rect(0, 0, 64, 64, fill=MUTED)
        initial = (clean(method.label)[:1] or '?').upper()
        canvas.text(32, 42, initial, 30, bold=True, fill=white, anchor='middle')
    return canvas.image


def _spaced_otp(otp):
    """'123456' gives '123 456'."""
    digits = re.sub(r'\s+', '', clean(otp))
    if len(digits) == 6:
        return f'{digits[:3]} {digits[3:]}'
    if len(digits) == 8:
        return f'{digits[:4]} {digits[4:]}'
    return digits


def _qr_picture(qr_png, size):
    """The QR code without its white margin, scaled to size, or None if the
    bytes are not a picture."""
    if not qr_png:
        return None
    try:
        with PILImage.open(BytesIO(qr_png)) as source:
            qr = source.convert('L')
        box = qr.point(lambda value: 255 if value < 196 else 0).getbbox()
        if box:
            qr = qr.crop(box)
        resample = PILImage.NEAREST if qr.width < size else PILImage.LANCZOS
        return qr.resize((size, size), resample).convert('RGB')
    except Exception as error:
        logger.warning('Could not use the QR code: %s', error)
        return None


class Cards:
    def __init__(self, currency, timezone, media_root):
        self.currency = currency
        self.timezone = timezone
        self.media_root = Path(media_root)

    def _photo_path(self, facility):
        """The restroom's uploaded photo, only if it is inside MEDIA_ROOT."""
        if not facility.photo:
            return None
        if facility.photo.startswith(DEMO_PHOTO_PREFIX):
            path = DEMO_PHOTOS / facility.photo.removeprefix(DEMO_PHOTO_PREFIX)
            return path if path.parent == DEMO_PHOTOS and path.is_file() else None
        root = self.media_root.resolve()
        path = (root / facility.photo).resolve()
        return path if path.is_relative_to(root) and path.is_file() else None

    def _distance(self, facility):
        return describe_distance(facility.distance_km) if facility.distance_km is not None else ''

    def _card(self, height):
        """The white rounded card on the light chat-grey background."""
        canvas = Canvas(WIDTH, height, background=BG)
        canvas.rect(PAD, PAD, WIDTH - 2 * PAD, height - 2 * PAD, fill='#ffffff', radius=24, outline='#e4e8eb')
        return canvas

    # ------------------------------------------------------------ restrooms

    def restroom_thumb(self, facility):
        """A rounded thumbnail: the photo if there is one, else the placeholder."""
        w, h = THUMB_W * SMALL_SCALE, THUMB_H * SMALL_SCALE
        inner = load_photo(self._photo_path(facility), w, h) or placeholder(THUMB_W, THUMB_H, scale=SMALL_SCALE)
        thumb = PILImage.new('RGBA', (w, h), (0, 0, 0, 0))
        thumb.paste(inner, (0, 0), rounded_mask(w, h, 14 * SMALL_SCALE))
        return to_png(thumb, f'thumb-{slug(facility.code)}.png')

    def restroom_photo(self, facility):
        """The photo shown while the bot looks for the route: the restroom photo
        with its name on a dark fade, or the illustration."""
        filename = f'restroom-{slug(facility.code)}.png'
        photo = load_photo(self._photo_path(facility), PHOTO_W * 2, PHOTO_H * 2)
        if not photo:
            picture = placeholder(PHOTO_W, PHOTO_H, name=facility.name)
            return to_png(picture.resize((PHOTO_W, PHOTO_H), PILImage.LANCZOS), filename)

        sub = ', '.join(part for part in (self._distance(facility), clean(facility.area)) if part)
        fade_h = 170 if sub else 130
        canvas = Canvas(PHOTO_W, PHOTO_H)
        canvas.image.paste(photo)
        # A fade from clear to 66% black over the bottom of the photo.
        alpha = PILImage.linear_gradient('L').resize((PHOTO_W * 2, fade_h * 2)).point(lambda v: round(v * 0.66))
        black = PILImage.new('RGB', alpha.size, '#000000')
        canvas.image.paste(black, (0, (PHOTO_H - fade_h) * 2), alpha)
        canvas.text(32, PHOTO_H - (66 if sub else 32), facility.name, 34, bold=True, fill='#ffffff',
                    max_width=PHOTO_W - 64)
        if sub:
            canvas.text(32, PHOTO_H - 30, sub, 21, fill='#e9edef', max_width=PHOTO_W - 64)
        return to_png(canvas.result(), filename)

    # ------------------------------------------------------------ payments

    def _custom_icon(self, method_id, size):
        """A logo uploaded as MEDIA_ROOT/payment-icons/<id>.png, on white."""
        if not SAFE_ID.fullmatch(str(method_id)):
            return None
        path = self.media_root / 'payment-icons' / f'{method_id}.png'
        if not path.is_file():
            return None
        try:
            with PILImage.open(path) as source:
                logo = source.convert('RGBA')
            logo = ImageOps.contain(logo, (size, size), PILImage.LANCZOS)
            square = PILImage.new('RGBA', (size, size), '#ffffff')
            square.alpha_composite(logo, ((size - logo.width) // 2, (size - logo.height) // 2))
            return square.convert('RGB')
        except Exception as error:
            logger.warning('Could not use payment icon %s: %s', path, error)
            return None

    def payment_icon(self, method):
        size = ICON_SIZE * SMALL_SCALE
        inner = self._custom_icon(method.id, size * 2) or _badge(method, size * 2)
        icon = PILImage.new('RGBA', (size * 2, size * 2), (0, 0, 0, 0))
        icon.paste(inner, (0, 0), rounded_mask(size * 2, size * 2, 14 * SMALL_SCALE * 2))
        return to_png(icon.resize((size, size), PILImage.LANCZOS), f'icon-{slug(method.id)}.png')

    # ------------------------------------------------------------ access card

    def access_card(self, access_pass, qr_png):
        """'Your access details': amount, restroom, expiry, OTP and QR code."""
        qr = _qr_picture(qr_png, QR_SIZE * 2)
        cx = WIDTH / 2
        amount = format_money(access_pass.total, self.currency)
        expiry = format_time(access_pass.expires_at, self.timezone)

        def column_width(label, value, low, high):
            return int(min(high, max(low, text_width(label, 18), text_width(value, 26, True))) + 0.999)

        amount_w = column_width('Amount paid', amount, 150, 220)
        expiry_w = column_width('Session expiry', expiry, 0, 160)
        facility_x = LEFT + amount_w + 44
        columns = [
            (LEFT, amount_w, 'Amount paid', amount),
            (facility_x, RIGHT - expiry_w - 36 - facility_x, 'Facility', access_pass.facility_name),
            (RIGHT - expiry_w, expiry_w, 'Session expiry', expiry),
        ]

        title_y = PAD + 64
        cols_y = PAD + 128
        otp_top = PAD + 210
        otp_h = 100
        qr_pad = 20
        qr_box = QR_SIZE + 2 * qr_pad
        qr_top = otp_top + otp_h + 28
        footer_y = (qr_top + qr_box if qr else otp_top + otp_h) + 50
        height = footer_y + 34 + PAD

        canvas = self._card(height)
        # Green check badge and the title.
        canvas.circle(LEFT + 22, title_y - 10, 22, fill=GREEN)
        canvas.line([(LEFT + 12, title_y - 9), (LEFT + 19.5, title_y - 1), (LEFT + 32.5, title_y - 16)], '#ffffff', 4.5)
        canvas.text(LEFT + 58, title_y, 'Your access details', 32, bold=True, fill=TEXT)
        canvas.line([(LEFT, PAD + 94), (RIGHT, PAD + 94)], LINE, 2, round_caps=False)

        for x, width, label, value in columns:
            canvas.text(x, cols_y, label, 18, fill=MUTED)
            canvas.text(x, cols_y + 38, value or '-', 26, bold=True, fill=BLUE, max_width=width)

        # The access code in a light green pill.
        pill_w = 560
        canvas.rect(cx - pill_w / 2, otp_top, pill_w, otp_h, fill='#e6f6fa', radius=22)
        otp = _spaced_otp(access_pass.otp)
        label_w = text_width('OTP', 26, True)
        code_w = sum(text_width(ch, 58, True) + 5 for ch in otp)
        left = cx - (label_w + 20 + code_w) / 2
        canvas.text(left, otp_top + 70, 'OTP', 26, bold=True, fill=GREEN)
        canvas.text(left + label_w + 20, otp_top + 70, otp, 58, bold=True, fill=TEXT, spacing=5)

        if qr:
            canvas.rect(cx - qr_box / 2, qr_top, qr_box, qr_box, fill='#ffffff', radius=20, outline=LINE, width=2)
            canvas.paste(qr, cx - QR_SIZE / 2, qr_top + qr_pad)

        canvas.text(cx, footer_y, f'Show this at the entrance. Ref {clean(access_pass.reference)}', 20,
                    fill=MUTED, anchor='middle', max_width=RIGHT - LEFT)
        return to_png(canvas.result(), f'access-{slug(access_pass.reference)}.png')
