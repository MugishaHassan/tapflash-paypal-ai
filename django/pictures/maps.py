"""Street map pictures: OpenStreetMap tiles with the walking route, the start
and destination markers and a "Destination" card. A port of maps-image.js.

Tiles and routes are cached on disk. Without network the map is drawn on a
plain street pattern, so a picture never fails because the internet is down.
"""
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image as PILImage
from PIL import ImageEnhance, ImageFilter

from restrooms.geo import distance_km, is_number

from .drawing import Canvas, fit_text, text_width, to_png

logger = logging.getLogger(__name__)

TILE_URL = 'https://tile.openstreetmap.org'
ROUTE_URL = 'https://routing.openstreetmap.de/routed-foot/route/v1/driving'
USER_AGENT = 'TapFlashBot/0.1 (WhatsApp restroom finder; testing)'

TILE_SIZE = 256
MIN_ZOOM, MAX_ZOOM = 12, 18
MAX_LAT = 85.0511287798
TILE_CONCURRENCY = 4
MISSING_TILE = '#f2efe9'
TILE_SATURATION = 0.6  # toned down so the green route stands out
BLUR_RADIUS = 14

GREEN = '#1DAA61'
INK = '#111b21'
MUTED = '#667781'
ATTRIBUTION = '© OpenStreetMap contributors'

# Layout in pixels: the route stays PAD from the edges and the bottom
# LABEL_ROOM pixels are kept for the "Destination" card.
PAD = 70
CARD_HEIGHT = 62
CARD_MARGIN = 16
CARD_BOTTOM = 24
CARD_PAD_RIGHT = 20
LABEL_ROOM = CARD_HEIGHT + CARD_BOTTOM + 24

# At most four tile downloads at once in this process (OSM tile policy).
_tile_slots = threading.BoundedSemaphore(TILE_CONCURRENCY)


def js_round(value):
    return math.floor(value + 0.5)


# ------------------------------------------------------------------ text

def clean_text(text):
    """Whitespace squashed, emoji and control characters dropped (© stays)."""
    out = []
    for ch in '' if text is None else str(text):
        code = ord(ch)
        if ch in '©®™':
            out.append(ch)
        elif 0x1F000 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF or 0xE0020 <= code <= 0xE007F:
            continue
        elif ch in '‍⃣︎️' or 0x1F3FB <= code <= 0x1F3FF:
            continue
        elif unicodedata.category(ch) in ('Cc', 'Cs') or ch in '￾￿':
            out.append(' ')
        else:
            out.append(ch)
    return re.sub(r'\s+', ' ', ''.join(out)).strip()


def truncate_text(text, max_chars):
    chars = clean_text(text)
    if len(chars) <= max_chars:
        return chars
    return chars[:max(1, max_chars - 1)].rstrip() + '…'


# ------------------------------------------------------------------ geometry

def lon_lat_to_pixel(lng, lat, zoom):
    """Web Mercator: a point as a pixel on the whole-world map at this zoom."""
    scale = TILE_SIZE * 2 ** zoom
    sin = math.sin(max(-MAX_LAT, min(MAX_LAT, lat)) * math.pi / 180)
    return ((lng + 180) / 360 * scale, (0.5 - math.log((1 + sin) / (1 - sin)) / (4 * math.pi)) * scale)


def straight_line_meters(a, b):
    return distance_km(a, b) * 1000


def is_plausible_route(distance_m, straight_m):
    """Is a routed walk believable for two points this far apart? A wrong snap
    can turn 400 m into a detour of several kilometres."""
    return is_number(distance_m) and distance_m <= max(3 * straight_m, straight_m + 1000)


def _bounds(points, zoom):
    pixels = [lon_lat_to_pixel(p.lng, p.lat, zoom) for p in points]
    xs = [x for x, _ in pixels]
    ys = [y for _, y in pixels]
    return min(xs), min(ys), max(xs), max(ys)


def _route_area(width, height):
    return max(1, width - 2 * PAD), max(1, height - PAD - LABEL_ROOM)


def choose_zoom(points, width, height):
    """The highest zoom (12-18) where all points fit in the route area."""
    area_w, area_h = _route_area(width, height)
    for zoom in range(MAX_ZOOM, MIN_ZOOM, -1):
        min_x, min_y, max_x, max_y = _bounds(points, zoom)
        if max_x - min_x <= area_w and max_y - min_y <= area_h:
            return zoom
    return MIN_ZOOM


def view_origin(points, zoom, width, height):
    """The world pixel at the picture's top-left corner, centring the points."""
    min_x, min_y, max_x, max_y = _bounds(points, zoom)
    _, area_h = _route_area(width, height)
    return js_round((min_x + max_x) / 2 - width / 2), js_round((min_y + max_y) / 2 - (PAD + area_h / 2))


