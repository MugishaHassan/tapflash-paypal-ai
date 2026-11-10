"""The browser test chat and the WhatsApp Web bridge: a message as JSON in,
the replies as JSON out. Same contract as the Node version's web.js.
"""
import base64
import binascii
import math
import re
import struct
from pathlib import PurePath

from django.utils import timezone

from bot.engine import Incoming
from restrooms.geo import Location

MAX_BODY_BYTES = 64 * 1024
MAX_PHOTO_BODY_BYTES = 7 * 1024 * 1024  # a photo of up to 5 MB, in base64

# The page is one inline file. It may load Barlow from Google Fonts, show
# data: images and talk to this server, nothing else.
SECURITY_HEADERS = {
    'Content-Security-Policy': '; '.join([
        "default-src 'none'",
        "script-src 'unsafe-inline'",
        "style-src 'unsafe-inline' https://fonts.googleapis.com",
        'font-src https://fonts.gstatic.com',
        'img-src data: blob:',
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]),
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Cross-Origin-Opener-Policy': 'same-origin',
}

MIME_BY_EXT = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
               '.webp': 'image/webp'}
MIMETYPE = re.compile('image/[a-z0-9.+-]+', re.IGNORECASE)
# Reference codes such as TF8E2258190E, left out of picture descriptions.
CODE = re.compile(r'\b(?=[A-Z0-9]*[0-9])[A-Z0-9]{6,}\b')


def coordinate(value):
    """A number, or text holding one. Anything else (null, '', true, [1]) is
    NaN, never 0 or 1, which are real places on the map."""
    if isinstance(value, bool):
        return math.nan
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except OverflowError:
            return math.nan
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except (ValueError, OverflowError):
            return math.nan
    return math.nan


def _text(value):
    """JavaScript's String(value ?? '') for JSON values."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        return '[object Object]' if isinstance(value, dict) else ','.join(_text(v) for v in value)
    return str(value)


# The web test chat's own number. A browser could send any number, so the web chat
# always counts as this one; only the WhatsApp bridge (with its token) passes real numbers.
WEB_CHAT_PHONE = '+254712345678'


def to_incoming(body, channel='web', base_url=''):
    user_id = f"web:{(_text(body.get('userId')) or 'anon')[:64]}"
    if channel == 'web':
        phone = WEB_CHAT_PHONE
    else:
        phone = _text(body.get('phone'))[:20] if body.get('phone') else ''
    base = {'user_id': user_id, 'phone': phone, 'received_at': timezone.now(), 'channel': channel,
            'base_url': base_url}
    if body.get('type') == 'location':
        return Incoming(type='location', location=Location(coordinate(body.get('lat')), coordinate(body.get('lng'))),
                        **base)
    if body.get('type') == 'image':
        try:
            data = base64.b64decode(str(body.get('image') or ''), validate=True)
        except (ValueError, binascii.Error):
            data = b''
        return Incoming(type='image', image=data, image_type=_text(body.get('mimetype'))[:30].lower(), **base)
    return Incoming(type='text', text=_text(body.get('text'))[:4096], **base)


def image_size(data):
    """Width and height of a PNG or JPEG, so the page can keep space for a
    picture before it loads. None for anything else."""
    if not data or len(data) < 24:
        return None
    if data[:4] == b'\x89PNG' and data[12:16] == b'IHDR':
        return struct.unpack('>II', data[16:24])
    if data[0] == 0xFF and data[1] == 0xD8:
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                return None
            marker = data[i + 1]
            length = struct.unpack('>H', data[i + 2:i + 4])[0]
            # SOF0-SOF15 hold the size; C4, C8 and CC are not frames.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack('>HH', data[i + 5:i + 9])
                return width, height
            i += 2 + length
    return None


def alt_for(image):
    """A short description for screen readers: 'route-map.png' gives 'Route map'."""
    name = re.sub(r'[-_]+', ' ', PurePath(image.filename or '').stem)
    name = re.sub(r'\s+', ' ', CODE.sub('', name)).strip()
    return name[:1].upper() + name[1:]


def _mime(image):
    if MIMETYPE.fullmatch(image.mimetype or ''):
        return image.mimetype
    return MIME_BY_EXT.get(PurePath(image.filename or '').suffix.lower(), 'image/png')


def to_picture(image):
    """An Image as {src: data URL, width, height, alt}, or None."""
    if not image or not image.data:
        return None
    size = image_size(image.data)
    picture = {'src': f'data:{_mime(image)};base64,{base64.b64encode(image.data).decode()}', 'alt': alt_for(image)}
    if size:
        picture['width'], picture['height'] = size
    return picture


def _option(option):
    out = {'id': str(option.id), 'title': str(option.title), 'description': option.description or ''}
    icon = to_picture(option.icon)
    if icon:
        out['icon'] = icon['src']
        if 'width' in icon:
            out['iconWidth'], out['iconHeight'] = icon['width'], icon['height']
    out['secondary'] = bool(option.secondary)
    return out


def to_page_reply(reply):
    """A reply in the shape the page draws, or None."""
    if reply.kind == 'text':
        return {'kind': 'text', 'text': reply.text}
    if reply.kind == 'menu':
        out = {'kind': 'menu', 'text': reply.text}
        image = to_picture(reply.image)
        if image:
            out['image'] = image['src']
            if 'width' in image:
                out['imageWidth'], out['imageHeight'] = image['width'], image['height']
            out['imageAlt'] = image['alt']
        out['options'] = [_option(o) for o in reply.options]
        if reply.input_placeholder:
            out['input'] = {'placeholder': reply.input_placeholder}
        return out
    if reply.kind == 'image':
        picture = to_picture(reply)
        if not picture:
            return {'kind': 'text', 'text': reply.caption} if reply.caption else None
        return {'kind': 'image', **picture, 'caption': reply.caption or ''}
    return None
