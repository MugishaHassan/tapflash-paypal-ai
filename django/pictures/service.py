"""Everything the bot draws, in the shape the conversation engine expects.

build_pictures() returns None (text only) when IMAGES is off or when the
image library can't be loaded on this server, so a broken Pillow never stops
the bot.
"""
import logging
import threading

logger = logging.getLogger(__name__)


class Pictures:
    def __init__(self, config, media_root, cache_dir, http_get=None):
        from .cards import Cards
        from .maps import MapImages

        self.cards = Cards(config.currency, config.timezone, media_root)
        # Maps download OpenStreetMap tiles; MAP_IMAGES=false turns them off.
        if not config.map_images:
            self.maps = None
        elif http_get:
            self.maps = MapImages(cache_dir / 'maps', http_get=http_get)
        else:
            self.maps = MapImages(cache_dir / 'maps')
        # Thumbnails and icons are drawn once per process.
        self._small = {}
        self._lock = threading.Lock()

    def _once(self, key, make):
        with self._lock:
            if key in self._small:
                return self._small[key]
        image = make()
        with self._lock:
            if len(self._small) > 200:
                self._small.clear()
            self._small[key] = image
        return image

    def restroom_thumb(self, facility):
        return self._once(('thumb', facility.code, facility.photo), lambda: self.cards.restroom_thumb(facility))

    def payment_icon(self, method):
        return self._once(('icon', method.id), lambda: self.cards.payment_icon(method))

    def restroom_photo(self, facility):
        return self.cards.restroom_photo(facility)

    def access_card(self, access_pass, qr_png):
        return self.cards.access_card(access_pass, qr_png)

    def route_preview(self, location, facility):
        """Blurred before payment, like the demo video; clear once paid."""
        return self.maps and self.maps.route_map(location, facility, label=facility.name, blur=True)

    def route_map(self, location, facility):
        return self.maps and self.maps.route_map(location, facility, label=facility.name)


def build_pictures(config, media_root, cache_dir):
    if not config.images:
        return None
    try:
        return Pictures(config, media_root, cache_dir)
    except Exception:
        logger.exception('Pictures are off: the image library could not be loaded')
        return None
