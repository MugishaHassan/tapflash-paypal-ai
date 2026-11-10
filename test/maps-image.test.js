'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const sharp = require('sharp');
const {
  createMapImages,
  lonLatToPixel,
  chooseZoom,
  isPlausibleRoute,
  pickRoute,
  routeKey,
  escapeXml,
  truncateText,
  cleanText,
} = require('../src/services/maps-image');

const FROM = { lat: -1.2864, lng: 36.8172 };
const TO = { lat: -1.2841, lng: 36.8199 };

// A striped square, so blurring has something to smooth out.
const STRIPES =
  '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">' +
  '<rect width="64" height="64" fill="#e8e0d0"/>' +
  '<rect width="32" height="64" fill="#7aa874"/><rect y="32" width="64" height="16" fill="#3b6ea5"/></svg>';

async function makeTile(size = 256) {
  return sharp(Buffer.from(STRIPES)).resize(size, size, { kernel: 'nearest' }).png().toBuffer();
}

function toArrayBuffer(buffer) {
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.length);
}

// An OSRM answer along the straight line, with the given length in metres.
function osrmJson(from, to, distance, code = 'Ok') {
  const mid = [(from.lng + to.lng) / 2, (from.lat + to.lat) / 2 + 0.0004];
  return {
    code,
    routes: [{ distance, geometry: { coordinates: [[from.lng, from.lat], mid, [to.lng, to.lat]] } }],
  };
}

// A fake fetch that serves tiles and OSRM answers and records what was asked.
async function fakeNetwork({ tileSize = 256, routeDistance = 450, failTile, delayMs = 0 } = {}) {
  const tile = await makeTile(tileSize);
  const calls = { tiles: [], routes: [], headers: [], active: 0, maxActive: 0 };
  const fetch = async (url, options = {}) => {
    calls.headers.push(options.headers);
    if (url.startsWith('https://tile.openstreetmap.org/')) {
      calls.tiles.push(url);
      calls.active++;
      calls.maxActive = Math.max(calls.maxActive, calls.active);
      try {
        if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
        if (failTile && failTile(calls.tiles.length)) return { ok: false, status: 503 };
        return { ok: true, status: 200, arrayBuffer: async () => toArrayBuffer(tile) };
      } finally {
        calls.active--;
      }
    }
    if (url.startsWith('https://routing.openstreetmap.de/')) {
      calls.routes.push(url);
      return { ok: true, status: 200, json: async () => osrmJson(FROM, TO, routeDistance) };
    }
    return { ok: false, status: 404 };
  };
  return { fetch, calls };
}

async function sizeOf(buffer) {
  const meta = await sharp(buffer).metadata();
  return [meta.width, meta.height];
}

