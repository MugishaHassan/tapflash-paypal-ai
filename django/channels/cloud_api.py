"""Meta's WhatsApp Cloud API: reading webhook messages and sending replies
through the Graph API. A port of the Node version's cloud-api.js.

Menus become real WhatsApp messages: up to 3 short choices are reply
buttons, more become a tap list, and more than 10 are written out as text.
"""
import hashlib
import hmac
import logging
import re
from dataclasses import replace

import requests

from bot.engine import Incoming
from bot.replies import menu_text
from restrooms.geo import Location

logger = logging.getLogger(__name__)

# WhatsApp's limits for interactive messages.
MAX_BUTTONS = 3
MAX_BUTTON_TITLE = 20
MAX_ROWS = 10
MAX_ROW_TITLE = 24
MAX_ROW_DESCRIPTION = 72
MAX_BUTTON_BODY = 1024
MAX_LIST_BODY = 4096
MAX_CAPTION = 1024

PHONE_ID = re.compile('[0-9]{6,15}')
TIMEOUT = 20  # seconds for each Graph call


def cut(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def valid_signature(raw_body, header, app_secret):
    """Is the X-Hub-Signature-256 header Meta's signature of this exact body?"""
    if not app_secret:
        return True
    expected = 'sha256=' + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest((header or '').encode(), expected.encode())


def messages_in(payload):
    """Every message in a webhook delivery."""
    out = []
    for entry in _list(payload, 'entry'):
        for change in _list(entry, 'changes'):
            value = change.get('value') if isinstance(change, dict) else None
            out += [m for m in _list(value, 'messages') if isinstance(m, dict)]
    return out


def _list(obj, key):
    value = obj.get(key) if isinstance(obj, dict) else None
    return value if isinstance(value, list) else []


def _get(obj, *keys):
    for key in keys:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def _coordinate(value):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return float('nan')


def to_incoming(message):
    """A Meta message as the engine's Incoming, or None to ignore it."""
    sender = str(message.get('from') or '')
    if not PHONE_ID.fullmatch(sender):
        return None
    base = {'user_id': sender, 'phone': f'+{sender}', 'channel': 'cloud'}
    kind = message.get('type')
    if kind == 'text':
        return Incoming(type='text', text=str(_get(message, 'text', 'body') or ''), **base)
    if kind == 'interactive':
        # A tapped button or list row sends back the option id we gave it.
        reply = _get(message, 'interactive', 'button_reply') or _get(message, 'interactive', 'list_reply')
        return Incoming(type='text', text=str(_get(reply, 'id') or ''), **base)
    if kind == 'button':
        return Incoming(type='text', text=str(_get(message, 'button', 'payload') or _get(message, 'button', 'text') or ''), **base)
    if kind == 'image':
        # The photo itself is fetched from Meta when the message is answered (fetch_photo).
        return Incoming(type='image', text=str(_get(message, 'image', 'id') or '')[:100],
                        image_type=str(_get(message, 'image', 'mime_type') or 'image/jpeg')[:30], **base)
    if kind == 'location':
        location = Location(_coordinate(_get(message, 'location', 'latitude')),
                            _coordinate(_get(message, 'location', 'longitude')))
        return Incoming(type='location', location=location, **base)
    return Incoming(type='other', **base)


def interactive_for(menu):
    """How to show a menu: reply buttons, a list, or None (send it as text)."""
    options = menu.options
    as_buttons = (
        len(options) <= MAX_BUTTONS
        and all(len(o.title) <= MAX_BUTTON_TITLE for o in options)
        and len(menu.text) <= MAX_BUTTON_BODY
    )
    if as_buttons:
        return {
            'type': 'button',
            'body': {'text': menu.text},
            'action': {'buttons': [{'type': 'reply', 'reply': {'id': o.id, 'title': o.title}} for o in options]},
        }
    if len(options) <= MAX_ROWS and len(menu.text) <= MAX_LIST_BODY:
        rows = []
        for o in options:
            # A title too long for the row keeps its full wording as the description.
            description = o.description or (o.title if len(o.title) > MAX_ROW_TITLE else '')
            row = {'id': o.id, 'title': cut(o.title, MAX_ROW_TITLE)}
            if description:
                row['description'] = cut(description, MAX_ROW_DESCRIPTION)
            rows.append(row)
        return {
            'type': 'list',
            'body': {'text': menu.text},
            'action': {'button': 'Choose', 'sections': [{'rows': rows}]},
        }
    return None


class GraphClient:
    """Sends replies to one WhatsApp number through Meta's Graph API."""

    def __init__(self, config, session=None):
        self.config = config
        self.base = f'https://graph.facebook.com/{config.graph_api_version}/{config.whatsapp_phone_number_id}'
        self.http = session or requests.Session()

    def _call(self, path, **kwargs):
        response = self.http.post(
            self.base + path, headers={'Authorization': f'Bearer {self.config.whatsapp_token}'},
            timeout=TIMEOUT, **kwargs,
        )
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not 200 <= response.status_code < 300:
            message = _get(data, 'error', 'message') or response.reason
            raise RuntimeError(f'Meta API {response.status_code}: {message}')
        return data

    def send_message(self, to, payload):
        return self._call('/messages', json={
            'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': to, **payload,
        })

    def download_media(self, media_id, limit=5 * 1024 * 1024):
        """A photo the user sent: Meta gives a short-lived URL for its media id."""
        headers = {'Authorization': f'Bearer {self.config.whatsapp_token}'}
        root = f'https://graph.facebook.com/{self.config.graph_api_version}'
        meta = self.http.get(f'{root}/{media_id}', headers=headers, timeout=TIMEOUT)
        if meta.status_code != 200:
            raise RuntimeError(f'Meta API {meta.status_code}: could not find the photo')
        info = meta.json()
        if int(info.get('file_size') or 0) > limit:
            raise RuntimeError('The photo is too big')
        photo = self.http.get(info['url'], headers=headers, timeout=TIMEOUT)
        if photo.status_code != 200 or len(photo.content) > limit:
            raise RuntimeError('Could not download the photo')
        return photo.content, str(info.get('mime_type') or 'image/jpeg')

    def upload(self, image):
        """Pictures are uploaded to Meta first, then sent by their media id."""
        data = self._call('/media', data={'messaging_product': 'whatsapp', 'type': image.mimetype},
                          files={'file': (image.filename or 'image.png', image.data, image.mimetype)})
        return data['id']

    def send_image(self, to, image, caption=''):
        media = {'id': self.upload(image)}
        if caption:
            media['caption'] = cut(caption, MAX_CAPTION)
        self.send_message(to, {'type': 'image', 'image': media})

    def send(self, to, reply):
        if reply.kind == 'text':
            self.send_message(to, {'type': 'text', 'text': {'body': reply.text, 'preview_url': True}})
        elif reply.kind == 'image':
            self.send_image(to, reply, reply.caption)
        elif reply.kind == 'menu':
            # Lists can't carry a picture, so a menu's picture goes first on its own.
            if reply.image:
                self.send_image(to, reply.image)
            interactive = interactive_for(reply)
            if interactive:
                self.send_message(to, {'type': 'interactive', 'interactive': interactive})
            else:
                self.send_message(to, {'type': 'text', 'text': {'body': menu_text(reply), 'preview_url': True}})
        else:
            raise ValueError(f'Unknown reply kind: {reply.kind}')


def fetch_photo(incoming, client):
    """For a photo message, the same Incoming with the photo's bytes (or as an
    unreadable message if Meta won't give it)."""
    if incoming.type != 'image' or incoming.image:
        return incoming
    try:
        data, mimetype = client.download_media(incoming.text)
    except Exception:
        logger.exception('Could not download a WhatsApp photo')
        return replace(incoming, type='other', text='')
    return replace(incoming, image=data, image_type=mimetype, text='')
