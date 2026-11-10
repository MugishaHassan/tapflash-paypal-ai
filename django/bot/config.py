"""TapFlash settings, read from environment variables (and the .env file).

build_config() is pure: it takes a mapping such as os.environ, so tests can
pass their own values.
"""
import math
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

TRUE_WORDS = {'1', 'true', 'yes', 'on'}
WEBHOOK_PROCESSING_MODES = ('thread', 'inline')


@dataclass(frozen=True)
class Config:
    bot_name: str
    currency: str
    country_code: str
    timezone: str
    default_price: Decimal
    card_fee_percent: Decimal
    max_results: int
    max_distance_km: float
    # While there is no real restroom data, invent sample restrooms around
    # whoever shares their location, so the bot can be tried from anywhere.
    demo_restrooms_near_user: bool
    access_minutes: int
    session_timeout_minutes: int
    images: bool
    map_images: bool
    whatsapp_token: str
    whatsapp_phone_number_id: str
    whatsapp_verify_token: str
    whatsapp_app_secret: str
    graph_api_version: str
    webhook_processing: str
    web_chat: bool
    # PayPal (Orders v2, refunds, payouts). Without a client id, card and
    # PayPal stay simulated like mobile money.
    paypal_client_id: str = ''
    paypal_client_secret: str = ''
    paypal_live: bool = False
    paypal_webhook_id: str = ''
    # PayPal does not take KES, so the total is charged in this currency at
    # PAYPAL_RATE local units per unit (129 KES per US dollar).
    paypal_currency: str = 'USD'
    paypal_hold: bool = False  # hold (authorize) at payment, take (capture) at the door
    tfpass: bool = False  # the prepaid TapFlash Pass as a way to pay (PAYPAL_ONLY mode)
    tfpass_visits: int = 20
    tfpass_price: str = '12.00'
    paypal_rate: Decimal = Decimal(129)
    # The address of this server as people's phones see it, for PayPal's return
    # page (https://bot.example.com). Empty: taken from each request.
    public_url: str = ''
    # The bot's own WhatsApp number (2547...), for the "Back to WhatsApp" button.
    bot_whatsapp_number: str = ''
    # Gemini (Google AI). Without a key the bot runs on its menus alone.
    gemini_api_key: str = ''
    gemini_model: str = 'gemini-3.8-flash'
    # Problem reports that may refund on their own: within this many minutes
    # of paying, at most this many refunds per user per day.
    refund_window_minutes: int = 120
    max_refunds_per_day: int = 1
    cleaning_bonus_hours: int = 4
    # Offer only PayPal and cards (both through PayPal), PayPal first; no simulated mobile money.
    paypal_only: bool = False
    # Sandbox demos only: pay operators for simulated mobile money visits too.
    payouts_count_simulated: bool = False
    # seed_demo: the sandbox personal account that demo operators and cleaners are paid to.
    demo_payout_email: str = ''
    cleaning_max_per_day: int = 3
    # With DEMO_RESTROOMS_NEAR_USER off: sample restrooms for users with no
    # real restroom nearby, so the hosted demo works from anywhere.
    demo_restrooms_fallback: bool = False

    @property
    def paypal_ready(self):
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def ai_ready(self):
        return bool(self.gemini_api_key)


def load_env_file(path):
    """Read KEY=value lines from a .env file into os.environ.

    Variables already set in the real environment win, so the host's settings
    can override the file. utf-8-sig skips the invisible BOM that some Windows
    editors write at the start of a file.
    """
    load_dotenv(path, encoding='utf-8-sig', interpolate=False)


def read_str(env, name, default=''):
    raw = env.get(name)
    return default if raw is None or not raw.strip() else raw.strip()


def read_bool(env, name, default):
    raw = read_str(env, name)
    return raw.lower() in TRUE_WORDS if raw else default


def read_list(env, name):
    """A comma separated list: "a.com, b.com" gives ['a.com', 'b.com']."""
    return [item.strip() for item in read_str(env, name).split(',') if item.strip()]


