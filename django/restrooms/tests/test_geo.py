from django.test import SimpleTestCase

from restrooms.geo import Location, describe_distance, distance_km, is_valid_location, walking_minutes


class DistanceTests(SimpleTestCase):
    def test_distance_between_two_points_in_nairobi(self):
        km = distance_km(Location(-1.2864, 36.8172), Location(-1.2921, 36.8219))
        self.assertTrue(0.7 < km < 0.9, f'expected about 0.8 km, got {km}')

    def test_distance_and_walking_time_in_words(self):
        self.assertEqual(describe_distance(0.35), '350 m, about 5 min walk')
        self.assertEqual(describe_distance(1.234), '1.2 km, about 19 min walk')
        self.assertEqual(describe_distance(0), '10 m, about 1 min walk')
        self.assertEqual(describe_distance(3.85), '3.9 km, about 1 h walk')
        self.assertEqual(describe_distance(12.7), '12.7 km, about 3 h 18 min walk')

    def test_halves_round_up_like_the_node_version(self):
        self.assertEqual(describe_distance(1.25), '1.3 km, about 20 min walk')
        self.assertEqual(walking_minutes(0), 1)

    def test_valid_locations(self):
        self.assertTrue(is_valid_location(-1.28, 36.82))
        self.assertTrue(is_valid_location(90, -180))
        self.assertFalse(is_valid_location(91, 0))
        self.assertFalse(is_valid_location(0, 180.5))
        self.assertFalse(is_valid_location('1', 2))
        self.assertFalse(is_valid_location(True, 2))
        self.assertFalse(is_valid_location(float('nan'), 2))
        self.assertFalse(is_valid_location(float('inf'), 2))
        # Too big for a float: JavaScript reads it as Infinity.
        self.assertFalse(is_valid_location(10**400, 0))
        self.assertFalse(is_valid_location(None, None))
