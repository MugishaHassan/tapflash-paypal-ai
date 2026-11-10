"""Place names to map points, with OpenStreetMap's Nominatim (free, no key).

People write "near Kencom" or "karibu na Archives" instead of sharing their
location. Searches stay inside Nairobi, and answers are kept, because
Nominatim asks for at most one request a second.
"""
import logging

import requests

from restrooms.geo import Location

logger = logging.getLogger(__name__)

NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
# west, north, east, south: Nairobi and its edges.
NAIROBI_BOX = '36.65,-1.10,37.10,-1.45'
# Nominatim wants an application name; no personal details are sent.
USER_AGENT = 'TapFlash/1.0 (restroom finder chat bot)'


_known = {}  # name -> (Location, label) or None, for answers Nominatim gave
MAX_KNOWN = 512


def find_place(name):
    """(Location, short label) for a place in Nairobi, or None."""
    name = ' '.join(str(name).split())[:120]
    if len(name) < 2:
        return None
    key = name.lower()
    if key in _known:
        return _known[key]
    try:
        res = requests.get(NOMINATIM_URL, timeout=(4, 10), headers={'User-Agent': USER_AGENT}, params={
            'q': name, 'format': 'jsonv2', 'limit': 1, 'countrycodes': 'ke',
            'viewbox': NAIROBI_BOX, 'bounded': 1,
        })
        res.raise_for_status()
        found = res.json()
    except (requests.RequestException, ValueError) as error:
        # Not remembered, so the next message tries again.
        logger.warning('Could not look up a place: %s', type(error).__name__)
        return None
    answer = None
    if found:
        place = found[0]
        label = place.get('name') or place.get('display_name', name).split(',')[0]
        answer = Location(float(place['lat']), float(place['lon'])), label
    if len(_known) >= MAX_KNOWN:
        _known.clear()
    _known[key] = answer
    return answer