class Point:
    __slots__ = ('lat', 'lng')

    def __init__(self, lat, lng):
        self.lat, self.lng = lat, lng

    def __eq__(self, other):
        return (self.lat, self.lng) == (other.lat, other.lng)


def is_point(p):
    return p is not None and is_number(getattr(p, 'lat', None)) and is_number(getattr(p, 'lng', None))


def route_key(frm, to):
    """Cache file name from coordinates rounded to 5 decimals (about 1 m)."""
    return f'{frm.lat:.5f}_{frm.lng:.5f}__{to.lat:.5f}_{to.lng:.5f}'


def straight_route(frm, to):
    return {'points': [Point(frm.lat, frm.lng), Point(to.lat, to.lng)],
            'distance_m': straight_line_meters(frm, to), 'source': 'straight'}


def pick_route(data, frm, to):
    """The route to draw from an OSRM answer: its walk when believable, else a
    straight line. None when the answer is unusable."""
    routes = data.get('routes') if isinstance(data, dict) and data.get('code') == 'Ok' else None
    best = routes[0] if isinstance(routes, list) and routes and isinstance(routes[0], dict) else None
    geometry = best.get('geometry') if best else None
    coords = geometry.get('coordinates') if isinstance(geometry, dict) else None
    if not isinstance(coords, list) or len(coords) < 2:
        return None
    if not all(isinstance(c, list) and len(c) >= 2 and is_number(c[0]) and is_number(c[1]) for c in coords):
        return None
    if not is_plausible_route(best.get('distance'), straight_line_meters(frm, to)):
        return straight_route(frm, to)
    return {'points': [Point(lat, lng) for lng, lat, *_ in coords], 'distance_m': best['distance'], 'source': 'osrm'}


def _route_from_json(value):
    try:
        points = [Point(lat, lng) for lat, lng in value['points']]
        if value['source'] in ('osrm', 'straight') and is_number(value['distance_m']) and len(points) >= 2 \
                and all(is_point(p) for p in points):
            return {'points': points, 'distance_m': value['distance_m'], 'source': value['source']}
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _imul(a, b):
    return (a * b) & 0xFFFFFFFF


def stable_hash(n):
    """Same seed, same number in [0, 1), exactly as the Node version."""
    h = _imul(n & 0xFFFFFFFF, 0x9E3779B1) ^ 0x85EBCA6B
    h = _imul(h ^ (h >> 15), 0x2C1B3C6D)
    h = _imul(h ^ (h >> 12), 0x297A2D39)
    return (h ^ (h >> 15)) / 4294967296


# ------------------------------------------------------------------ drawing

def fallback_background(width, height, origin):
    """A stylised street map for when no tiles can be downloaded, laid out in
    world pixels so the same place always looks the same."""
    spacing = 96
    ox, oy = origin

    def line(i, salt):
        return i * spacing + (stable_hash(i * 7 + salt) - 0.5) * 30

    xs = [(i, line(i, 11) - ox) for i in range(math.floor(ox / spacing) - 1, math.ceil((ox + width) / spacing) + 2)]
    ys = [(j, line(j, 97) - oy) for j in range(math.floor(oy / spacing) - 1, math.ceil((oy + height) / spacing) + 2)]

    canvas = Canvas(width, height, background='#eef1ee')
    for a in range(len(xs) - 1):
        for b in range(len(ys) - 1):
            h = stable_hash(xs[a][0] * 131 + ys[b][0] * 17 + 5)
            fill = '#e1ebdc' if h < 0.1 else '#f3f5f3' if h < 0.5 else '#eef1ee'
            canvas.rect(xs[a][1], ys[b][1], xs[a + 1][1] - xs[a][1], ys[b + 1][1] - ys[b][1], fill=fill)
    streets = [((x, -10), (x + (stable_hash(i) - 0.5) * 24, height + 10), i % 3 == 0) for i, x in xs]
    streets += [((-10, y), (width + 10, y + (stable_hash(j + 500) - 0.5) * 24), j % 3 == 0) for j, y in ys]
    for start, end, major in streets:
        canvas.line([start, end], '#dde3dd', 13 if major else 8)
    for start, end, major in streets:
        canvas.line([start, end], '#ffffff', 9 if major else 5)
    return canvas.result()


def _thin(pixels):
    """Drop points that land on the same pixel."""
    out = []
    for p in pixels:
        if not out or abs(p[0] - out[-1][0]) >= 0.5 or abs(p[1] - out[-1][1]) >= 0.5:
            out.append(p)
    if len(out) == 1 and len(pixels) > 1:
        out.append(pixels[-1])
    return out


