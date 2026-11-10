'use strict';

const fs = require('fs');
const path = require('path');
const sharp = require('sharp');
const { distanceKm } = require('./restrooms');

// Street map pictures: OpenStreetMap tiles with the walking route, the start
// and destination markers and a "Destination" label card. Tiles and routes are
// cached on disk. Without network the map is drawn on a plain street pattern,
// so a picture never fails because the internet is down. No emoji in here:
// they render monochrome, so icons are drawn with shapes.

const TILE_URL = 'https://tile.openstreetmap.org';
const ROUTE_URL = 'https://routing.openstreetmap.de/routed-foot/route/v1/driving';

const TILE_SIZE = 256;
const MIN_ZOOM = 12;
const MAX_ZOOM = 18;
const MAX_LAT = 85.0511287798;
const TILE_CONCURRENCY = 4;
const MISSING_TILE = '#f2efe9';
// Tiles are toned down a little so the green route stands out.
const TILE_SATURATION = 0.6;
const BLUR_SIGMA = 14;

const GREEN = '#1DAA61';
const INK = '#111b21';
const MUTED = '#667781';
const FONT = 'Segoe UI, Arial, Liberation Sans, Helvetica, sans-serif';
const ATTRIBUTION = '© OpenStreetMap contributors';

// Map layout, in pixels. The route stays PAD away from the edges, and the
// bottom LABEL_ROOM pixels are kept for the "Destination" card (with room for
// the destination halo, so it never hides under the card).
const PAD = 70;
const CARD_HEIGHT = 62;
const CARD_MARGIN = 16;
const CARD_BOTTOM = 24;
const CARD_PAD_RIGHT = 20;
const LABEL_ROOM = CARD_HEIGHT + CARD_BOTTOM + 24;

// ---------------------------------------------------------------- text helpers

function escapeXml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

// Squash whitespace and drop emoji (they would come out as grey boxes) and
// characters XML cannot hold. © ® ™ are plain text glyphs, so they stay.
function cleanText(text) {
  return String(text ?? '')
    .replace(/(?![©®™])\p{Extended_Pictographic}/gu, '')
    .replace(/[\p{Regional_Indicator}\p{Emoji_Modifier}‍⃣︎️\u{E0020}-\u{E007F}]/gu, '')
    .replace(/[\p{Cc}\p{Cs}￾￿]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// Cut text to maxChars, ending with an ellipsis when something was removed.
function truncateText(text, maxChars) {
  const chars = Array.from(cleanText(text));
  if (chars.length <= maxChars) return chars.join('');
  return chars.slice(0, Math.max(1, maxChars - 1)).join('').trimEnd() + '…';
}

// Width in pixels of text as the SVG renderer draws it (both go through Pango).
const textWidths = new Map();
async function measureText(text, size, bold) {
  const key = `${size}|${bold ? 1 : 0}|${text}`;
  if (!textWidths.has(key)) {
    let width;
    try {
      const markup =
        `<span font_family="${FONT}" font_weight="${bold ? 'bold' : 'normal'}" font_size="${size}pt">` +
        `${escapeXml(text)}</span>`;
      width = (await sharp({ text: { text: markup, rgba: true, dpi: 72 } }).metadata()).width;
    } catch {
      width = Math.ceil(Array.from(text).length * size * (bold ? 0.6 : 0.55));
    }
    if (textWidths.size > 500) textWidths.clear();
    textWidths.set(key, width);
  }
  return textWidths.get(key);
}

// Shorten text with an ellipsis until it is at most maxPx wide, cutting between
// words when that keeps most of it.
async function fitText(text, size, bold, maxPx) {
  const full = truncateText(text, 90);
  const fullWidth = await measureText(full, size, bold);
  if (fullWidth <= maxPx) return { text: full, width: fullWidth };

  const chars = Array.from(full.replace(/…$/, ''));
  const cut = (n) => chars.slice(0, n).join('').replace(/[\s,;:(\-–—/&]+$/, '') + '…';
  let lo = 1;
  let hi = chars.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if ((await measureText(cut(mid), size, bold)) <= maxPx) lo = mid;
    else hi = mid - 1;
  }
  const space = chars.lastIndexOf(' ', lo);
  const keep = space >= lo * 0.75 ? space : lo;
  const value = cut(keep);
  return { text: value, width: await measureText(value, size, bold) };
}

// ---------------------------------------------------------------- geometry

// Web Mercator: a longitude/latitude as a pixel on the whole-world map at this
// zoom, with 256 px tiles.
function lonLatToPixel(lng, lat, zoom) {
  const scale = TILE_SIZE * 2 ** zoom;
  const sin = Math.sin((Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * Math.PI) / 180);
  return {
    x: ((lng + 180) / 360) * scale,
    y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale,
  };
}

function straightLineMeters(a, b) {
  return distanceKm(a, b) * 1000;
}

// Is a routed walk believable for two points this far apart? A wrong snap or a
// missing footpath can turn 400 m into a detour of several kilometres.
function isPlausibleRoute(distanceM, straightM) {
  return Number.isFinite(distanceM) && distanceM <= Math.max(3 * straightM, straightM + 1000);
}

function pixelBounds(points, zoom) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of points) {
    const { x, y } = lonLatToPixel(p.lng, p.lat, zoom);
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  }
  return { minX, minY, maxX, maxY };
}

