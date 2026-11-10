"""A chat to test with, like the Node tests' setup(): one user, a clock that
only moves when told, a fixed access code, and every turn through
handle_message, so the chat is saved to and read from the database each time."""
from datetime import datetime, timedelta, timezone

from bot.config import build_config
from bot.engine import Engine, Incoming
from bot.handler import handle_message
from bot.replies import Image, menu_text
from restrooms.geo import Location

NAIROBI = Location(-1.2864, 36.8172)
USER = 'user-1'
PHONE = '+254712345678'
# 10:00 in Nairobi.
START = datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc)


class ChatTester:
    def __init__(self, env=None, user=USER, **engine_args):
        self.config = build_config(env or {})
        self.user = user
        self.now = START
        self.engine_args = engine_args
        self.engine = self.new_engine()

    def new_engine(self):
        """A fresh engine on the same database and clock, as after a restart."""
        return Engine(self.config, clock=lambda: self.now, make_otp=lambda: '123456', **self.engine_args)

    def send(self, incoming):
        sent = []
        handle_message(incoming, sent.append, self.engine)
        return sent

    def say(self, text, **fields):
        return self.send(Incoming(self.user, 'text', text=text, phone=PHONE, **fields))

    def share_location(self, location=NAIROBI):
        return self.send(Incoming(self.user, 'location', location=location, phone=PHONE))

    def step(self):
        """The step the user's saved chat is on."""
        from bot.models import Conversation
        from bot.state import Step
        row = Conversation.objects.filter(user_id=self.user).first()
        step = (row.state or {}).get('step') if row else None
        return Step(step) if step else None

    def advance(self, minutes=0, milliseconds=0):
        self.now += timedelta(minutes=minutes, milliseconds=milliseconds)

    def reach_payment_summary(self, phone='0712345678'):
        """From "hi" to the payment summary with M-Pesa."""
        self.say('hi')
        self.share_location()
        self.say('1')  # the first restroom
        self.say('1')  # pay
        self.say('1')  # M-Pesa
        return self.say(phone)

    def pay_by_card(self, restroom='1'):
        """From "hi" to a paid journey with a card (no phone number needed)."""
        self.say('hi')
        self.share_location()
        self.say(restroom)
        self.say('1')  # pay
        self.say('3')  # card
        return self.say('1')  # authorize


def texts(replies):
    """Everything the user would read: texts, menus written out, picture captions."""
    parts = []
    for reply in replies:
        if reply.kind == 'text':
            parts.append(reply.text)
        elif reply.kind == 'menu':
            parts.append(menu_text(reply))
        else:
            parts.append(reply.caption)
    return '\n---\n'.join(part for part in parts if part)


def images(replies):
    """The pictures the user sees, including the one at the top of a menu."""
    found = []
    for reply in replies:
        if reply.kind == 'image':
            found.append(reply)
        elif reply.kind == 'menu' and reply.image:
            found.append(reply.image)
    return found


def last_menu(replies):
    return [reply for reply in replies if reply.kind == 'menu'][-1]


class FakePictures:
    """Every picture is a tiny "PNG" named after what was drawn. With
    behaviour 'fail' drawing raises, with 'none' there is no picture."""

    NAMES = ('restroom_thumb', 'payment_icon', 'restroom_photo', 'route_preview', 'route_map', 'access_card')

    def __init__(self, behaviour='ok'):
        self.behaviour = behaviour

    def __getattr__(self, name):
        if name not in self.NAMES:
            raise AttributeError(name)

        def draw(*args):
            if self.behaviour == 'fail':
                raise RuntimeError('drawing failed')
            if self.behaviour == 'none':
                return None
            return Image(b'png', 'image/png', f'{name}.png')

        return draw
