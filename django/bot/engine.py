"""The conversation, step by step.

The engine knows nothing about WhatsApp or HTTP: it takes one incoming
message and the user's chat (a ChatStore, see sessions.py) and returns the
replies to send (see replies.py). bot.handler calls it for every channel.

`pictures` (optional) draws the pictures from the demo video. Without it the
bot sends text only. It offers restroom_thumb(facility), payment_icon(method),
restroom_photo(facility), route_preview(location, facility),
route_map(location, facility) and access_card(access_pass, qr_png), each
returning an Image or None.
"""
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP
from typing import NamedTuple

from django.utils import timezone

from payments.checkout import build_checkout, to_paypal_amount
from payments.methods import payment_methods
from payments.mock import ChargeResult, MockPayments
from payments.passes import PassStore
from payments.refunds import refund_visit
from pictures.qr import qr_image
from restrooms.finder import RestroomFinder
from restrooms.geo import Location, is_valid_location, walking_minutes
from restrooms.hours import is_open_on_arrival, next_closing

from . import records
from .ai import places
from .ai.concierge import NEED_KEYS as AI_NEEDS
from .ai.cleaning import CleaningJudge
from .ai.concierge import Concierge
from .ai.gemini import build_gemini
from .ai.triage import Triage
from .directions import directions_url
from .formatting import CENT, JS_SPACE, js_trim, fee_for, format_date_time, iso_utc, normalize_phone
from .messages import Messages
from .replies import Menu, Option, Text
from .state import PaymentDraft, Step

logger = logging.getLogger(__name__)

# Once paid, a new location must not throw the user back to the restroom list.
PAID_STEPS = frozenset({Step.NAVIGATING, Step.ARRIVED, Step.ACCESS, Step.REPORT})
# The steps that need a pass that still works.
PASS_STEPS = frozenset({Step.NAVIGATING, Step.ARRIVED, Step.ACCESS})
# Steps between choosing a restroom and paying for it.
PAYING_STEPS = frozenset({
    Step.CONFIRM_PAY, Step.CHOOSE_METHOD, Step.AWAIT_PHONE, Step.AUTHORIZE, Step.AWAIT_CHECKOUT,
    Step.PAYMENT_FAILED,
})
# Paid on PayPal's page when PayPal is set up (cards too, as PayPal guest checkout).
CHECKOUT_METHODS = frozenset({'paypal', 'card'})

GREETINGS = frozenset({'hi', 'hello', 'hey', 'hi there', 'start', 'menu', 'restart', 'jambo', 'habari'})
EXITS = frozenset({'exit', 'stop', 'cancel', 'quit', 'bye', 'goodbye'})
PASS_WORDS = frozenset({'pass', 'my pass', 'ticket'})
HELP_WORDS = frozenset({'help'})

BACK = Option('0', 'Back', secondary=True)
# What the user can look for, as shown, and the key the restroom search
# uses (restrooms.finder.NEED_FILTERS).
NEEDS = [
    'The most clean',
    'The most convenient',
    'The most accessible',
    'Toilet for the disabled',
    'Washroom to change baby diapers',
]
NEED_KEYS = dict(zip(NEEDS, ['clean', 'convenient', 'accessible', 'disabled', 'baby']))
EXIT = Option('exit', 'Exit', secondary=True, text_hint='Reply *exit* to leave.')

PROVIDER_ERROR = 'The payment could not be completed right now.'
# Where a request for restrooms near a place may start a new search.
SEARCH_STEPS = frozenset({None, Step.CHOOSE_NEED, Step.AWAIT_LOCATION, Step.CHOOSE_RESTROOM, Step.CONFIRM_PAY,
                          Step.PASS_EXPIRED})

KEYCAP = re.compile('[\ufe0f\u20e3]')
CHOICE = re.compile('([0-9]{1,2})[.)]?')
NUMBER = re.compile('[0-9]+')
SPACES = re.compile(f'{JS_SPACE}+')
TRAILING_PUNCTUATION = re.compile('[!.,?]+$')
# Commands are short, so longer messages are never read as one.
MAX_COMMAND_LENGTH = 40


@dataclass(frozen=True)
class Incoming:
    """One message from a user, whatever the channel."""
    user_id: str
    type: str  # 'text', 'location', 'image' or 'other' (a sticker, a voice note)
    text: str = ''
    image: bytes = b''  # a photo's bytes, for type 'image'
    image_type: str = ''  # its MIME type, e.g. image/jpeg
    location: Location | None = None
    phone: str = ''  # the user's WhatsApp number, when the channel knows it
    received_at: datetime | None = None  # when it arrived, to spot double sends
    channel: str = 'web'  # web, bridge or cloud
    base_url: str = ''  # this server's address as the user's browser sees it


class PassEnd(NamedTuple):
    at: datetime
    at_closing: bool  # cut short by the restroom's closing time