// Space for the route: the picture minus padding and the label card.
function routeArea(width, height) {
  return { w: Math.max(1, width - 2 * PAD), h: Math.max(1, height - PAD - LABEL_ROOM) };
}

// The highest zoom (12-18) where all points fit in the route area.
function chooseZoom(points, width, height) {
  const area = routeArea(width, height);
  for (let zoom = MAX_ZOOM; zoom > MIN_ZOOM; zoom--) {
    const box = pixelBounds(points, zoom);
    if (box.maxX - box.minX <= area.w && box.maxY - box.minY <= area.h) return zoom;
  }
  return MIN_ZOOM;
}

// World pixel at the picture's top-left corner, so the points' bounding box
// sits in the middle of the route area.
function viewOrigin(points, zoom, width, height) {
  const box = pixelBounds(points, zoom);
  const area = routeArea(width, height);
  return {
    x: Math.round((box.minX + box.maxX) / 2 - width / 2),
    y: Math.round((box.minY + box.maxY) / 2 - (PAD + area.h / 2)),
  };
}

function isPoint(p) {
  return Boolean(p) && Number.isFinite(p.lat) && Number.isFinite(p.lng);
}

// A GeoJSON position: [lng, lat].
function isPosition(c) {
  return Array.isArray(c) && Number.isFinite(c[0]) && Number.isFinite(c[1]);
}

// Cache file name for a route, from coordinates rounded to 5 decimals (~1 m).
function routeKey(from, to) {
  const r = (n) => n.toFixed(5);
  return `${r(from.lat)}_${r(from.lng)}__${r(to.lat)}_${r(to.lng)}`;
}

function straightRoute(from, to) {
  return { points: [from, to], distanceM: straightLineMeters(from, to), source: 'straight' };
}

// The route to draw from an OSRM answer: its walk when believable, otherwise a
// straight line. null when the answer is unusable.
function pickRoute(data, from, to) {
  const best = data && data.code === 'Ok' && Array.isArray(data.routes) && data.routes[0];
  const coords = best && best.geometry && best.geometry.coordinates;
  if (!Array.isArray(coords) || coords.length < 2 || !coords.every(isPosition)) return null;
  if (!isPlausibleRoute(best.distance, straightLineMeters(from, to))) return straightRoute(from, to);
  return { points: coords.map(([lng, lat]) => ({ lat, lng })), distanceM: best.distance, source: 'osrm' };
}

function isRoute(value) {
  return (
    Boolean(value) &&
    (value.source === 'osrm' || value.source === 'straight') &&
    Number.isFinite(value.distanceM) &&
    Array.isArray(value.points) &&
    value.points.length >= 2 &&
    value.points.every(isPoint)
  );
}

// Same seed, same number in [0, 1): keeps the offline street pattern stable.
function hash(n) {
  let h = Math.imul(n | 0, 0x9e3779b1) ^ 0x85ebca6b;
  h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d);
  h = Math.imul(h ^ (h >>> 12), 0x297a2d39);
  return ((h ^ (h >>> 15)) >>> 0) / 4294967296;
}

