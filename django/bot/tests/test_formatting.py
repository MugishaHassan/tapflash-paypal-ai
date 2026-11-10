from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from bot.directions import directions_url
from bot.formatting import (
    fee_for, format_date_time, format_money, format_time, normalize_phone, round_half_up, to_fixed,
)

TZ = 'Africa/Nairobi'


class PhoneTests(SimpleTestCase):
    def test_local_and_international_formats(self):
        self.assertEqual(normalize_phone('0712345678', '254'), '+254712345678')
        self.assertEqual(normalize_phone('0712 345 678', '254'), '+254712345678')
        self.assertEqual(normalize_phone('+254 712-345-678', '254'), '+254712345678')
        self.assertEqual(normalize_phone('254712345678', '254'), '+254712345678')
        self.assertEqual(normalize_phone('00254712345678', '254'), '+254712345678')
        self.assertEqual(normalize_phone('712345678', '254'), '+254712345678')
        self.assertEqual(normalize_phone('(0712) 345.678', '254'), '+254712345678')
        self.assertEqual(normalize_phone('0755123456', '255'), '+255755123456')

    def test_things_that_are_not_phone_numbers(self):
        self.assertIsNone(normalize_phone('12345', '254'))
        self.assertIsNone(normalize_phone('07123456789', '254'))
        self.assertIsNone(normalize_phone('+255712345678', '254'), 'other country')
        self.assertIsNone(normalize_phone('abc', '254'))
        self.assertIsNone(normalize_phone('', '254'))
        self.assertIsNone(normalize_phone('07\u0661\u0662345678', '254'), 'digits from another script')


class MoneyTests(SimpleTestCase):
    def test_money_and_fees(self):
        self.assertEqual(format_money(100, 'KES'), 'KES 100')
        self.assertEqual(format_money(Decimal('101.5'), 'KES'), 'KES 101.50')
        self.assertEqual(format_money(Decimal('100.00'), 'TZS'), 'TZS 100')
        self.assertEqual(format_money(Decimal('0.005'), 'KES'), 'KES 0.01', 'halves round up')
        self.assertEqual(fee_for(100, 1), Decimal('1.00'))
        self.assertEqual(fee_for(150, 1), Decimal('1.50'))
        self.assertEqual(fee_for(100, 0), 0)
        self.assertEqual(fee_for(Decimal('50'), Decimal('2.5')), Decimal('1.25'))
        self.assertEqual(fee_for(Decimal('10.10'), Decimal('2.5')), Decimal('0.25'), '0.2525 rounds to 0.25')


class TimeTests(SimpleTestCase):
    def test_times_are_shown_in_the_bot_time_zone(self):
        when = datetime(2026, 9, 10, 7, 55, tzinfo=timezone.utc)
        self.assertEqual(format_time(when, TZ), '10:55')
        self.assertEqual(format_date_time(when, TZ), '10 Sept 2026, 10:55')
        self.assertEqual(format_date_time(datetime(2026, 1, 4, 21, 5, tzinfo=timezone.utc), TZ), '5 Jan 2026, 00:05')


class JavaScriptRoundingTests(SimpleTestCase):
    def test_round_half_up_like_math_round(self):
        self.assertEqual([round_half_up(x) for x in (0.5, 1.5, 2.5, -2.5, -2.6, 198.12)], [1, 2, 3, -2, -3, 198])

    def test_to_fixed_like_javascript(self):
        self.assertEqual(to_fixed(1.25, 1), '1.3')
        self.assertEqual(to_fixed(0.0078125, 6), '0.007813')
        self.assertEqual(to_fixed(-0.0, 6), '0.000000')
        self.assertEqual(to_fixed(-1.28, 6), '-1.280000')
        self.assertEqual(to_fixed(12.7, 1), '12.7')


class DirectionsTests(SimpleTestCase):
    def test_walking_directions_without_the_user_location(self):
        url = directions_url(SimpleNamespace(lat=-1.28, lng=36.82))
        self.assertEqual(url, 'https://www.google.com/maps/dir/?api=1&destination=-1.280000%2C36.820000&travelmode=walking')
