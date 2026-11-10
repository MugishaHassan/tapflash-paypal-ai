from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from restrooms.models import Restroom


def restroom(**fields):
    return Restroom(**{'code': 'city-market', 'name': 'City Market', 'latitude': -1.2833, 'longitude': 36.8219, **fields})


class RestroomTests(TestCase):
    def test_readable_or_empty_opening_hours_are_accepted(self):
        for hours in ['6:00 AM – 9:00 PM', '06:00-21:00', '6 pm to 2 am', '24 hours', '']:
            with self.subTest(hours=hours):
                restroom(opening_hours=hours).full_clean()

    def test_opening_hours_that_cannot_be_read_are_rejected(self):
        with self.assertRaises(ValidationError) as caught:
            restroom(opening_hours='weekdays only').full_clean()
        self.assertIn("can't be read", caught.exception.message_dict['opening_hours'][0])

    def test_coordinates_must_be_on_the_globe(self):
        with self.assertRaises(ValidationError) as caught:
            restroom(latitude=91, longitude=-181).full_clean()
        self.assertEqual(set(caught.exception.message_dict), {'latitude', 'longitude'})

    def test_price_cannot_be_negative(self):
        with self.assertRaises(ValidationError):
            restroom(price=Decimal('-1')).full_clean()

    def test_code_is_a_slug(self):
        with self.assertRaises(ValidationError):
            restroom(code='city market!').full_clean()

    def test_facility_snapshot(self):
        facility = restroom(area='Nairobi CBD', opening_hours='24 hours').to_facility(Decimal(100))
        self.assertEqual(facility.code, 'city-market')
        self.assertEqual((facility.lat, facility.lng), (-1.2833, 36.8219))
        self.assertEqual(facility.price, Decimal(100), 'no price: the default price')
        self.assertEqual(facility.hours, '24 hours')
        self.assertEqual(facility.photo, '')
        self.assertIsNone(facility.distance_km)
        self.assertEqual(restroom(price=Decimal(50)).to_facility(Decimal(100)).price, Decimal(50))
        self.assertEqual(restroom(price=Decimal(0)).to_facility(Decimal(100)).price, Decimal(0), 'free is a price')