function clampSize(value, fallback) {
  return Number.isFinite(value) ? Math.min(2048, Math.max(200, Math.round(value))) : fallback;
}

// ---------------------------------------------------------------- SVG pieces

const num = (n) => n.toFixed(1);

// A stylised street map for when no tiles can be downloaded. Streets are laid
// out in world pixels, so the same place always looks the same.
function fallbackSvg(width, height, origin) {
  const spacing = 96;
  const line = (i, salt) => i * spacing + (hash(i * 7 + salt) - 0.5) * 30;
  const xs = [];
  const ys = [];
  for (let i = Math.floor(origin.x / spacing) - 1; i <= Math.ceil((origin.x + width) / spacing) + 1; i++) {
    xs.push([i, line(i, 11) - origin.x]);
  }
  for (let j = Math.floor(origin.y / spacing) - 1; j <= Math.ceil((origin.y + height) / spacing) + 1; j++) {
    ys.push([j, line(j, 97) - origin.y]);
  }

  // City blocks, a few of them parks.
  let blocks = '';
  for (let a = 0; a < xs.length - 1; a++) {
    for (let b = 0; b < ys.length - 1; b++) {
      const h = hash(xs[a][0] * 131 + ys[b][0] * 17 + 5);
      const fill = h < 0.1 ? '#e1ebdc' : h < 0.5 ? '#f3f5f3' : '#eef1ee';
      blocks +=
        `<rect x="${num(xs[a][1])}" y="${num(ys[b][1])}" width="${num(xs[a + 1][1] - xs[a][1])}" ` +
        `height="${num(ys[b + 1][1] - ys[b][1])}" fill="${fill}"/>`;
    }
  }

  // Streets: every third one a little wider, each with a faint edge.
  let edges = '';
  let streets = '';
  const street = (x1, y1, x2, y2, major) => {
    const d = `M${num(x1)} ${num(y1)}L${num(x2)} ${num(y2)}`;
    edges += `<path d="${d}" stroke-width="${major ? 13 : 8}"/>`;
    streets += `<path d="${d}" stroke-width="${major ? 9 : 5}"/>`;
  };
  for (const [i, x] of xs) street(x, -10, x + (hash(i) - 0.5) * 24, height + 10, i % 3 === 0);
  for (const [j, y] of ys) street(-10, y, width + 10, y + (hash(j + 500) - 0.5) * 24, j % 3 === 0);

  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">` +
    `<rect width="${width}" height="${height}" fill="#eef1ee"/>${blocks}` +
    `<g fill="none" stroke="#dde3dd" stroke-linecap="round">${edges}</g>` +
    `<g fill="none" stroke="#ffffff" stroke-linecap="round">${streets}</g>` +
    '</svg>'
  );
}

// Green location pin, drawn with shapes, centred on (cx, cy).
function pinIcon(cx, cy) {
  return (
    `<g transform="translate(${cx} ${cy - 1})">` +
    `<path d="M0 10C-1 8.5-7 3-7-2.5A7 7 0 1 1 7-2.5C7 3 1 8.5 0 10Z" fill="${GREEN}"/>` +
    '<circle cx="0" cy="-2.5" r="2.7" fill="#ffffff"/></g>'
  );
}

function circle(p, r, attrs) {
  return `<circle cx="${num(p.x)}" cy="${num(p.y)}" r="${r}" ${attrs}/>`;
}

function pathData(pixels) {
  return pixels.map((p, i) => `${i ? 'L' : 'M'}${num(p.x)} ${num(p.y)}`).join('');
}

// Drop points that land on the same pixel, to keep the SVG small.
function thin(pixels) {
  const out = [];
  for (const p of pixels) {
    const last = out[out.length - 1];
    if (!last || Math.abs(p.x - last.x) >= 0.5 || Math.abs(p.y - last.y) >= 0.5) out.push(p);
  }
  if (out.length === 1 && pixels.length > 1) out.push(pixels[pixels.length - 1]);
  return out;
}

