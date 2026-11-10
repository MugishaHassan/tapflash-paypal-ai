"""Money, times, numbers and phone numbers, written the way the bot shows them.

The Node version rounds halves up (Math.round, toFixed) where Python rounds
them to even, so the helpers here round the Node way.
"""
import re
from datetime import timezone as dt_timezone
from decimal import ROUND_HALF_DOWN, ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

CENT = Decimal('0.01')
# The month names of the Node version's en-GB dates (September is "Sept").
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept', 'Oct', 'Nov', 'Dec')


def round_half_up(value):
    """Round to a whole number like JavaScript's Math.round: 2.5 gives 3, -2.5 gives -2."""
    rounding = ROUND_HALF_UP if value >= 0 else ROUND_HALF_DOWN
    return int(Decimal(value).to_integral_value(rounding))


def to_fixed(value, digits):
    """Write a number with `digits` decimals like JavaScript's toFixed: 1.25 gives "1.3"."""
    fixed = Decimal(value).quantize(Decimal(10) ** -digits, ROUND_HALF_UP)
    if value == 0:
        fixed = abs(fixed)  # toFixed writes -0 as "0"
    return f'{fixed:f}'


def format_money(amount, currency):
    """'KES 100' or 'KES 100.50'."""
    cents = Decimal(amount).quantize(CENT, ROUND_HALF_UP)
    text = f'{cents:f}' if cents % 1 else f'{cents:.0f}'
    return f'{currency} {text}'


def fee_for(amount, percent):
    """The fee for `amount` at `percent`, rounded to cents."""
    return (Decimal(amount) * Decimal(percent) / 100).quantize(CENT, ROUND_HALF_UP)


def format_time(when, timezone):
    """'10:55'"""
    return f'{when.astimezone(ZoneInfo(timezone)):%H:%M}'


def format_date_time(when, timezone):
    """'10 Oct 2026, 10:55'"""
    local = when.astimezone(ZoneInfo(timezone))
    return f'{local.day} {MONTHS[local.month - 1]} {local.year}, {local:%H:%M}'


def iso_utc(when):
    """'2026-10-10T15:00:00.000Z', like JavaScript's toISOString."""
    return when.astimezone(dt_timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


# The characters JavaScript's trim() and \s treat as spaces. Python's set
# differs (it leaves out U+FEFF and adds some control characters), and the
# bot must read messages the way the Node version did.
JS_SPACE = r'[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]'
_EDGE_SPACES = re.compile(f'^{JS_SPACE}+|{JS_SPACE}+$')


def js_trim(text):
    """text with spaces cut from both ends, as JavaScript's trim() does."""
    return _EDGE_SPACES.sub('', text)


def normalize_phone(text, country_code):
    """What the user typed as +<country code><9 digits>, or None if it isn't a phone number.

    Kenya, Tanzania, Uganda and Rwanda all use 9 digit numbers after the
    country code, so 0712 345 678 becomes +254712345678.
    """
    digits = re.sub(f'{JS_SPACE}|[-().]', '', js_trim(str(text)))
    if digits.startswith('+'):
        digits = digits[1:]
    elif digits.startswith('00'):
        digits = digits[2:]
    elif digits.startswith('0'):
        digits = country_code + digits[1:]
    elif len(digits) == 9:
        digits = country_code + digits

    if not re.fullmatch('[0-9]+', digits) or not digits.startswith(country_code):
        return None
    if len(digits) - len(country_code) != 9:
        return None
    return f'+{digits}'