def parse_choice(text):
    """The option number typed: "1", "1.", "1)", " 2 " and the keycap emoji for 1 all give 1."""
    match = CHOICE.fullmatch(js_trim(KEYCAP.sub('', text)))
    return int(match[1]) if match else None


def normalize_words(text):
    """'Hi!' gives 'hi', 'help?' gives 'help', '  My   Pass ' gives 'my pass'."""
    if len(text) > MAX_COMMAND_LENGTH:
        return ''
    return TRAILING_PUNCTUATION.sub('', js_trim(SPACES.sub(' ', text.lower())))


def choices_in(replies):
    """The names of the numbered choices in the last menu of the replies, so
    typing a name ("back", "cancel") works like its number: {'back': '0'}.
    Titles too long to be typed as a command are left out."""
    menus = [reply for reply in replies if reply.kind == 'menu']
    if not menus:
        return {}
    names = {normalize_words(option.title): option.id for option in menus[-1].options if NUMBER.fullmatch(option.id)}
    names.pop('', None)
    return names


def describes_problem(text):
    """A problem report has to say something: "99" or a thumbs-up does not."""
    return any(char.isalpha() for char in text)


def default_otp():
    return f'{secrets.randbelow(1_000_000):06d}'


class Engine:
    def __init__(self, config, *, restrooms=None, payments=None, passes=None, pictures=None,
                 clock=timezone.now, make_otp=default_otp, checkout=None, concierge=None, triage=None,
                 find_place=None, cleaning_judge=None):
        self.config = config
        self.restrooms = restrooms or RestroomFinder(config)
        self.payments = payments or MockPayments()
        # PayPal's page for cards and PayPal; None keeps them simulated.
        self.checkout = checkout if checkout is not None else build_checkout(config)
        # Gemini: understands free text and reviews problem reports. None:
        # the chat runs on its menus, as before.
        gemini = build_gemini(config) if None in (concierge, triage, cleaning_judge) else None
        self.concierge = concierge if concierge is not None else (Concierge(gemini) if gemini else None)
        self.triage = triage if triage is not None else (Triage(gemini) if gemini else None)
        # Checks cleaners' photos before a cleaning bonus is paid (ops.cleaning).
        self.cleaning_judge = (cleaning_judge if cleaning_judge is not None
                               else (CleaningJudge(gemini) if gemini else None))
        self.find_place = find_place or places.find_place
        self.passes = passes or PassStore(clock)
        self.pictures = pictures
        self.clock = clock
        self.make_otp = make_otp
        self.msg = Messages(config)
        self.methods = payment_methods(config)

    def handle(self, incoming, store):
        """The replies to one message from the user whose chat is `store`."""
        replies = self._respond(incoming, store)
        # Remember the choices now on screen, for users who type a choice's name.
        session = store.get()
        if session:
            self._note_channel(session, incoming)
            session.choices = choices_in(replies)
        return replies

    # ---- events that don't come from a chat message ----

    def on_checkout_return(self, store, order_id):
        """The buyer came back from PayPal's page (or PayPal's webhook said the
        order was approved). The replies to send to the chat, or None when the
        chat is no longer waiting for this order: then nothing is captured, so
        the buyer is never charged for a payment the chat has given up on."""
        session = store.get()
        if not self._waiting_for(session, order_id):
            return None
        replies = self._finish_checkout(store, session)
        session.choices = choices_in(replies)
        return replies

    def on_checkout_cancel(self, store, order_id):
        """The buyer pressed cancel on PayPal's page."""
        session = store.get()
        if not self._waiting_for(session, order_id):
            return None
        self.checkout.cancel(order_id)
        session.step = Step.CHOOSE_METHOD
        replies = [Text(self.msg.checkout_cancelled()), self._method_menu()]
        session.choices = choices_in(replies)
        return replies

    def _waiting_for(self, session, order_id):
        return bool(
            self.checkout and session and session.step == Step.AWAIT_CHECKOUT
            and session.payment and session.payment.order_id == order_id
        )

    def _respond(self, incoming, store):
        session = store.get()
        if session:
            store.touch()
            session.whatsapp_phone = normalize_phone(incoming.phone, self.config.country_code)
            self._note_channel(session, incoming)

        if incoming.type == 'location':
            return self._on_location(incoming, store, session)
        if incoming.type == 'image':
            cleaning = self._cleaning_photo(incoming)
            if cleaning is not None:
                return cleaning

        raw = js_trim(incoming.text) if incoming.type == 'text' else ''
        if not raw:
            # A sticker, a voice note, a photo.
            if not session:
                return self._restart(incoming, store, '')
            return [Text(self.msg.text_only()), *self._prompt(session)]

        words = normalize_words(raw)
        named = session.choices.get(words) if session else None
        if named is not None:
            return self._on_reply(store, session, named)
        if words in GREETINGS:
            return self._start_conversation(store, incoming.phone)
        # "cancel" while writing a problem report only leaves the report.
        if words in EXITS and not (words == 'cancel' and session and session.step == Step.REPORT):
            return self._exit(store)
        if words in PASS_WORDS:
            return self._show_pass(store, session)
        if words in HELP_WORDS:
            return [Text(self.msg.help()), *(self._prompt(session) if session else [])]
        if not session:
            return self._restart(incoming, store, raw)
        return self._on_reply(store, session, raw)

    def _restart(self, incoming, store, raw):
        # A chat that timed out mid-way is explained, not silently restarted.
        # Only an answer uses this up: after "help" or "pass", the next answer
        # still hears that the chat timed out.
        old = store.take_timed_out()
        if old:
            return self._after_timeout(store, incoming.phone, old, raw)
        return self._start_conversation(store, incoming.phone, raw)

    def _draw(self, make):
        """A picture, or None when there are no pictures or drawing fails (no
        internet for the map, for example). The chat never depends on a picture."""
        if not self.pictures:
            return None
        try:
            return make(self.pictures) or None
        except Exception:
            logger.exception('Could not draw a picture')
            return None

    def _captioned(self, make, caption):
        """A picture with a caption, or the caption alone."""
        image = self._draw(make)
        return image.with_caption(caption) if image else Text(caption)

    @staticmethod
    def _note_channel(session, incoming):
        """Remember where the user writes from, for replies sent later."""
        session.channel = incoming.channel
        if incoming.base_url:
            session.base_url = incoming.base_url

    def _new_session(self, store, phone, opening=None):
        session = store.start()
        session.whatsapp_phone = normalize_phone(phone, self.config.country_code)
        out = [Text(opening or self.msg.greeting())]
        out += [Text(self.msg.active_pass_reminder(access_pass)) for access_pass in self.passes.active(store.user_id)]
        return session, out

    def _start_conversation(self, store, phone, raw=''):
        session, out = self._new_session(store, phone)
        session.step = Step.CHOOSE_NEED
        # A first message that already says what is needed ("clean toilet
        # near Kencom") is answered at once by the AI concierge.
        if raw and self.concierge and len(raw.split()) >= 2:
            return [*out, *self._understand(store, session, raw)]
        return [*out, self._need_menu()]

    def _after_timeout(self, store, phone, old, raw):
        # The user answered a question from a chat that has timed out. Say so
        # (and that nothing was charged) before starting again. A problem
        # report they were writing is still saved.
        out = []
        if old.step == Step.REPORT and describes_problem(raw):
            self._save_report(store, old, raw)
            out.append(Text(self.msg.report_thanks()))
        session, started = self._new_session(store, phone, self.msg.timed_out(old.step in PAYING_STEPS))
        session.step = Step.CHOOSE_NEED
        return [*out, *started, self._need_menu()]

    def _on_location(self, incoming, store, session):
        location = incoming.location
        if not (location and is_valid_location(location.lat, location.lng)):
            if not session:
                return self._start_conversation(store, incoming.phone)
            # Repeat the question the user is on, so its choices still do what they say.
            return [Text(self.msg.bad_location()), *self._prompt(session)]
        if not session:
            session, out = self._new_session(store, incoming.phone)
            session.location = location
            return [*out, *self._show_restrooms(store, session)]
        # After paying, the journey goes on, but the new location is kept:
        # another restroom is then looked for from where the user is now.
        session.location = location
        if session.step in PAID_STEPS:
            return [Text(self.msg.location_noted()), *self._prompt(session)]
        return self._show_restrooms(store, session)

    def _show_restrooms(self, store, session):
        # A restroom the user already has a working pass for is left out, so
        # it is never paid for twice.
        paid_for = {access_pass.facility_code for access_pass in self.passes.active(store.user_id)}
        need = NEED_KEYS.get(session.need, '')
        session.options = self.restrooms.nearest(session.location, at=self.clock(), leave_out=paid_for, need=need)
        note = []
        if not session.options and need:
            # None nearby offers it: say so and show the closest instead.
            session.options = self.restrooms.nearest(session.location, at=self.clock(), leave_out=paid_for)
            if session.options:
                # Some do, but not at this time: say they are closed instead.
                closed = bool(self.restrooms.nearest(session.location, leave_out=paid_for, need=need))
                note = [Text(self.msg.need_closed(session.need) if closed else self.msg.no_match(session.need))]
        if not session.options:
            session.step = Step.AWAIT_LOCATION
            if paid_for and self.restrooms.nearest(session.location, at=self.clock()):
                return [Text(self.msg.all_paid_for())]
            # Without the time, closed restrooms count too: are there any at all?
            any_nearby = bool(self.restrooms.nearest(session.location))
            return [Text(self.msg.all_closed() if any_nearby else self.msg.none_nearby())]
        session.step = Step.CHOOSE_RESTROOM
        return [*note, self._restroom_menu(session)]

    # Menus. with_map=False is used when repeating a question, so the map is
    # not sent again.

    def _need_menu(self):
        return Menu(self.msg.ask_need(), [*(Option(str(n), need) for n, need in enumerate(NEEDS, start=1)), EXIT])

    def _location_menu(self, question=None):
        return Menu(question or self.msg.ask_location(), [Option('1', 'Continue'), Option('2', 'Exit', secondary=True), BACK])

    def _restroom_menu(self, session):
        choices = [
            Option(
                str(number),
                facility.name,
                description=self.msg.restroom_details(facility, now=self.clock()),
                icon=self._draw(lambda pictures: pictures.restroom_thumb(facility)),
            )
            for number, facility in enumerate(session.options, start=1)
        ]
        return Menu(self.msg.choose_restroom(), [*choices, EXIT])

    def _pay_menu(self, session, with_map=False):
        facility = session.restroom
        image = self._draw(lambda pictures: pictures.route_preview(session.location, facility)) if with_map else None
        return Menu(
            self.msg.pay_prompt(facility, now=self.clock()),
            [Option('1', 'Pay'), Option('2', 'Exit'), Option('3', 'Choose a different restroom')],
            image=image,
        )

    def _method_menu(self):
        choices = [
            Option(
                str(number),
                method.label,
                description=self.msg.method_fee(method),
                icon=self._draw(lambda pictures: pictures.payment_icon(method)),
            )
            for number, method in enumerate(self.methods, start=1)
        ]
        return Menu(self.msg.choose_payment_method(), [*choices, BACK])

    def _phone_menu(self, session, question=None):
        choices = [Option('1', self.msg.use_whatsapp_number(session.whatsapp_phone))] if session.whatsapp_phone else []
        return Menu(
            question or self.msg.ask_phone(session.method), [*choices, BACK], input_placeholder='0712 345 678'
        )

    def _uses_checkout(self, method):
        return bool(self.checkout) and method.id in CHECKOUT_METHODS

    def _summary_menu(self, session):
        draft = session.payment
        if self._uses_checkout(draft.method):
            text = self.msg.payment_summary(
                draft, self.checkout.is_test_mode, to_paypal_amount(draft.total, self.config.paypal_rate)
            )
            return Menu(text, [Option('1', 'Pay with PayPal'), Option('2', 'Cancel', secondary=True)])
        return Menu(
            self.msg.payment_summary(draft, self.payments.is_test_mode),
            [Option('1', 'Authorize payment'), Option('2', 'Cancel', secondary=True)],
        )

    def _checkout_menu(self, session, note=None):
        text = self.msg.checkout_link(session.payment, self.checkout.is_test_mode)
        return Menu(f'{note}\n\n{text}' if note else text, [
            Option('1', 'I have paid'),
            Option('2', 'Pay another way', secondary=True),
        ])

    def _failed_menu(self, session):
        return Menu(self.msg.payment_failed(session.fail_reason), [
            Option('1', 'Try again'),
            Option('2', 'Choose another payment method'),
            Option('3', 'Exit', secondary=True),
        ])

    def _journey_menu(self, session, with_map=False):
        facility = session.restroom
        image = self._draw(lambda pictures: pictures.route_map(session.location, facility)) if with_map else None
        return Menu(
            self.msg.start_journey(directions_url(facility)),
            [
                Option('1', 'I have arrived'),
                Option('2', 'Report a location problem', secondary=True),
                Option('3', 'Choose a different restroom', secondary=True),
            ],
            image=image,
        )

    def _arrived_menu(self, session):
        return Menu(self.msg.arrived(session.restroom), [
            Option('1', 'Unlock / enter'),
            Option('2', 'Report a location problem', secondary=True),
            Option('3', 'Choose a different restroom', secondary=True),
        ])

    @staticmethod
    def _access_choices():
        return [
            Option('1', 'View QR code'),
            Option('2', 'View OTP'),
            Option('3', 'Done'),
            Option('4', 'Report a problem', secondary=True),
        ]

    def _report_menu(self):
        return Menu(self.msg.ask_report(), [BACK], input_placeholder='Describe the problem')

    def _expired_menu(self, session):
        access_pass = self._pass_of(session)
        payment = self.passes.payment_for(access_pass.reference) if access_pass else None
        refunded = bool(payment and payment.status == 'refunded')
        text = self.msg.pass_refunded(access_pass) if refunded else self.msg.pass_expired(access_pass)
        return Menu(text, [
            Option('1', 'Report a problem'),
            Option('2', 'Find a restroom'),
            Option('3', 'Exit', secondary=True),
        ])

    def _pass_of(self, session):
        """The access pass of the journey under way."""
        return self.passes.find(session.pass_reference) if session.pass_reference else None

    def _pass_ran_out(self, session):
        # A journey can't go on once its pass has run out: switch to saying
        # so, so no menu offers to unlock with an expired pass. True if it switched.
        if session.step not in PASS_STEPS or self.passes.is_active(self._pass_of(session)):
            return False
        session.step = Step.PASS_EXPIRED
        return True

    def _prompt(self, session):
        """The question for the step the user is on, asked again."""
        self._pass_ran_out(session)
        match session.step:
            case Step.CHOOSE_NEED:
                return [self._need_menu()]
            case Step.CHOOSE_RESTROOM:
                return [self._restroom_menu(session)]
            case Step.CONFIRM_PAY:
                return [self._pay_menu(session)]
            case Step.CHOOSE_METHOD:
                return [self._method_menu()]
            case Step.AWAIT_PHONE:
                return [self._phone_menu(session)]
            case Step.AUTHORIZE:
                return [self._summary_menu(session)]
            case Step.AWAIT_CHECKOUT:
                return [self._checkout_menu(session)]
            case Step.PAYMENT_FAILED:
                return [self._failed_menu(session)]
            case Step.NAVIGATING:
                return [self._journey_menu(session)]
            case Step.ARRIVED:
                return [self._arrived_menu(session)]
            case Step.ACCESS:
                return [Menu(self.msg.what_next(), self._access_choices())]
            case Step.REPORT:
                return [self._report_menu()]
            case Step.PASS_EXPIRED:
                return [self._expired_menu(session)]
            case _:
                return [self._location_menu()]

    def _did_not_understand(self, session):
        return [Text(self.msg.did_not_understand()), *self._prompt(session)]

    def _exit(self, store):
        store.end()
        active = self.passes.active(store.user_id)
        return [Text(self.msg.goodbye(active[-1] if active else None))]

    def _on_reply(self, store, session, raw, understood=False):
        choice = parse_choice(raw)
        # The user hasn't seen that the pass ran out yet: tell them first.
        if self._pass_ran_out(session):
            return self._prompt(session)

        match session.step:
            case Step.CHOOSE_NEED:
                if choice is not None and 1 <= choice <= len(NEEDS):
                    session.need = NEEDS[choice - 1]
                    session.step = Step.AWAIT_LOCATION
                    return [Text(self.msg.need_noted(session.need)), self._location_menu()]

            case Step.AWAIT_LOCATION:
                if choice == 2:
                    return self._exit(store)
                if choice == 0:
                    # Back to "What are you looking for?".
                    session.step = Step.CHOOSE_NEED
                    return [self._need_menu()]
                if not session.location:
                    return [self._location_menu(self.msg.need_location())]
                if choice == 1:
                    return self._show_restrooms(store, session)

            case Step.CHOOSE_RESTROOM:
                if choice is not None and 1 <= choice <= len(session.options):
                    session.restroom = session.options[choice - 1]
                    session.step = Step.CONFIRM_PAY
                    return [self._pay_menu(session, with_map=True)]

            case Step.CONFIRM_PAY:
                if choice == 1:
                    session.step = Step.CHOOSE_METHOD
                    return [self._method_menu()]
                if choice == 2:
                    return self._exit(store)
                if choice == 3:
                    session.step = Step.CHOOSE_RESTROOM
                    return [self._restroom_menu(session)]

            case Step.CHOOSE_METHOD:
                if choice == 0:
                    session.step = Step.CONFIRM_PAY
                    return self._prompt(session)
                if choice is not None and 1 <= choice <= len(self.methods):
                    session.method = self.methods[choice - 1]
                    if session.method.id == 'tfpass':
                        # The pass is already paid for: no summary to confirm, the visit is taken from it.
                        self._prepare_payment(session, None)
                        return self._authorize(store, session)
                    if not session.method.needs_phone:
                        return self._prepare_payment(session, None)
                    session.step = Step.AWAIT_PHONE
                    return self._prompt(session)

            case Step.AWAIT_PHONE:
                if choice == 0:
                    session.step = Step.CHOOSE_METHOD
                    return self._prompt(session)
                if choice == 1 and session.whatsapp_phone:
                    return self._prepare_payment(session, session.whatsapp_phone)
                phone = normalize_phone(raw, self.config.country_code)
                if not phone:
                    return [self._phone_menu(session, self.msg.bad_phone(session.method))]
                return self._prepare_payment(session, phone)

            case Step.AUTHORIZE:
                if choice == 1:
                    return self._authorize(store, session)
                if choice == 2:
                    session.step = Step.CONFIRM_PAY
                    return self._prompt(session)

            case Step.AWAIT_CHECKOUT:
                if choice == 1:
                    return self._finish_checkout(store, session)
                if choice == 2:
                    self.checkout.cancel(session.payment.order_id)
                    session.step = Step.CHOOSE_METHOD
                    return self._prompt(session)

            case Step.PAYMENT_FAILED:
                if choice == 1:
                    return self._authorize(store, session)
                if choice == 2:
                    session.step = Step.CHOOSE_METHOD
                    return self._prompt(session)
                if choice == 3:
                    return self._exit(store)

            # "Choose a different restroom" means paying again; the pass
            # already bought stays valid.
            case Step.NAVIGATING:
                if choice == 1:
                    session.step = Step.ARRIVED
                    return self._prompt(session)
                if choice == 2:
                    return self._ask_report(session)
                if choice == 3:
                    return self._show_restrooms(store, session)

            case Step.ARRIVED:
                if choice == 1:
                    return self._show_access(session)
                if choice == 2:
                    return self._ask_report(session)
                if choice == 3:
                    return self._show_restrooms(store, session)

            case Step.ACCESS:
                access_pass = self._pass_of(session)
                if choice == 1:
                    return [self._access_qr(access_pass), Menu(self.msg.what_next(), self._access_choices())]
                if choice == 2:
                    return [Menu(self.msg.otp(access_pass), self._access_choices())]
                if choice == 3:
                    store.end()
                    return [Text(self.msg.done(access_pass))]
                if choice == 4:
                    return self._ask_report(session)

            case Step.PASS_EXPIRED:
                if choice == 1:
                    return self._ask_report(session)
                if choice == 2:
                    return self._show_restrooms(store, session)
                if choice == 3:
                    return self._exit(store)

            case Step.REPORT:
                if choice == 0 or normalize_words(raw) == 'cancel':
                    session.step = session.return_step
                    return self._prompt(session)
                if not describes_problem(raw):
                    return [Text(self.msg.describe_in_words()), self._report_menu()]
                report = self._save_report(store, session, raw)
                session.step = session.return_step
                return self._after_report(store, session, report, raw)

            case _:
                return self._start_conversation(store, session.whatsapp_phone or '')

        # Not a choice on screen: the AI concierge may know what was meant.
        return self._did_not_understand(session) if understood else self._understand(store, session, raw)

    def _pass_end(self, facility, start):
        # A pass bought at `start` ends after the walk there and then
        # ACCESS_MINUTES (so a restroom further away can still be used), or
        # when the restroom closes, if that comes first.
        full = start + timedelta(minutes=walking_minutes(facility.distance_km) + self.config.access_minutes)
        closes = next_closing(facility.hours, start, self.config.timezone)
        return PassEnd(closes, True) if closes and closes < full else PassEnd(full, False)

    def _prepare_payment(self, session, phone):
        facility = session.restroom
        fee = fee_for(facility.price, session.method.fee_percent)
        end = self._pass_end(facility, self.clock())
        session.payment = PaymentDraft(
            facility=facility,
            method=session.method,
            phone=phone,
            price=facility.price,
            fee=fee,
            total=(facility.price + fee).quantize(CENT, ROUND_HALF_UP),
            closes_at=end.at if end.at_closing else None,
        )
        session.step = Step.AUTHORIZE
        return [self._summary_menu(session)]

    def _authorize(self, store, session):
        draft = session.payment
        facility = draft.facility
        # The list may be a while old: never charge for a restroom that will
        # be closed by the time the user gets there.
        if not is_open_on_arrival(facility, self.clock(), self.config.timezone):
            return [Text(self.msg.closed_on_arrival(facility)), *self._show_restrooms(store, session)]
        if draft.method.id == 'tfpass':
            return self._use_tfpass(store, session)
        if self._uses_checkout(draft.method):
            return self._start_checkout(store, session)
        try:
            result = self.payments.charge(
                user_id=store.user_id,
                method=draft.method.id,
                phone=draft.phone,
                amount=draft.total,
                currency=self.config.currency,
                facility_code=facility.code,
            )
        except Exception:
            logger.exception('The payment provider failed')
            result = ChargeResult(ok=False, reason=PROVIDER_ERROR)

        if not result.ok:
            return self._payment_failed(store, session, result.reason, self.payments.name)
        return self._paid(store, session, result.reference, self.payments.name)

    def _use_tfpass(self, store, session):
        """Use one visit from the visitor's TapFlash Pass, or offer one (bought once with PayPal)."""
        from payments import tfpass
        reference = tfpass.use_visit(store.user_id)
        if reference is None:
            offer = tfpass.offer(store.user_id, session.channel, self.config)
            base_url = (self.config.public_url or session.base_url or '').rstrip('/')
            session.step = Step.CHOOSE_METHOD
            return [Text(self.msg.tfpass_offer(offer, f'{base_url}/tfpass/{offer.token}')), self._method_menu()]
        return self._paid(store, session, reference, 'tfpass')

    def _payment_failed(self, store, session, reason, provider):
        session.step = Step.PAYMENT_FAILED
        session.fail_reason = reason
        records.save_failed_payment(
            user_id=store.user_id,
            draft=session.payment,
            provider=provider,
            currency=self.config.currency,
            reason=reason,
            at=self.clock(),
        )
        return [self._failed_menu(session)]

    def _start_checkout(self, store, session):
        """Send a PayPal link; the visit is paid once PayPal confirms."""
        draft = session.payment
        base_url = self.config.public_url or session.base_url
        try:
            if not base_url:
                raise ValueError('PUBLIC_URL is not set, so PayPal has no page to send the buyer back to')
            link = self.checkout.start(
                user_id=store.user_id, channel=session.channel, draft=draft,
                local_currency=self.config.currency, base_url=base_url,
            )
        except Exception:
            logger.exception('Could not create the PayPal order')
            return self._payment_failed(store, session, PROVIDER_ERROR, self.checkout.name)
        draft.order_id = link.order_id
        draft.checkout_url = link.url
        draft.checkout_amount = str(link.amount)
        draft.checkout_currency = link.currency
        session.step = Step.AWAIT_CHECKOUT
        return [self._checkout_menu(session)]

    def _finish_checkout(self, store, session):
        draft = session.payment
        try:
            result = self.checkout.finish(draft.order_id)
        except Exception:
            logger.exception('Could not finish the PayPal order %s', draft.order_id)
            result = ChargeResult(ok=False, waiting=True, reason=PROVIDER_ERROR)
        if result.waiting:
            return [self._checkout_menu(session, self.msg.checkout_not_finished())]
        if not result.ok:
            return self._payment_failed(store, session, result.reason, self.checkout.name)
        replies = self._paid(store, session, result.reference, self.checkout.name)
        self.checkout.attach_payment(draft.order_id, self.passes.payment_for(result.reference))
        return replies

    def _fresh_otp(self, facility_code, now):
        """An access code no other valid pass at this restroom has, so the code the
        attendant types finds exactly one visitor."""
        from payments.models import AccessPass
        otp = self.make_otp()
        for _ in range(10):
            taken = AccessPass.objects.filter(payment__facility_code=facility_code, otp=otp, expires_at__gt=now)
            if not taken.exists():
                break
            otp = self.make_otp()
        return otp

    def _paid(self, store, session, reference, provider):
        """The payment went through: the access pass, the receipt and the way there."""
        draft = session.payment
        facility = draft.facility
        paid_at = self.clock()
        access_pass = self.passes.issue(
            user_id=store.user_id,
            draft=draft,
            reference=reference,
            provider=provider,
            currency=self.config.currency,
            otp=self._fresh_otp(facility.code, paid_at),
            issued_at=paid_at,
            expires_at=self._pass_end(facility, paid_at).at,
        )
        session.pass_reference = access_pass.reference
        # The chat stays open for the whole walk, however long.
        store.keep_open_until(access_pass.expires_at)
        session.step = Step.NAVIGATING
        receipt = qr_image(
            self.msg.receipt_text(access_pass, format_date_time(paid_at, self.config.timezone)),
            f'receipt-{access_pass.reference}.png',
        )
        return [
            Text(self.msg.payment_received(access_pass.reference, provider)),
            *([receipt] if receipt else []),
            self._captioned(lambda pictures: pictures.restroom_photo(facility), self.msg.searching_route(facility)),
            self._journey_menu(session, with_map=True),
        ]

    def _ask_report(self, session):
        session.return_step = session.step
        session.step = Step.REPORT
        return [self._report_menu()]

    def _access_qr(self, access_pass):
        payload = ['TAPFLASH-ACCESS', access_pass.reference, access_pass.facility_code, access_pass.otp,
                   iso_utc(access_pass.expires_at)]
        return qr_image('|'.join(payload), f'access-{access_pass.reference}.png')

    def _access_card(self, access_pass):
        """The access card (which shows the details and the QR code) and its
        caption, or a plain QR code and the details as text if the card can't be drawn."""
        qr = self._access_qr(access_pass)
        if qr is None:
            return None, self.msg.access_details(access_pass)
        card = self._draw(lambda pictures: pictures.access_card(access_pass, qr.data))
        if card:
            return card, self.msg.access_card_caption(access_pass)
        return qr, self.msg.access_details(access_pass)

    def _show_access(self, session):
        session.step = Step.ACCESS
        image, text = self._access_card(self._pass_of(session))
        return [Menu(text, self._access_choices(), image=image)]

    def _show_pass(self, store, session):
        """Every pass that still works, or when the last one ran out."""
        active = self.passes.active(store.user_id)
        rest = self._prompt(session) if session else []
        if not active:
            # The expired pass menu already says when it ran out.
            if session and session.step == Step.PASS_EXPIRED:
                return rest
            last = self.passes.latest(store.user_id)
            return [Text(self.msg.pass_ended(last) if last else self.msg.no_pass()), *rest]
        cards = [self._access_card(access_pass) for access_pass in active]
        return [*(image.with_caption(text) if image else Text(text) for image, text in cards), *rest]

    def _save_report(self, store, session, message):
        access_pass = self._pass_of(session)
        return records.save_report(
            payment=self.passes.payment_for(access_pass.reference) if access_pass else None,
            user_id=store.user_id,
            phone=session.whatsapp_phone,
            facility=session.restroom,
            step=session.return_step,
            message=message,
            at=self.clock(),
        )

    def _cleaning_photo(self, incoming):
        """A photo from a registered cleaner is a cleaning check (see
        ops.cleaning). None for anyone else, whose photo is answered as before."""
        from ops.cleaning import check_photo, cleaner_for

        cleaner = cleaner_for(incoming.phone, self.config)
        if cleaner is None:
            return None
        text = check_photo(
            cleaner=cleaner, user_id=incoming.user_id, channel=incoming.channel, photo=incoming.image,
            mimetype=incoming.image_type, judge=self.cleaning_judge, checkout=self.checkout, config=self.config,
            now=self.clock(),
        )
        return [Text(f'{self.config.bot_name} cleaning check, {cleaner.restroom.name}: {text}')]

    # ---- AI ----

    def _understand(self, store, session, raw):
        """A message the menus can't read: ask the AI concierge what it means
        and do that, with the same rules as a tapped choice."""
        if not self.concierge or not describes_problem(raw):
            return self._did_not_understand(session)
        menu = sorted(session.choices.items(), key=lambda item: int(item[1]) if item[1].isdigit() else 99)
        try:
            decision = self.concierge.decide(session, [f'{number}. {title}' for title, number in menu], raw)
        except Exception:
            logger.exception('The AI concierge failed')
            decision = None
        if not decision:
            return self._did_not_understand(session)
        name, args = decision
        reply = clean_ai_text(args.get('reply'), 240)
        lead = [Text(reply)] if reply else []
        searching = session.step in SEARCH_STEPS
        if name == 'set_need' and args.get('need') in AI_NEEDS and searching:
            session.need = NEEDS[AI_NEEDS.index(args['need'])]
            return [*lead, *self._go_near(store, session, args.get('place'))]
        if name == 'search_near' and args.get('place') and searching:
            return [*lead, *self._go_near(store, session, args['place'])]
        if name == 'pick_option':
            number = str(args.get('number', '')).split('.')[0]
            if number in session.choices.values():
                return [*lead, *self._on_reply(store, session, number, understood=True)]
        answer = clean_ai_text(args.get('answer'), 600)
        if name == 'answer_question' and answer:
            return [Text(answer), *self._prompt(session)]
        if name == 'report_problem':
            # The triage answers the report itself, so the concierge's line is not sent.
            return self._report_from_chat(store, session, raw)
        return [Text(reply or self.msg.did_not_understand()), *self._prompt(session)]

    def _go_near(self, store, session, place):
        """Restrooms near a place the user named, or the question for their location."""
        found = self.find_place(place) if place else None
        if not found:
            session.step = Step.AWAIT_LOCATION
            note = [Text(self.msg.place_not_found(place))] if place else []
            return [*note, self._location_menu()]
        session.location, label = found
        return [Text(self.msg.searching_near(label)), *self._show_restrooms(store, session)]

    def _report_from_chat(self, store, session, raw):
        """A complaint written mid-chat: saved and reviewed like a report."""
        if session.step == Step.REPORT:
            return self._on_reply(store, session, raw, understood=True)
        session.return_step = session.step
        report = self._save_report(store, session, raw)
        return self._after_report(store, session, report, raw)

    def _after_report(self, store, session, report, raw):
        """The AI's review and any refund; a plain thank-you only when there is no review."""
        reviewed = self._triage_report(store, session, report, raw)
        thanks = [] if reviewed else [Text(self.msg.report_thanks())]
        return [*thanks, *reviewed, *self._prompt(session)]

    def _triage_report(self, store, session, report, text):
        """The AI reviews a report about a paid visit and may refund it, within the rules."""
        access_pass = self._pass_of(session)
        payment = self.passes.payment_for(access_pass.reference) if access_pass else None
        if not (self.triage and report and payment):
            return []
        now = self.clock()
        try:
            decision = self.triage.decide(
                report=text, facility_name=payment.facility_name, step=str(session.return_step or ''),
                minutes_since_paying=max(int((now - payment.created_at).total_seconds() // 60), 0),
            )
        except Exception:
            logger.exception('The AI triage failed')
            decision = None
        if not decision:
            return []
        refund, held_back = refund_visit(payment=payment, report=report, decision=decision,
                                         checkout=self.checkout, config=self.config, now=now)
        records.note_triage(report, decision, refund, held_back)
        reply = clean_ai_text(decision['reply'], 400)
        out = [Text(reply)] if reply else []
        if refund:
            out.append(Text(self.msg.refunded(refund, payment)))
            if decision['refund'] == 'full':
                # The visit is given back, so its pass ends: find another restroom.
                self.passes.end(access_pass.reference, now)
        elif decision['refund'] != 'none':
            out.append(Text(self.msg.refund_for_staff()))
        return out


LINK = re.compile(r'https?://\S+')


def clean_ai_text(value, limit):
    """The model's text on one line, without anything that looks like a link."""
    text = ' '.join(str(value or '').split())
    return LINK.sub('', text)[:limit].strip()