// Route line, start and destination markers, label card and, when OSM data is
// shown, the OSM credit.
async function overlaySvg({ width, height, zoom, origin, found, from, to, label, credit }) {
  const toPx = (p) => {
    const w = lonLatToPixel(p.lng, p.lat, zoom);
    return { x: w.x - origin.x, y: w.y - origin.y };
  };
  const line = thin(found.points.map(toPx));
  const start = toPx(from);
  const end = toPx(to);
  const gap = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

  // Dotted connectors from the exact points to where the walking route begins
  // and ends (OSRM snaps both ends to the nearest path).
  let connectors = '';
  if (gap(start, line[0]) > 4) connectors += `<path d="${pathData([start, line[0]])}"/>`;
  if (gap(end, line[line.length - 1]) > 4) connectors += `<path d="${pathData([line[line.length - 1], end])}"/>`;
  const routeD = pathData(line);

  // Label card: pin, "Destination" and the (shortened) name.
  const iconX = CARD_MARGIN + 16 + 18;
  const textLeft = iconX + 18 + 14;
  const maxText = Math.max(60, width - CARD_MARGIN - textLeft - CARD_PAD_RIGHT);
  const caption = await measureText('Destination', 13, false);
  const name = cleanText(label) ? await fitText(label, 17, true, maxText) : null;
  const cardW = Math.ceil(textLeft - CARD_MARGIN + Math.max(caption, name ? name.width : 0) + CARD_PAD_RIGHT);
  const cardY = height - CARD_BOTTOM - CARD_HEIGHT;
  const midY = cardY + CARD_HEIGHT / 2;
  const texts = name
    ? `<text x="${textLeft}" y="${cardY + 26}" font-size="13" fill="${MUTED}">Destination</text>` +
      `<text x="${textLeft}" y="${cardY + 47}" font-size="17" font-weight="700" fill="${INK}">${escapeXml(name.text)}</text>`
    : `<text x="${textLeft}" y="${midY + 6}" font-size="17" font-weight="700" fill="${INK}">Destination</text>`;

  // OSM licence: credit on a light strip in the bottom-right corner.
  let creditSvg = '';
  if (credit) {
    const creditW = (await measureText(ATTRIBUTION, 11, false)) + 14;
    creditSvg =
      `<rect x="${width - creditW}" y="${height - 18}" width="${creditW + 8}" height="26" rx="5" fill="#ffffff" fill-opacity="0.8"/>` +
      `<text x="${width - 7}" y="${height - 5}" font-size="11" fill="#3b4a54" text-anchor="end">${escapeXml(ATTRIBUTION)}</text>`;
  }

  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" font-family="${FONT}">` +
    '<defs>' +
    '<filter id="card" x="-10%" y="-30%" width="120%" height="170%">' +
    '<feDropShadow dx="0" dy="2" stdDeviation="4" flood-color="#0b141a" flood-opacity="0.22"/></filter>' +
    '<filter id="dot" x="-100%" y="-100%" width="300%" height="300%">' +
    '<feDropShadow dx="0" dy="1" stdDeviation="1.5" flood-color="#0b141a" flood-opacity="0.35"/></filter>' +
    '</defs>' +
    (connectors
      ? '<g fill="none" stroke-linecap="round" stroke-dasharray="0.5 8.5">' +
        `<g stroke="#ffffff" stroke-width="8">${connectors}</g>` +
        `<g stroke="${GREEN}" stroke-width="5">${connectors}</g></g>`
      : '') +
    '<g fill="none" stroke-linecap="round" stroke-linejoin="round">' +
    `<path d="${routeD}" stroke="#0b141a" stroke-opacity="0.16" stroke-width="14"/>` +
    `<path d="${routeD}" stroke="#ffffff" stroke-width="11"/>` +
    `<path d="${routeD}" stroke="${GREEN}" stroke-width="6"/></g>` +
    circle(start, 7, `fill="${GREEN}" stroke="#ffffff" stroke-width="3" filter="url(#dot)"`) +
    circle(end, 18, `fill="${GREEN}" fill-opacity="0.25"`) +
    circle(end, 8, `fill="${GREEN}" stroke="#ffffff" stroke-width="3" filter="url(#dot)"`) +
    `<rect x="${CARD_MARGIN}" y="${cardY}" width="${cardW}" height="${CARD_HEIGHT}" rx="14" fill="#ffffff" filter="url(#card)"/>` +
    `<circle cx="${iconX}" cy="${midY}" r="18" fill="#e7f6ee"/>` +
    pinIcon(iconX, midY) +
    texts +
    creditSvg +
    '</svg>'
  );
}