def _dots(canvas, a, b, color, radius, step=9):
    """A dotted connector, one round dot every `step` pixels."""
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    for k in range(int(length // step) + 1):
        t = k * step / length if length else 0
        canvas.circle(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, radius, fill=color)


def overlay(width, height, zoom, origin, found, frm, to, label, credit):
    """The route, the markers, the destination card and, when OSM data is
    shown, the OSM credit, on a transparent picture."""
    def to_px(p):
        x, y = lon_lat_to_pixel(p.lng, p.lat, zoom)
        return x - origin[0], y - origin[1]

    route = _thin([to_px(p) for p in found['points']])
    start, end = to_px(frm), to_px(to)
    canvas = Canvas(width, height, background=(0, 0, 0, 0), mode='RGBA')

    # Dotted connectors from the exact points to where the walk begins and
    # ends (OSRM snaps both ends to the nearest path).
    connectors = [(a, b) for a, b in ((start, route[0]), (route[-1], end)) if math.dist(a, b) > 4]
    for color, radius in (('#ffffff', 4), (GREEN, 2.5)):
        for a, b in connectors:
            _dots(canvas, a, b, color, radius)
    canvas.line(route, (11, 20, 26, 41), 14)
    canvas.line(route, '#ffffff', 11)
    canvas.line(route, GREEN, 6)

    canvas.circle(start[0], start[1] + 1, 10, fill=(11, 20, 26, 50))
    canvas.circle(*start, 8.5, fill='#ffffff')
    canvas.circle(*start, 5.5, fill=GREEN)
    canvas.circle(*end, 18, fill=(29, 170, 97, 64))
    canvas.circle(end[0], end[1] + 1, 11.5, fill=(11, 20, 26, 50))
    canvas.circle(*end, 9.5, fill='#ffffff')
    canvas.circle(*end, 6.5, fill=GREEN)

    # The card: a pin, "Destination" and the (shortened) name.
    icon_x = CARD_MARGIN + 16 + 18
    text_left = icon_x + 18 + 14
    max_text = max(60, width - CARD_MARGIN - text_left - CARD_PAD_RIGHT)
    name = fit_text(truncate_text(label, 90), max_text, 17, bold=True) if clean_text(label) else ''
    widest = max(text_width('Destination', 13), text_width(name, 17, True) if name else 0)
    card_w = math.ceil(text_left - CARD_MARGIN + widest + CARD_PAD_RIGHT)
    card_y = height - CARD_BOTTOM - CARD_HEIGHT
    mid_y = card_y + CARD_HEIGHT / 2

    shadow = Canvas(width, height, background=(0, 0, 0, 0), mode='RGBA')
    shadow.rect(CARD_MARGIN, card_y + 2, card_w, CARD_HEIGHT, fill=(11, 20, 26, 56), radius=14)
    canvas.image.alpha_composite(shadow.image.filter(ImageFilter.GaussianBlur(4 * canvas.s)))
    canvas.rect(CARD_MARGIN, card_y, card_w, CARD_HEIGHT, fill='#ffffff', radius=14)
    canvas.circle(icon_x, mid_y, 18, fill='#e7f6ee')
    # The pin: a round head that narrows to a point.
    canvas.circle(icon_x, mid_y - 3.5, 7, fill=GREEN)
    canvas.polygon([(icon_x - 6.2, mid_y), (icon_x + 6.2, mid_y), (icon_x, mid_y + 9)], GREEN)
    canvas.circle(icon_x, mid_y - 3.5, 2.7, fill='#ffffff')
    if name:
        canvas.text(text_left, card_y + 26, 'Destination', 13, fill=MUTED)
        canvas.text(text_left, card_y + 47, name, 17, bold=True, fill=INK)
    else:
        canvas.text(text_left, mid_y + 6, 'Destination', 17, bold=True, fill=INK)

    if credit:
        credit_w = text_width(ATTRIBUTION, 11) + 14
        canvas.rect(width - credit_w, height - 18, credit_w + 8, 26, fill=(255, 255, 255, 204), radius=5)
        canvas.text(width - 7, height - 5, ATTRIBUTION, 11, fill='#3b4a54', anchor='end')
    return canvas.result()


# ------------------------------------------------------------------ the maps

def _requests_get(url, headers, timeout):
    return requests.get(url, headers=headers, timeout=timeout)


class MapImages:
    """Route maps. http_get(url, headers, timeout) returns an object with
    status_code, content and json(), like requests.get; tests pass a fake."""

    def __init__(self, cache_dir, http_get=_requests_get, user_agent=USER_AGENT, timeout=6.0):
        self.cache_dir = Path(cache_dir)
        self.http_get = http_get
        self.headers = {'User-Agent': user_agent}
        self.timeout = timeout

    def _get(self, url, timeout=None):
        response = self.http_get(url, self.headers, timeout or self.timeout)
        if response is None or response.status_code != 200:
            raise OSError(f'HTTP {getattr(response, "status_code", None)}: {url}')
        return response

    def _write_cache(self, path, data):
        """Write through a temp file, so a half-written file is never read."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f'{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp')
            temp.write_bytes(data)
            os.replace(temp, path)
        except OSError:
            pass  # the cache is optional

    def route(self, frm, to):
        """The walking route from OSRM, or a straight line when it is missing or silly."""
        if not is_point(frm) or not is_point(to):
            raise TypeError('route needs from and to with numeric lat and lng')
        path = self.cache_dir / 'routes' / f'{route_key(frm, to)}.json'
        try:
            cached = _route_from_json(json.loads(path.read_text('utf-8')))
            if cached:
                return cached
        except (OSError, ValueError):
            pass
        found = None
        try:
            url = f'{ROUTE_URL}/{frm.lng},{frm.lat};{to.lng},{to.lat}?overview=full&geometries=geojson'
            found = pick_route(self._get(url).json(), frm, to)
        except Exception:
            pass  # offline or slow: a straight line now, and ask again next time
        if not found:
            return straight_route(frm, to)
        data = {**found, 'points': [[p.lat, p.lng] for p in found['points']]}
        self._write_cache(path, json.dumps(data).encode())
        return found

    def _tile(self, z, x, y, deadline):
        """A 256 x 256 tile from the disk cache or OSM, or None."""
        path = self.cache_dir / 'tiles' / str(z) / str(x) / f'{y}.png'
        try:
            return _square_tile(path.read_bytes())[0]
        except Exception:
            pass  # not cached yet: download it
        try:
            with _tile_slots:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None  # this map has waited long enough
                body = self._get(f'{TILE_URL}/{z}/{x}/{y}.png', min(self.timeout, left)).content
            tile, png = _square_tile(body)
            self._write_cache(path, png)
            return tile
        except Exception:
            return None

    def _tile_layer(self, zoom, origin, width, height):
        count = 2 ** zoom
        x0, x1 = math.floor(origin[0] / TILE_SIZE), math.floor((origin[0] + width - 1) / TILE_SIZE)
        y0, y1 = math.floor(origin[1] / TILE_SIZE), math.floor((origin[1] + height - 1) / TILE_SIZE)
        # Slow tiles get two timeouts in total, so a bad connection can't stall the bot.
        deadline = time.monotonic() + 2 * self.timeout
        wanted = [(tx, ty) for ty in range(max(0, y0), min(count - 1, y1) + 1) for tx in range(x0, x1 + 1)]
        with ThreadPoolExecutor(max_workers=TILE_CONCURRENCY) as pool:
            loaded = list(pool.map(lambda t: self._tile(zoom, t[0] % count, t[1], deadline), wanted))
        if not any(loaded):
            return None
        mosaic = PILImage.new('RGB', ((x1 - x0 + 1) * TILE_SIZE, (y1 - y0 + 1) * TILE_SIZE), MISSING_TILE)
        for (tx, ty), tile in zip(wanted, loaded):
            if tile:
                mosaic.paste(tile, ((tx - x0) * TILE_SIZE, (ty - y0) * TILE_SIZE))
        left, top = origin[0] - x0 * TILE_SIZE, origin[1] - y0 * TILE_SIZE
        layer = mosaic.crop((left, top, left + width, top + height))
        return ImageEnhance.Color(layer).enhance(TILE_SATURATION)

    def route_map(self, frm, to, label='', blur=False, width=800, height=500):
        """The map: tiles, walking route, markers and the destination card.
        blur=True hides the details, for the preview shown before payment."""
        if not is_point(frm) or not is_point(to):
            raise TypeError('route_map needs from and to with numeric lat and lng')
        width, height = _clamp_size(width, 800), _clamp_size(height, 500)
        found = self.route(frm, to)
        points = [frm, to, *found['points']]
        zoom = choose_zoom(points, width, height)
        origin = view_origin(points, zoom, width, height)

        tiles = self._tile_layer(zoom, origin, width, height)
        picture = (tiles or fallback_background(width, height, origin)).convert('RGBA')
        credit = tiles is not None or found['source'] == 'osrm'
        picture.alpha_composite(overlay(width, height, zoom, origin, found, frm, to, label, credit))
        picture = picture.convert('RGB')
        if blur:
            picture = picture.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
        return to_png(picture, 'route-preview.png' if blur else 'route-map.png')


def _square_tile(data):
    """Any picture in: a 256 x 256 RGB tile and its PNG bytes (raises if not a picture)."""
    with PILImage.open(BytesIO(data)) as source:
        source.load()
        if source.format == 'PNG' and source.size == (TILE_SIZE, TILE_SIZE):
            return source.convert('RGB'), data
        tile = source.convert('RGB').resize((TILE_SIZE, TILE_SIZE))
    buffer = BytesIO()
    tile.save(buffer, 'PNG')
    return tile, buffer.getvalue()


def _clamp_size(value, fallback):
    return min(2048, max(200, js_round(value))) if is_number(value) else fallback
