"""Sample restrooms placed around the user (DEMO_RESTROOMS_NEAR_USER=true), so
the bot can be tried anywhere before real restrooms are added."""
import math
from pathlib import Path

from .facility import Facility
from .geo import to_radians

# Free sample photos in restrooms/demo_photos (sources in CREDITS.txt there).
DEMO_PHOTO_PREFIX = 'demo:'
DEMO_PHOTOS = Path(__file__).resolve().parent / 'demo_photos'

DAY_HOURS = '6:00 AM – 10:00 PM'
KM_PER_DEGREE_LAT = 110.574

# Name, area, how many km north and east of the user, hours, cleanliness
# (1-5) and what it offers: (accessible, disabled toilet, baby changing).
# Two are open all night, so the demo also works late.
DEMO_PLACES = [
    ('City Mall Restroom', 'Shopping mall, ground floor', 0.25, 0.3, DAY_HOURS, 5, (True, True, True)),
    ('Central Bus Station Restroom', 'Next to the ticket office', -0.35, 0.2, DAY_HOURS, 3, (True, False, False)),
    ('Market Square Restroom', 'Behind the fruit market', 0.55, -0.4, DAY_HOURS, 2, (False, False, False)),
    ('Riverside Park Restroom', 'Park main gate', -0.8, -0.7, DAY_HOURS, 4, (True, True, False)),
    ('Fuel Station Restroom', 'Main road fuel station', 1.3, 0.9, '24 hours', 3, (False, False, True)),
    ('Hospital Road Restroom', 'Opposite the hospital', -1.6, 1.2, '24 hours', 4, (True, True, True)),
]


def demo_restrooms_around(location, default_price):
    km_per_degree_lng = 111.32 * math.cos(to_radians(location.lat)) or 1e-6
    return [
        Facility(
            code=f'demo-{number}',
            name=f'{name} (demo)',
            area=area,
            lat=_clamp_lat(location.lat + north / KM_PER_DEGREE_LAT),
            lng=_wrap_lng(location.lng + east / km_per_degree_lng),
            price=default_price,
            hours=hours,
            photo=f'{DEMO_PHOTO_PREFIX}{number}.jpg',
            cleanliness=stars,
            accessible=offers[0],
            disabled_toilet=offers[1],
            baby_changing=offers[2],
        )
        for number, (name, area, north, east, hours, stars, offers) in enumerate(DEMO_PLACES, start=1)
    ]


# Near a pole or the date line an offset can step past 90 or 180 degrees:
# keep the latitude on the globe and wrap the longitude round to -180..180.
def _clamp_lat(lat):
    return min(90, max(-90, lat))


def _wrap_lng(lng):
    return math.fmod(math.fmod(lng + 180, 360) + 360, 360) - 180
