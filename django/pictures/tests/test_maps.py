import json
import tempfile
import threading
import time
from io import BytesIO
from pathlib import Path

from django.test import SimpleTestCase
from PIL import Image as PILImage

from pictures.maps import (
    USER_AGENT, MapImages, choose_zoom, clean_text, is_plausible_route, lon_lat_to_pixel, pick_route, route_key,
    stable_hash, truncate_text,
)
from restrooms.geo import Location

FROM = Location(-1.2864, 36.8172)
TO = Location(-1.2841, 36.8199)


def make_tile(size=256):
    """A striped square, so blurring has something to smooth out."""
    tile = PILImage.new('RGB', (size, size), '#e8e0d0')
    tile.paste('#7aa874', (0, 0, size // 2, size))
    tile.paste('#3b6ea5', (0, size // 2, size, size * 3 // 4))
    buffer = BytesIO()
    tile.save(buffer, 'PNG')
    return buffer.getvalue()


def osrm(distance, code='Ok'):
    mid = [(FROM.lng + TO.lng) / 2, (FROM.lat + TO.lat) / 2 + 0.0004]
    return {'code': code, 'routes': [{'distance': distance, 'geometry': {
        'coordinates': [[FROM.lng, FROM.lat], mid, [TO.lng, TO.lat]]}}]}


class Response:
    def __init__(self, status=200, content=b'', data=None):
        self.status_code, self.content, self.data = status, content, data

    def json(self):
        return self.data


class FakeNetwork:
    """Serves tiles and OSRM answers and records what was asked."""

    def __init__(self, tile_size=256, route_distance=450, fail_tile=None, delay=0, tile=None):
        self.tile = tile if tile is not None else make_tile(tile_size)
        self.route_distance, self.fail_tile, self.delay = route_distance, fail_tile, delay
        self.tiles, self.routes, self.headers = [], [], []
        self.active = self.max_active = 0
        self.lock = threading.Lock()

    def __call__(self, url, headers, timeout):
        self.headers.append(headers)
        if url.startswith('https://tile.openstreetmap.org/'):
            with self.lock:
                self.tiles.append(url)
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                number = len(self.tiles)
            try:
                time.sleep(self.delay)
                if self.fail_tile and self.fail_tile(number):
                    return Response(503)
                return Response(200, self.tile)
            finally:
                with self.lock:
                    self.active -= 1
        if url.startswith('https://routing.openstreetmap.de/'):
            self.routes.append(url)
            return Response(200, data=osrm(self.route_distance))
        return Response(404)


def offline(url, headers, timeout):
    raise OSError('no network')


def size_of(image):
    return PILImage.open(BytesIO(image.data)).size


class GeometryTests(SimpleTestCase):
    def test_lon_lat_to_pixel(self):
        self.assertEqual(lon_lat_to_pixel(0, 0, 0), (128, 128))
        x, y = lon_lat_to_pixel(180, 85.0511287798, 1)
        self.assertAlmostEqual(x, 512)
        self.assertAlmostEqual(y, 0, places=4)

    def test_close_points_zoom_in_and_far_points_zoom_out(self):
        self.assertEqual(choose_zoom([FROM, TO], 800, 500), 17)  # as Node
        self.assertEqual(choose_zoom([FROM, Location(-1.0, 37.2)], 800, 500), 12)

    def test_route_sanity_check(self):
        self.assertTrue(is_plausible_route(450, 400))
        self.assertTrue(is_plausible_route(1300, 400))
        self.assertFalse(is_plausible_route(5000, 400))
        self.assertFalse(is_plausible_route(float('nan'), 400))

    def test_pick_route(self):
        found = pick_route(osrm(450), FROM, TO)
        self.assertEqual((found['source'], len(found['points']), found['distance_m']), ('osrm', 3, 450))
        self.assertEqual(pick_route(osrm(9000), FROM, TO)['source'], 'straight')
        self.assertIsNone(pick_route(osrm(450, code='NoRoute'), FROM, TO))
        self.assertIsNone(pick_route({'code': 'Ok', 'routes': [{'geometry': {'coordinates': [[1, 'x'], [2, 3]]}}]},
                                     FROM, TO))
        self.assertIsNone(pick_route(None, FROM, TO))

    def test_route_key_and_text(self):
        self.assertEqual(route_key(FROM, TO), '-1.28640_36.81720__-1.28410_36.81990')
        self.assertEqual(clean_text(' A© \U0001F6BB B\x07 '), 'A© B')
        self.assertEqual(truncate_text('abcdef', 4), 'abc…')
        self.assertEqual(truncate_text('abc', 4), 'abc')

    def test_stable_hash_matches_the_node_version(self):
        # Values printed by the Node version's hash().
        for n, value in [(0, 0.3186303242109716), (-7, 0.6146907068323344), (42, 0.14045337797142565),
                         (123456, 0.995385511778295)]:
            self.assertEqual(stable_hash(n), value)


class RouteMapTests(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache = Path(folder.name)

    def maps(self, network, **kwargs):
        return MapImages(self.cache, http_get=network, **kwargs)

    def test_draws_tiles_and_the_osrm_route_and_caches_both(self):
        network = FakeNetwork()
        image = self.maps(network).route_map(FROM, TO, label='City Market Restroom')
        self.assertEqual((image.filename, image.mimetype, size_of(image)), ('route-map.png', 'image/png', (800, 500)))
        self.assertTrue(network.tiles)
        self.assertEqual(len(network.routes), 1)
        self.assertTrue(all(h == {'User-Agent': USER_AGENT} for h in network.headers))
        cached = json.loads(next((self.cache / 'routes').iterdir()).read_text())
        self.assertEqual(cached['source'], 'osrm')
        self.assertTrue(list((self.cache / 'tiles').rglob('*.png')))

        again = FakeNetwork()
        self.maps(again).route_map(FROM, TO)
        self.assertEqual((again.tiles, again.routes), ([], []))

    def test_an_implausible_route_is_drawn_as_a_straight_line(self):
        found = self.maps(FakeNetwork(route_distance=50000)).route(FROM, TO)
        self.assertEqual(found['source'], 'straight')

    def test_preview_is_blurred(self):
        network = FakeNetwork()
        sharp = PILImage.open(BytesIO(self.maps(network).route_map(FROM, TO).data)).convert('L')
        preview = self.maps(network).route_map(FROM, TO, blur=True)
        self.assertEqual(preview.filename, 'route-preview.png')
        blurred = PILImage.open(BytesIO(preview.data)).convert('L')

        def roughness(picture):
            values = list(picture.getdata())
            return sum(abs(a - b) for a, b in zip(values, values[1:]))

        self.assertLess(roughness(blurred), roughness(sharp) / 2)

    def test_offline_falls_back_to_drawn_streets_and_a_straight_line(self):
        image = self.maps(offline).route_map(FROM, TO, label='')
        self.assertEqual(size_of(image), (800, 500))
        self.assertFalse((self.cache / 'routes').exists())  # asked again next time

    def test_bad_tiles_and_odd_sizes(self):
        image = self.maps(FakeNetwork(tile=b'not a png')).route_map(FROM, TO)
        self.assertEqual(size_of(image), (800, 500))
        image = self.maps(FakeNetwork(tile_size=512, fail_tile=lambda n: n % 2 == 0)).route_map(
            FROM, TO, width=99999, height=float('nan'))
        self.assertEqual(size_of(image), (2048, 500))

    def test_at_most_four_tile_downloads_at_once(self):
        network = FakeNetwork(delay=0.02)
        self.maps(network).route_map(FROM, Location(-1.30, 36.84), width=1600, height=1200)
        self.assertGreater(len(network.tiles), 4)
        self.assertLessEqual(network.max_active, 4)

    def test_points_must_be_numbers(self):
        with self.assertRaises(TypeError):
            self.maps(offline).route_map(Location(float('nan'), 1), TO)
