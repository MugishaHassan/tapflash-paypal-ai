"""Distances and walking times."""
import math
from typing import NamedTuple

from bot.formatting import round_half_up, to_fixed

EARTH_RADIUS_KM = 6371
WALKING_SPEED_KMH = 5
# Streets are never a straight line, so pad the as-the-crow-flies distance.
DETOUR_FACTOR = 1.3


class Location(NamedTuple):
    lat: float
    lng: float


def to_radians(degrees):
    # The same steps as the Node version (not math.radians), so distances
    # match it to the last digit.
    return degrees * math.pi / 180


def distance_km(a, b):
    """Great-circle distance between two points with .lat and .lng, in km."""
    d_lat = to_radians(b.lat - a.lat)
    d_lng = to_radians(b.lng - a.lng)
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(to_radians(a.lat)) * math.cos(to_radians(b.lat)) * math.sin(d_lng / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1, math.sqrt(h)))


def walking_minutes(km):
    return max(1, round_half_up(km * DETOUR_FACTOR / WALKING_SPEED_KMH * 60))


def describe_minutes(minutes):
    """'45 min', '2 h' or '3 h 18 min'"""
    if minutes < 60:
        return f'{minutes} min'
    hours, rest = divmod(minutes, 60)
    return f'{hours} h {rest} min' if rest else f'{hours} h'


def describe_distance(km):
    """'350 m, about 5 min walk' or '17.9 km, about 4 h 40 min walk'"""
    where = f'{max(10, round_half_up(km * 1000 / 10) * 10)} m' if km < 1 else f'{to_fixed(km, 1)} km'
    return f'{where}, about {describe_minutes(walking_minutes(km))} walk'


def is_number(value):
    """True for a finite int or float. Text, true/false, NaN and infinity are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        # An int too big for a float, which JavaScript would read as Infinity.
        return False


def is_valid_location(lat, lng):
    """True for a real point on the globe."""
    return is_number(lat) and is_number(lng) and abs(lat) <= 90 and abs(lng) <= 180
