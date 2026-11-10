from urllib.parse import urlencode

from .formatting import to_fixed


def directions_url(place):
    """Google Maps walking directions to a place with .lat and .lng.

    With no starting point, Google Maps starts from the phone's own position,
    so the user's location stays out of the link.
    """
    params = {
        'api': '1',
        'destination': f'{to_fixed(place.lat, 6)},{to_fixed(place.lng, 6)}',
        'travelmode': 'walking',
    }
    return f'https://www.google.com/maps/dir/?{urlencode(params)}'
