import re
from datetime import datetime, timedelta
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from bot.config import build_config
from bot.directions import directions_url
from restrooms.demo import demo_restrooms_around
from restrooms.finder import RestroomFinder, find_nearest
from restrooms.geo import Location, is_valid_location
from restrooms.models import Restroom

HERE = Location(-1.2864, 36.8172)


def codes(facilities):
    return [facility.code for facility in facilities]


class DemoRestroomTests(SimpleTestCase):
    def test_restrooms_to_leave_out_make_room_for_the_next_closest(self):
        demo = demo_restrooms_around(HERE, Decimal(100))
        options = {'max_results': 2, 'max_distance_km': 20}
        self.assertEqual(codes(find_nearest(demo, HERE, **options)), ['demo-1', 'demo-2'])
        self.assertEqual(codes(find_nearest(demo, HERE, leave_out={'demo-1'}, **options)), ['demo-2', 'demo-3'])

    def test_demo_restrooms_near_a_pole_or_the_date_line_get_real_coordinates(self):
        destination = re.compile(r'destination=-?[0-9]{1,2}\.[0-9]{6}%2C-?[0-9]{1,3}\.[0-9]{6}&')
        for here in [Location(90, 0), Location(-90, 0), Location(89.9999, 10), Location(0, 180), Location(10, 179.999)]:
            for facility in demo_restrooms_around(here, Decimal(100)):
                with self.subTest(here=here, facility=facility.code):
                    self.assertTrue(is_valid_location(facility.lat, facility.lng), f'{facility.lat},{facility.lng}')
                    self.assertRegex(directions_url(facility), destination)

        date_line = Location(0, 180)
        [mall] = find_nearest(demo_restrooms_around(date_line, Decimal(100)), date_line, max_results=1, max_distance_km=20)
        self.assertLess(mall.distance_km, 0.5, 'still next to the user across the date line')

    def test_demo_restrooms_surround_the_user_and_are_sorted_by_distance(self):
        here = Location(-6.17, 35.74)
        nearest = find_nearest(demo_restrooms_around(here, Decimal(100)), here, max_results=4, max_distance_km=20)
        self.assertEqual(len(nearest), 4)
        distances = [facility.distance_km for facility in nearest]
        self.assertEqual(distances, sorted(distances))
        self.assertLess(nearest[0].distance_km, 0.5)

    def test_demo_restrooms_use_the_default_price_and_say_they_are_demos(self):
        [mall, *_] = demo_restrooms_around(HERE, Decimal(80))
        self.assertEqual(mall.name, 'City Mall Restroom (demo)')
        self.assertEqual(mall.price, Decimal(80))
        self.assertEqual(mall.hours, '6:00 AM – 10:00 PM')

    def test_closed_restrooms_are_left_out_when_a_time_is_given(self):
        demo = demo_restrooms_around(HERE, Decimal(100))
        late = datetime.fromisoformat('2026-10-10T23:30:00+03:00')
        nearest = find_nearest(demo, HERE, max_results=4, max_distance_km=20, at=late, timezone='Africa/Nairobi')
        self.assertEqual(codes(nearest), ['demo-5', 'demo-6'], 'only the two open all night')


class RestroomFinderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def add(code, lat, lng, **fields):
            Restroom.objects.create(code=code, name=code.title(), latitude=lat, longitude=lng, **fields)

        add('kenyatta', -1.2850, 36.8200, price=Decimal(50), opening_hours='24 hours')
        add('city-market', -1.2833, 36.8219, opening_hours='6:00 AM – 9:00 PM')
        add('hidden', -1.2864, 36.8173, is_active=False)
        add('mombasa', -4.0435, 39.6682)
        add('same-latitude-far-east', -1.2864, 39.0)

    def finder(self, **env):
        return RestroomFinder(build_config({'DEMO_RESTROOMS_NEAR_USER': 'false', **env}))

    def test_active_restrooms_nearby_are_offered_nearest_first(self):
        nearest = self.finder().nearest(HERE)
        self.assertEqual(codes(nearest), ['kenyatta', 'city-market'])
        self.assertAlmostEqual(nearest[0].distance_km, 0.348, places=3)

    def test_restrooms_without_a_price_cost_the_default_price(self):
        kenyatta, city_market = self.finder(DEFAULT_PRICE='120').nearest(HERE)
        self.assertEqual(kenyatta.price, Decimal(50))
        self.assertEqual(city_market.price, Decimal(120))

    def test_closed_and_left_out_restrooms_are_skipped(self):
        late = datetime.fromisoformat('2026-10-10T22:00:00+03:00')
        self.assertEqual(codes(self.finder().nearest(HERE, late)), ['kenyatta'])
        self.assertEqual(codes(self.finder().nearest(HERE, leave_out={'kenyatta'})), ['city-market'])

    def test_max_results_and_distance_come_from_the_settings(self):
        self.assertEqual(codes(self.finder(MAX_RESULTS='1').nearest(HERE)), ['kenyatta'])
        self.assertEqual(codes(self.finder(MAX_DISTANCE_KM='0.4').nearest(HERE)), ['kenyatta'])
        self.assertEqual(self.finder().nearest(Location(51.5, -0.12)), [])

    def test_demo_mode_ignores_the_database(self):
        nearest = RestroomFinder(build_config({})).nearest(HERE)
        self.assertEqual(codes(nearest), ['demo-1', 'demo-2', 'demo-3', 'demo-4'])

    def test_a_checked_cleaning_photo_shows_and_puts_the_restroom_first_for_clean(self):
        from bot.messages import Messages
        from ops.models import Cleaner, CleaningCheck
        now = datetime.fromisoformat('2026-11-08T12:00:00+03:00')
        market = Restroom.objects.get(code='city-market')
        Restroom.objects.filter(code='kenyatta').update(cleanliness=5)
        cleaner = Cleaner.objects.create(name='Amina', phone='+254700000001', restroom=market)
        for minutes, status, score in [(300, 'approved', 2), (25, 'approved', 5), (5, 'rejected', 1)]:
            CleaningCheck.objects.create(cleaner=cleaner, restroom=market, user_id='u', photo=b'x',
                                         photo_hash=str(minutes), status=status, score=score,
                                         created_at=now - timedelta(minutes=minutes))
        found = self.finder().nearest(HERE, now, need='clean')
        self.assertEqual(codes(found), ['city-market', 'kenyatta'], 'cleaned 25 min ago beats 5 stars')
        msg = Messages(build_config({}))
        self.assertTrue(msg.restroom_details(found[0], now=now).endswith(', cleaned 25 min ago'))
        self.assertIn('Cleaned 25 min ago (the cleaner\'s photo was checked by AI)', msg.pay_prompt(found[0], now=now))
        self.assertNotIn('cleaned', msg.restroom_details(found[1], now=now))
        later = now + timedelta(days=1)
        self.assertNotIn('cleaned', msg.restroom_details(found[0], now=later), 'older than a day is not shown')

    def test_cleaned_ago_counts_from_the_approval_not_from_the_photo(self):
        from bot.messages import Messages
        from ops.models import Cleaner, CleaningCheck
        now = datetime.fromisoformat('2026-11-08T12:00:00+03:00')
        market = Restroom.objects.get(code='city-market')
        cleaner = Cleaner.objects.create(name='Amina', phone='+254700000001', restroom=market)
        # The photo waited three hours for a supervisor, who approved it 5 minutes ago.
        CleaningCheck.objects.create(cleaner=cleaner, restroom=market, user_id='u', photo=b'x', photo_hash='h',
                                     status='approved', score=4, created_at=now - timedelta(hours=3),
                                     decided_at=now - timedelta(minutes=5))
        found = self.finder().nearest(HERE, now, need='clean')
        city = next(f for f in found if f.code == 'city-market')
        self.assertTrue(Messages(build_config({})).restroom_details(city, now=now).endswith(', cleaned 5 min ago'))
