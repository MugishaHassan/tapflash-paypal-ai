from datetime import datetime

from django.test import SimpleTestCase

from restrooms.facility import Facility
from restrooms.hours import is_open_at, is_open_on_arrival, next_closing, parse_hours

TZ = 'Africa/Nairobi'


def nairobi(time, day='2026-10-10'):
    return datetime.fromisoformat(f'{day}T{time}+03:00')


class ParseHoursTests(SimpleTestCase):
    def test_hours_are_read(self):
        self.assertEqual(parse_hours('6:00 AM – 9:00 PM'), (360, 1260))
        self.assertEqual(parse_hours('06:00-21:00'), (360, 1260))
        self.assertEqual(parse_hours('24 hours'), (0, 1440))
        self.assertEqual(parse_hours('6 pm to 2 am'), (1080, 120))
        self.assertEqual(parse_hours('6.30 am \u2014 9 pm'), (390, 1260), 'em dash')
        self.assertEqual(parse_hours('Open 24 hrs'), (0, 1440))
        self.assertEqual(parse_hours('24/7'), (0, 1440))

    def test_hours_that_cannot_be_read(self):
        self.assertIsNone(parse_hours('weekdays only'))
        self.assertIsNone(parse_hours(''))
        self.assertIsNone(parse_hours(None))
        self.assertIsNone(parse_hours('13 pm - 2 am'))
        self.assertIsNone(parse_hours('6:75 - 9:00'))
        self.assertIsNone(parse_hours('25:00 - 26:00'))
        # Digits from other scripts are not clock times.
        self.assertIsNone(parse_hours('\u0666:00 - 9:00'))


class OpenAtTests(SimpleTestCase):
    def test_open_check_in_the_bot_time_zone(self):
        self.assertTrue(is_open_at('6:00 AM – 6:00 PM', nairobi('17:59'), TZ))
        self.assertFalse(is_open_at('6:00 AM – 6:00 PM', nairobi('18:00'), TZ))
        self.assertFalse(is_open_at('6:00 AM – 6:00 PM', nairobi('05:30'), TZ))
        self.assertTrue(is_open_at('6 pm to 2 am', nairobi('01:00'), TZ), 'past midnight')
        self.assertFalse(is_open_at('6 pm to 2 am', nairobi('12:00'), TZ))
        self.assertTrue(is_open_at('24 hours', nairobi('03:00'), TZ))
        self.assertTrue(is_open_at('', nairobi('03:00'), TZ), 'no hours: always listed')
        self.assertTrue(is_open_at('weekdays only', nairobi('03:00'), TZ), 'unreadable hours count as open')

    def test_the_same_moment_in_another_time_zone(self):
        utc_17_59 = datetime.fromisoformat('2026-10-10T14:59:00+00:00')
        self.assertTrue(is_open_at('6:00 AM – 6:00 PM', utc_17_59, TZ))
        self.assertFalse(is_open_at('6:00 AM – 6:00 PM', utc_17_59, 'Asia/Dubai'), '18:59 in Dubai')

    def test_open_on_arrival_counts_the_walk(self):
        # 1.2 km is a 19 minute walk.
        far = Facility('a', 'A', '', 0, 0, 100, hours='6:00 AM – 6:00 PM', distance_km=1.2)
        self.assertTrue(is_open_on_arrival(far, nairobi('17:40'), TZ))
        self.assertFalse(is_open_on_arrival(far, nairobi('17:42'), TZ))


class NextClosingTests(SimpleTestCase):
    def test_next_closing_time_in_the_bot_time_zone(self):
        def closes(hours, at):
            return next_closing(hours, nairobi(at), TZ)

        self.assertEqual(closes('6:00 AM – 6:00 PM', '17:45:30'), nairobi('18:00:00'))
        self.assertEqual(closes('6 pm to 2 am', '23:00:00'), nairobi('02:00:00', day='2026-10-11'), 'past midnight')
        self.assertEqual(closes('6:00 AM – 10:00 PM', '22:30:00'), nairobi('22:00:00', day='2026-10-11'), 'closed: tomorrow')
        self.assertIsNone(closes('24 hours', '23:00:00'))
        self.assertIsNone(closes('', '23:00:00'))
        self.assertIsNone(closes('weekdays only', '23:00:00'))