function tempCache(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tapflash-maps-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// How many pixels are exactly this colour. With a fetch, counts them in a fresh
// map drawn with that fetch instead.
async function countColor(buffer, [r, g, b], fetch, t) {
  if (fetch) {
    const maps = createMapImages({ cacheDir: tempCache(t), fetch });
    buffer = (await maps.routeMap(FROM, TO, { label: 'All tiles' })).buffer;
  }
  const { data, info } = await sharp(buffer).raw().toBuffer({ resolveWithObject: true });
  let count = 0;
  for (let i = 0; i < data.length; i += info.channels) {
    if (data[i] === r && data[i + 1] === g && data[i + 2] === b) count++;
  }
  return count;
}

// Sum of squared differences between neighbouring pixels: high for sharp edges,
// low once they are blurred.
async function edgeEnergy(buffer) {
  const { data, info } = await sharp(buffer).greyscale().raw().toBuffer({ resolveWithObject: true });
  let total = 0;
  for (let y = 0; y < info.height; y++) {
    for (let x = 1; x < info.width; x++) {
      total += (data[y * info.width + x] - data[y * info.width + x - 1]) ** 2;
    }
  }
  return total;
}

test('lonLatToPixel is Web Mercator with 256 px tiles', () => {
  assert.deepEqual(lonLatToPixel(0, 0, 0), { x: 128, y: 128 });
  assert.deepEqual(lonLatToPixel(-180, 0, 1), { x: 0, y: 256 });
  assert.ok(lonLatToPixel(0, 60, 2).y < 512, 'north is up');

  // Same tile as the standard slippy-map formula.
  const zoom = 17;
  const p = lonLatToPixel(FROM.lng, FROM.lat, zoom);
  const latRad = (FROM.lat * Math.PI) / 180;
  const tileX = Math.floor(((FROM.lng + 180) / 360) * 2 ** zoom);
  const tileY = Math.floor(((1 - Math.asinh(Math.tan(latRad)) / Math.PI) / 2) * 2 ** zoom);
  assert.equal(Math.floor(p.x / 256), tileX);
  assert.equal(Math.floor(p.y / 256), tileY);
});

test('chooseZoom picks the highest zoom (12-18) where the route fits', () => {
  assert.equal(chooseZoom([FROM, FROM], 800, 500), 18);
  const near = chooseZoom([FROM, TO], 800, 500);
  assert.ok(near >= 16 && near <= 18, `zoom ${near}`);
  const far = chooseZoom([FROM, { lat: FROM.lat + 0.3, lng: FROM.lng + 0.3 }], 800, 500);
  assert.equal(far, 12);
  // A bigger picture allows a closer zoom.
  assert.ok(chooseZoom([FROM, TO], 1600, 1000) >= near);
});

test('route sanity check and cache key', () => {
  assert.equal(isPlausibleRoute(450, 400), true);
  assert.equal(isPlausibleRoute(1300, 400), true, 'within straight + 1 km');
  assert.equal(isPlausibleRoute(8700, 400), false);
  assert.equal(isPlausibleRoute(NaN, 400), false);
  assert.equal(routeKey(FROM, TO), '-1.28640_36.81720__-1.28410_36.81990');
  assert.equal(routeKey({ lat: -1.2864001, lng: 36.8172001 }, TO), routeKey(FROM, TO));
});

test('pickRoute reads OSRM GeoJSON ([lng, lat]) and rejects detours', () => {
  const ok = pickRoute(osrmJson(FROM, TO, 520), FROM, TO);
  assert.equal(ok.source, 'osrm');
  assert.deepEqual(ok.points[0], { lat: FROM.lat, lng: FROM.lng });
  assert.deepEqual(ok.points[2], { lat: TO.lat, lng: TO.lng });

  const detour = pickRoute(osrmJson(FROM, TO, 8746.7), FROM, TO);
  assert.equal(detour.source, 'straight');
  assert.deepEqual(detour.points, [FROM, TO]);

  assert.equal(pickRoute(osrmJson(FROM, TO, 450, 'NoRoute'), FROM, TO), null);
  assert.equal(pickRoute({ code: 'Ok', routes: [{ distance: 1, geometry: { coordinates: [[1, 'x']] } }] }, FROM, TO), null);
  assert.equal(pickRoute(null, FROM, TO), null);
});

test('text helpers escape, clean and truncate', () => {
  assert.equal(escapeXml(`<a href="x">Tom & 'Jerry'</a>`), '&lt;a href=&quot;x&quot;&gt;Tom &amp; &apos;Jerry&apos;&lt;/a&gt;');
  assert.equal(truncateText('Short', 10), 'Short');
  assert.equal(truncateText('A very long restroom name', 10), 'A very lo…');
  assert.equal(cleanText('  City\n Mall 🚻  '), 'City Mall');
  assert.equal(cleanText('Café™ 🇰🇪 👍🏽 ❤️ (24h)'), 'Café™ (24h)');
});

test('routeMap returns a PNG of the requested size', async (t) => {
  const net = await fakeNetwork();
  const maps = createMapImages({ cacheDir: tempCache(t), fetch: net.fetch });

  const image = await maps.routeMap(FROM, TO, { label: 'City Mall Restroom (demo)' });
  assert.equal(image.kind, 'image');
  assert.equal(image.mimetype, 'image/png');
  assert.match(image.filename, /\.png$/);
  const meta = await sharp(image.buffer).metadata();
  assert.equal(meta.format, 'png');
  assert.equal(meta.width, 800);
  assert.equal(meta.height, 500);

  const small = await maps.routeMap(FROM, TO, { label: 'Small', width: 480, height: 360 });
  assert.deepEqual(await sizeOf(small.buffer), [480, 360]);
});

test('requests carry the User-Agent and the documented URLs', async (t) => {
  const net = await fakeNetwork({ routeDistance: 450 });
  const maps = createMapImages({
    cacheDir: tempCache(t),
    fetch: net.fetch,
    userAgent: 'TestAgent/1.0',
  });
  await maps.routeMap(FROM, TO, { label: 'x' });

  assert.ok(net.calls.tiles.length > 0);
  assert.ok(net.calls.tiles.every((u) => /^https:\/\/tile\.openstreetmap\.org\/\d+\/\d+\/\d+\.png$/.test(u)));
  assert.equal(net.calls.routes.length, 1);
  assert.equal(
    net.calls.routes[0],
    'https://routing.openstreetmap.de/routed-foot/route/v1/driving/' +
      '36.8172,-1.2864;36.8199,-1.2841?overview=full&geometries=geojson'
  );
  assert.ok(net.calls.headers.every((h) => h['User-Agent'] === 'TestAgent/1.0'));
});

test('tiles that are not 256x256 are resized instead of breaking the map', async (t) => {
  const net = await fakeNetwork({ tileSize: 8 });
  const maps = createMapImages({ cacheDir: tempCache(t), fetch: net.fetch });
  const image = await maps.routeMap(FROM, TO, { label: 'Tiny tiles' });
  assert.deepEqual(await sizeOf(image.buffer), [800, 500]);
});

test('blur: true gives a much smoother picture of the same size', async (t) => {
  const net = await fakeNetwork();
  const maps = createMapImages({ cacheDir: tempCache(t), fetch: net.fetch });

  const sharpImage = await maps.routeMap(FROM, TO, { label: 'City Mall Restroom (demo)' });
  const blurred = await maps.routeMap(FROM, TO, { label: 'City Mall Restroom (demo)', blur: true });

  assert.equal(blurred.mimetype, 'image/png');
  assert.deepEqual(await sizeOf(blurred.buffer), [800, 500]);
  assert.notEqual(blurred.filename, sharpImage.filename);
  const before = await edgeEnergy(sharpImage.buffer);
  const after = await edgeEnergy(blurred.buffer);
  assert.ok(after < before * 0.3, `blurred ${after} should be well below sharp ${before}`);
});

test('a fetch that always rejects still gives an image (offline background)', async (t) => {
  const fetch = () => Promise.reject(new Error('offline'));
  const cacheDir = tempCache(t);
  const maps = createMapImages({ cacheDir, fetch });

  const image = await maps.routeMap(FROM, TO, { label: 'Offline Restroom' });
  assert.equal((await sharp(image.buffer).metadata()).format, 'png');
  assert.deepEqual(await sizeOf(image.buffer), [800, 500]);

  // The stylised background is not one flat colour.
  const stats = await sharp(image.buffer).stats();
  assert.ok(stats.channels[0].stdev > 3);

  const found = await maps.route(FROM, TO);
  assert.equal(found.source, 'straight');
  assert.deepEqual(found.points, [FROM, TO]);
  // Nothing cached, so the route is asked for again once back online.
  assert.equal(fs.existsSync(path.join(cacheDir, 'routes')), false);

  const blurred = await maps.routeMap(FROM, TO, { label: 'Offline Restroom', blur: true });
  assert.deepEqual(await sizeOf(blurred.buffer), [800, 500]);
});

test('a fetch that hangs is given up on after timeoutMs', async (t) => {
  const maps = createMapImages({
    cacheDir: tempCache(t),
    fetch: () => new Promise(() => {}),
    timeoutMs: 30,
  });
  const started = Date.now();
  const image = await maps.routeMap(FROM, TO, { label: 'Slow network' });
  assert.deepEqual(await sizeOf(image.buffer), [800, 500]);
  assert.ok(Date.now() - started < 5000);
});

test('tiles that fail are filled with #f2efe9, the rest of the map is still drawn', async (t) => {
  const net = await fakeNetwork({ failTile: (n) => n % 2 === 0 });
  const maps = createMapImages({ cacheDir: tempCache(t), fetch: net.fetch });
  const image = await maps.routeMap(FROM, TO, { label: 'Patchy network' });
  assert.deepEqual(await sizeOf(image.buffer), [800, 500]);
  assert.ok(net.calls.tiles.length > 1);

  const count = await countColor(image.buffer, [0xf2, 0xef, 0xe9]);
  assert.ok(count > 20000, `${count} missing-tile pixels`);
  const tiled = await countColor(image.buffer, [0xf2, 0xef, 0xe9], (await fakeNetwork()).fetch, t);
  assert.equal(tiled, 0, 'no missing-tile colour when every tile loads');
});

test('an absurdly long OSRM route falls back to a straight line', async (t) => {
  const net = await fakeNetwork({ routeDistance: 8700 });
  const maps = createMapImages({ cacheDir: tempCache(t), fetch: net.fetch });

  const found = await maps.route(FROM, TO);
  assert.equal(found.source, 'straight');
  assert.deepEqual(found.points, [FROM, TO]);
  assert.ok(found.distanceM > 300 && found.distanceM < 500, `distance ${found.distanceM}`);

  const image = await maps.routeMap(FROM, TO, { label: 'Detour' });
  assert.deepEqual(await sizeOf(image.buffer), [800, 500]);
});

test('a believable OSRM route is used, and a bad OSRM answer is not', async (t) => {
  const good = await fakeNetwork({ routeDistance: 520 });
  const maps = createMapImages({ cacheDir: tempCache(t), fetch: good.fetch });
  const found = await maps.route(FROM, TO);
  assert.equal(found.source, 'osrm');
  assert.equal(found.distanceM, 520);
  assert.equal(found.points.length, 3);
  assert.ok(found.points.every((p) => Number.isFinite(p.lat) && Number.isFinite(p.lng)));

  const badFetch = async () => ({
    ok: true,
    status: 200,
    json: async () => osrmJson(FROM, TO, 450, 'NoRoute'),
    arrayBuffer: async () => toArrayBuffer(await makeTile()),
  });
  const other = createMapImages({ cacheDir: tempCache(t), fetch: badFetch });
  assert.equal((await other.route(FROM, TO)).source, 'straight');
});

test('routes and tiles are cached: a second call makes no requests', async (t) => {
  const cacheDir = tempCache(t);
  const net = await fakeNetwork({ routeDistance: 450 });
  const maps = createMapImages({ cacheDir, fetch: net.fetch });

  await maps.routeMap(FROM, TO, { label: 'Cached' });
  const tileCalls = net.calls.tiles.length;
  const routeCalls = net.calls.routes.length;
  assert.ok(tileCalls > 0);
  assert.equal(routeCalls, 1);
  assert.ok(fs.existsSync(path.join(cacheDir, 'routes', `${routeKey(FROM, TO)}.json`)));
  const [, z, x, file] = new URL(net.calls.tiles[0]).pathname.split('/');
  assert.ok(fs.existsSync(path.join(cacheDir, 'tiles', z, x, file)));

  await maps.routeMap(FROM, TO, { label: 'Cached', blur: true });
  assert.equal(net.calls.tiles.length, tileCalls, 'tiles served from cache');
  assert.equal(net.calls.routes.length, routeCalls, 'route served from cache');

  // A fresh instance sharing the same folder reuses the files too.
  const again = createMapImages({ cacheDir, fetch: net.fetch });
  await again.routeMap(FROM, TO, { label: 'Cached' });
  assert.equal(net.calls.tiles.length, tileCalls);
  assert.equal(net.calls.routes.length, routeCalls);
});

test('at most 4 tiles are downloaded at a time, and no tile twice at once', async (t) => {
  const net = await fakeNetwork({ delayMs: 15 });
  const maps = createMapImages({ cacheDir: tempCache(t), fetch: net.fetch });

  // Two maps of the same place and one elsewhere, all at once.
  await Promise.all([
    maps.routeMap(FROM, TO, { label: 'One' }),
    maps.routeMap(FROM, TO, { label: 'Two' }),
    maps.routeMap({ lat: 0.5, lng: 30 }, { lat: 0.502, lng: 30.002 }, { label: 'Three' }),
  ]);
  assert.ok(net.calls.tiles.length > 8, 'needs several tiles for this check');
  assert.ok(net.calls.maxActive <= 4, `active ${net.calls.maxActive}`);
  assert.equal(net.calls.maxActive, 4, 'downloads do run in parallel');
  assert.equal(new Set(net.calls.tiles).size, net.calls.tiles.length, 'each tile fetched once');
});

test('user text is escaped and shortened, never breaking the image', async (t) => {
  const net = await fakeNetwork();
  const maps = createMapImages({ cacheDir: tempCache(t), fetch: net.fetch });

  const nasty = `<svg onload="x"> & 'quotes' "double" ${'very long name '.repeat(20)}`;
  const image = await maps.routeMap(FROM, TO, { label: nasty });
  assert.deepEqual(await sizeOf(image.buffer), [800, 500]);

  const noLabel = await maps.routeMap(FROM, TO);
  assert.deepEqual(await sizeOf(noLabel.buffer), [800, 500]);
});

test('same start and destination, or bad coordinates', async (t) => {
  const net = await fakeNetwork();
  const maps = createMapImages({ cacheDir: tempCache(t), fetch: net.fetch });

  const same = await maps.routeMap(FROM, FROM, { label: 'Right here' });
  assert.deepEqual(await sizeOf(same.buffer), [800, 500]);

  await assert.rejects(() => maps.routeMap({ lat: 'x', lng: 1 }, TO), TypeError);
  await assert.rejects(() => maps.route(FROM, null), TypeError);
  assert.throws(() => createMapImages({}), TypeError);
});