def read_int(env, name, default):
    raw = read_str(env, name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ImproperlyConfigured(f'{name} must be a whole number, got "{raw}"') from None


def read_number(env, name, default):
    """A number, written back the way it was typed: "20" gives 20, "2.5" gives 2.5."""
    raw = read_str(env, name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise ImproperlyConfigured(f'{name} must be a number, got "{raw}"')
    return int(value) if value.is_integer() else value


def read_decimal(env, name, default):
    """An exact number for money and percentages: "1.50" gives Decimal('1.5')."""
    raw = read_str(env, name)
    if not raw:
        return Decimal(default)
    try:
        value = Decimal(raw)
    except InvalidOperation:
        value = Decimal('NaN')
    if not value.is_finite():
        raise ImproperlyConfigured(f'{name} must be a number, got "{raw}"')
    # Without this, "1e2" would show as "1E+2" in messages.
    return value.quantize(Decimal(1)) if value == value.to_integral_value() else value.normalize()


def read_timezone(env, name, default):
    value = read_str(env, name, default)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ImproperlyConfigured(
            f'{name} "{value}" is not a valid time zone (example: Africa/Nairobi)'
        ) from None
    return value


def build_config(env):
    country_code = read_str(env, 'COUNTRY_CODE', '254').removeprefix('+')
    if not re.fullmatch('[0-9]{1,3}', country_code):
        raise ImproperlyConfigured(f'COUNTRY_CODE must be 1-3 digits, got "{country_code}"')

    webhook_processing = read_str(env, 'WEBHOOK_PROCESSING', 'thread').lower()
    if webhook_processing not in WEBHOOK_PROCESSING_MODES:
        raise ImproperlyConfigured(
            f'WEBHOOK_PROCESSING must be thread or inline, got "{webhook_processing}"'
        )

    paypal_currency = read_str(env, 'PAYPAL_CURRENCY', 'USD').upper()
    if not re.fullmatch('[A-Z]{3}', paypal_currency):
        raise ImproperlyConfigured(f'PAYPAL_CURRENCY must be a 3-letter code such as USD, got "{paypal_currency}"')
    paypal_rate = read_decimal(env, 'PAYPAL_RATE', 129)
    if paypal_rate <= 0:
        raise ImproperlyConfigured(f'PAYPAL_RATE must be more than 0, got "{paypal_rate}"')

    return Config(
        bot_name=read_str(env, 'BOT_NAME', 'TapFlash'),
        currency=read_str(env, 'CURRENCY', 'KES'),
        country_code=country_code,
        timezone=read_timezone(env, 'TIMEZONE', 'Africa/Nairobi'),
        default_price=read_decimal(env, 'DEFAULT_PRICE', 100),
        card_fee_percent=read_decimal(env, 'CARD_FEE_PERCENT', 1),
        max_results=read_int(env, 'MAX_RESULTS', 4),
        max_distance_km=read_number(env, 'MAX_DISTANCE_KM', 20),
        demo_restrooms_near_user=read_bool(env, 'DEMO_RESTROOMS_NEAR_USER', True),
        access_minutes=read_int(env, 'ACCESS_MINUTES', 30),
        session_timeout_minutes=read_int(env, 'SESSION_TIMEOUT_MINUTES', 30),
        images=read_bool(env, 'IMAGES', True),
        map_images=read_bool(env, 'MAP_IMAGES', True),
        # Secrets come only from .env or the host's environment, never from the code.
        whatsapp_token=read_str(env, 'WHATSAPP_TOKEN'),
        whatsapp_phone_number_id=read_str(env, 'WHATSAPP_PHONE_NUMBER_ID'),
        whatsapp_verify_token=read_str(env, 'WHATSAPP_VERIFY_TOKEN'),
        whatsapp_app_secret=read_str(env, 'WHATSAPP_APP_SECRET'),
        graph_api_version=read_str(env, 'GRAPH_API_VERSION', 'v23.0'),
        webhook_processing=webhook_processing,
        # The browser test chat is on while developing, unless turned off.
        web_chat=read_bool(env, 'WEB_CHAT', read_bool(env, 'DEBUG', False)),
        paypal_client_id=read_str(env, 'PAYPAL_CLIENT_ID'),
        paypal_client_secret=read_str(env, 'PAYPAL_CLIENT_SECRET'),
        paypal_live=read_str(env, 'PAYPAL_ENV', 'sandbox').lower() == 'live',
        paypal_webhook_id=read_str(env, 'PAYPAL_WEBHOOK_ID'),
        paypal_currency=paypal_currency,
        paypal_rate=paypal_rate,
        public_url=read_str(env, 'PUBLIC_URL').rstrip('/'),
        paypal_hold=read_bool(env, 'PAYPAL_HOLD', 'test' not in sys.argv[1:2]),
        tfpass=read_bool(env, 'TFPASS', 'test' not in sys.argv[1:2]),
        tfpass_visits=read_int(env, 'TFPASS_VISITS', 20),
        tfpass_price=read_str(env, 'TFPASS_PRICE_USD', '12.00'),
        bot_whatsapp_number=re.sub('[^0-9]', '', read_str(env, 'BOT_WHATSAPP_NUMBER', '254182848213')),
        gemini_api_key=read_str(env, 'GEMINI_API_KEY'),
        gemini_model=read_str(env, 'GEMINI_MODEL', 'gemini-3.8-flash'),
        refund_window_minutes=read_int(env, 'REFUND_WINDOW_MINUTES', 120),
        max_refunds_per_day=read_int(env, 'MAX_REFUNDS_PER_DAY', 1),
        cleaning_bonus_hours=read_int(env, 'CLEANING_BONUS_HOURS', 4),
        payouts_count_simulated=read_bool(env, 'PAYOUTS_COUNT_SIMULATED', False),
        paypal_only=read_bool(env, 'PAYPAL_ONLY', False),
        demo_payout_email=read_str(env, 'DEMO_PAYOUT_EMAIL'),
        cleaning_max_per_day=read_int(env, 'CLEANING_MAX_PER_DAY', 3),
        demo_restrooms_fallback=read_bool(env, 'DEMO_RESTROOMS_FALLBACK', False),
    )