// ---------------------------------------------------------------- the module

function createMapImages({
  cacheDir,
  userAgent = 'TapFlashBot/0.1 (WhatsApp restroom finder; testing)',
  fetch = globalThis.fetch,
  timeoutMs = 6000,
} = {}) {
  if (!cacheDir) throw new TypeError('createMapImages needs a cacheDir');
  const pendingTiles = new Map();

  // GET a URL and read the body with parse(), giving up after ms.
  async function request(url, parse, ms = timeoutMs) {
    const controller = new AbortController();
    let timer;
    const timeout = new Promise((resolve, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new Error(`Timed out: ${url}`));
      }, ms);
    });
    const work = (async () => {
      const res = await fetch(url, { headers: { 'User-Agent': userAgent }, signal: controller.signal });
      if (!res || !res.ok) throw new Error(`HTTP ${res && res.status}: ${url}`);
      return parse(res);
    })();
    work.catch(() => {});
    try {
      return await Promise.race([work, timeout]);
    } finally {
      clearTimeout(timer);
    }
  }

  // At most TILE_CONCURRENCY tile downloads at once, across all maps being
  // drawn (OSM tile policy). A finished download hands its slot to the next.
  let active = 0;
  const waiting = [];
  function withSlot(job) {
    const run = () => Promise.resolve().then(job).finally(() => {
      const next = waiting.shift();
      if (next) next();
      else active--;
    });
    if (active < TILE_CONCURRENCY) {
      active++;
      return run();
    }
    return new Promise((resolve) => waiting.push(resolve)).then(run);
  }

  async function readJson(file) {
    try {
      return JSON.parse(await fs.promises.readFile(file, 'utf8'));
    } catch {
      return null;
    }
  }

  // Write via a temp file so a half-written file is never read as a cache hit.
  async function writeCache(file, data) {
    try {
      await fs.promises.mkdir(path.dirname(file), { recursive: true });
      const temp = `${file}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
      await fs.promises.writeFile(temp, data);
      await fs.promises.rename(temp, file);
    } catch {
      // The cache is optional; carry on without it.
    }
  }

  // Walking route from OSRM, or a straight line when it is missing or silly.
  async function route(from, to) {
    if (!isPoint(from) || !isPoint(to)) throw new TypeError('route needs from and to with numeric lat and lng');
    const file = path.join(cacheDir, 'routes', `${routeKey(from, to)}.json`);
    const cached = await readJson(file);
    if (isRoute(cached)) return cached;

    let found = null;
    try {
      const url = `${ROUTE_URL}/${from.lng},${from.lat};${to.lng},${to.lat}?overview=full&geometries=geojson`;
      found = pickRoute(await request(url, (res) => res.json()), from, to);
    } catch {
      // Offline or slow: straight line below, and ask again next time.
    }
    if (!found) return straightRoute(from, to);
    await writeCache(file, JSON.stringify(found));
    return found;
  }

  // A 256x256 PNG tile from the disk cache or OSM; null when unavailable. Two
  // maps asking for the same tile at once share one download.
  function getTile(z, x, y, deadline) {
    const file = path.join(cacheDir, 'tiles', String(z), String(x), `${y}.png`);
    if (!pendingTiles.has(file)) {
      pendingTiles.set(file, loadTile(file, `${TILE_URL}/${z}/${x}/${y}.png`, deadline).finally(() => pendingTiles.delete(file)));
    }
    return pendingTiles.get(file);
  }

  async function loadTile(file, url, deadline) {
    try {
      return await squareTile(await fs.promises.readFile(file));
    } catch {
      // Not cached yet (or unreadable): download it.
    }
    try {
      const body = await withSlot(() => {
        const left = deadline - Date.now();
        if (left <= 0) return null; // this map has waited long enough
        return request(url, async (res) => Buffer.from(await res.arrayBuffer()), Math.min(timeoutMs, left));
      });
      if (!body) return null;
      const tile = await squareTile(body);
      await writeCache(file, tile);
      return tile;
    } catch {
      return null;
    }
  }

  // Any image in, a 256x256 PNG out (throws if it is not an image).
  async function squareTile(buffer) {
    const meta = await sharp(buffer).metadata();
    if (meta.format === 'png' && meta.width === TILE_SIZE && meta.height === TILE_SIZE) return buffer;
    return sharp(buffer).resize(TILE_SIZE, TILE_SIZE, { fit: 'fill' }).png().toBuffer();
  }

  // The tiles that cover the picture, cropped to it and toned down, as a PNG
  // that is transparent where a tile is missing. null when no tile loaded.
  async function tileLayer(zoom, origin, width, height) {
    const count = 2 ** zoom;
    const x0 = Math.floor(origin.x / TILE_SIZE);
    const x1 = Math.floor((origin.x + width - 1) / TILE_SIZE);
    const y0 = Math.floor(origin.y / TILE_SIZE);
    const y1 = Math.floor((origin.y + height - 1) / TILE_SIZE);
    // Slow tiles get two timeouts in total, so a bad connection can't stall the bot.
    const deadline = Date.now() + 2 * timeoutMs;
    const wanted = [];
    for (let ty = Math.max(0, y0); ty <= Math.min(count - 1, y1); ty++) {
      for (let tx = x0; tx <= x1; tx++) wanted.push({ tx, ty });
    }
    const loaded = await Promise.all(
      wanted.map((t) => getTile(zoom, ((t.tx % count) + count) % count, t.ty, deadline))
    );
    const layers = wanted
      .map((t, i) => ({ input: loaded[i], left: (t.tx - x0) * TILE_SIZE, top: (t.ty - y0) * TILE_SIZE }))
      .filter((layer) => layer.input);
    if (!layers.length) return null;
    try {
      const mosaic = await sharp({
        create: {
          width: (x1 - x0 + 1) * TILE_SIZE,
          height: (y1 - y0 + 1) * TILE_SIZE,
          channels: 4,
          background: { r: 0, g: 0, b: 0, alpha: 0 },
        },
      })
        .composite(layers)
        .raw()
        .toBuffer({ resolveWithObject: true });
      return await sharp(mosaic.data, { raw: mosaic.info })
        .extract({ left: origin.x - x0 * TILE_SIZE, top: origin.y - y0 * TILE_SIZE, width, height })
        .modulate({ saturation: TILE_SATURATION })
        .png({ compressionLevel: 0 })
        .toBuffer();
    } catch {
      return null; // broken tiles: use the drawn background instead
    }
  }

  // The map picture: tiles, walking route, markers and the destination label.
  // blur: true hides the details, for the preview shown before payment.
  async function routeMap(from, to, { label = '', blur = false, width = 800, height = 500 } = {}) {
    if (!isPoint(from) || !isPoint(to)) throw new TypeError('routeMap needs from and to with numeric lat and lng');
    width = clampSize(width, 800);
    height = clampSize(height, 500);

    const found = await route(from, to);
    const points = [from, to, ...found.points];
    const zoom = chooseZoom(points, width, height);
    const origin = viewOrigin(points, zoom, width, height);

    const tiles = await tileLayer(zoom, origin, width, height);
    const background = tiles || Buffer.from(fallbackSvg(width, height, origin));
    const credit = Boolean(tiles) || found.source === 'osrm';
    const overlay = Buffer.from(await overlaySvg({ width, height, zoom, origin, found, from, to, label, credit }));
    const flat = await sharp({ create: { width, height, channels: 3, background: MISSING_TILE } })
      .composite([{ input: background }, { input: overlay }])
      .raw()
      .toBuffer({ resolveWithObject: true });

    let image = sharp(flat.data, { raw: flat.info }).removeAlpha();
    if (blur) image = image.blur(BLUR_SIGMA);
    const buffer = await image.png().toBuffer();
    return {
      kind: 'image',
      buffer,
      mimetype: 'image/png',
      filename: blur ? 'route-preview.png' : 'route-map.png',
    };
  }

  return { routeMap, route };
}

module.exports = {
  createMapImages,
  lonLatToPixel,
  chooseZoom,
  viewOrigin,
  isPlausibleRoute,
  pickRoute,
  routeKey,
  escapeXml,
  cleanText,
  truncateText,
};
