'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { normalizePhone, formatMoney, feeFor } = require('../src/services/format');
const {
  distanceKm,
  describeDistance,
  demoRestroomsAround,
  findNearest,
  loadRestrooms,
  parseHours,
  isOpenAt,
  isValidLocation,
  nextClosing,
} = require('../src/services/restrooms');
const { directionsUrl } = require('../src/services/maps');
const { createUserQueue } = require('../src/util/queue');
const { isPrivateChat } = require('../src/connectors/whatsapp-web');
const { loadEnvFile, buildConfig } = require('../src/config');

test('normalizePhone understands local and international formats', () => {
  assert.equal(normalizePhone('0712345678', '254'), '+254712345678');
  assert.equal(normalizePhone('0712 345 678', '254'), '+254712345678');
  assert.equal(normalizePhone('+254 712-345-678', '254'), '+254712345678');
  assert.equal(normalizePhone('254712345678', '254'), '+254712345678');
  assert.equal(normalizePhone('00254712345678', '254'), '+254712345678');
  assert.equal(normalizePhone('712345678', '254'), '+254712345678');
  assert.equal(normalizePhone('0755123456', '255'), '+255755123456');
});

test('normalizePhone rejects things that are not phone numbers', () => {
  assert.equal(normalizePhone('12345', '254'), null);
  assert.equal(normalizePhone('07123456789', '254'), null);
  assert.equal(normalizePhone('+255712345678', '254'), null, 'other country');
  assert.equal(normalizePhone('abc', '254'), null);
  assert.equal(normalizePhone('', '254'), null);
});

test('money and fees', () => {
  assert.equal(formatMoney(100, 'KES'), 'KES 100');
  assert.equal(formatMoney(101.5, 'KES'), 'KES 101.50');
  assert.equal(feeFor(100, 1), 1);
  assert.equal(feeFor(150, 1), 1.5);
  assert.equal(feeFor(100, 0), 0);
});

test('distance and walking time', () => {
  const km = distanceKm({ lat: -1.2864, lng: 36.8172 }, { lat: -1.2921, lng: 36.8219 });
  assert.ok(km > 0.7 && km < 0.9, `expected ~0.8 km, got ${km}`);
  assert.equal(describeDistance(0.35), '350 m, about 5 min walk');
  assert.equal(describeDistance(1.234), '1.2 km, about 19 min walk');
  assert.equal(describeDistance(0), '10 m, about 1 min walk');
  assert.equal(describeDistance(3.85), '3.9 km, about 1 h walk');
  assert.equal(describeDistance(12.7), '12.7 km, about 3 h 18 min walk');
});

test('opening hours are read and checked in the bot time zone', () => {
  assert.deepEqual(parseHours('6:00 AM – 9:00 PM'), { opens: 360, closes: 1260 });
  assert.deepEqual(parseHours('06:00-21:00'), { opens: 360, closes: 1260 });
  assert.deepEqual(parseHours('24 hours'), { opens: 0, closes: 1440 });
  assert.deepEqual(parseHours('6 pm to 2 am'), { opens: 1080, closes: 120 });
  assert.equal(parseHours('weekdays only'), null);

  const nairobi = (hhmm) => new Date(`2026-10-10T${hhmm}:00+03:00`);
  const tz = 'Africa/Nairobi';
  assert.equal(isOpenAt('6:00 AM – 6:00 PM', nairobi('17:59'), tz), true);
  assert.equal(isOpenAt('6:00 AM – 6:00 PM', nairobi('18:00'), tz), false);
  assert.equal(isOpenAt('6:00 AM – 6:00 PM', nairobi('05:30'), tz), false);
  assert.equal(isOpenAt('6 pm to 2 am', nairobi('01:00'), tz), true, 'past midnight');
  assert.equal(isOpenAt('6 pm to 2 am', nairobi('12:00'), tz), false);
  assert.equal(isOpenAt('24 hours', nairobi('03:00'), tz), true);
  assert.equal(isOpenAt('', nairobi('03:00'), tz), true, 'no hours: always listed');
});

test('the next closing time is found in the bot time zone', () => {
  const nairobi = (hhmmss) => new Date(`2026-10-10T${hhmmss}+03:00`);
  const tz = 'Africa/Nairobi';
  const closes = (hours, at) => nextClosing(hours, nairobi(at), tz)?.toISOString() ?? null;
  assert.equal(closes('6:00 AM – 6:00 PM', '17:45:30'), nairobi('18:00:00').toISOString());
  assert.equal(closes('6 pm to 2 am', '23:00:00'), new Date('2026-10-11T02:00:00+03:00').toISOString(), 'past midnight');
  assert.equal(closes('6:00 AM – 10:00 PM', '22:30:00'), new Date('2026-10-11T22:00:00+03:00').toISOString(), 'closed: tomorrow');
  assert.equal(closes('24 hours', '23:00:00'), null);
  assert.equal(closes('', '23:00:00'), null);
  assert.equal(closes('weekdays only', '23:00:00'), null);
});

test('restrooms to leave out make room for the next closest', () => {
  const here = { lat: -1.2864, lng: 36.8172 };
  const all = demoRestroomsAround(here, { defaultPrice: 100 });
  const ids = (list) => list.map((r) => r.id);
  const options = { maxResults: 2, maxDistanceKm: 20 };
  assert.deepEqual(ids(findNearest(all, here, options)), ['demo-1', 'demo-2']);
  assert.deepEqual(ids(findNearest(all, here, { ...options, leaveOut: new Set(['demo-1']) })), ['demo-2', 'demo-3']);
});

test('demo restrooms near a pole or the date line get real coordinates', () => {
  const places = [[90, 0], [-90, 0], [89.9999, 10], [0, 180], [10, 179.999]];
  for (const here of places.map(([lat, lng]) => ({ lat, lng }))) {
    for (const r of demoRestroomsAround(here, { defaultPrice: 100 })) {
      assert.ok(isValidLocation(r), `${JSON.stringify(here)} -> ${r.lat},${r.lng}`);
      assert.match(directionsUrl(r), /destination=-?\d{1,2}\.\d{6}%2C-?\d{1,3}\.\d{6}&/);
    }
  }
  const [mall] = findNearest(demoRestroomsAround({ lat: 0, lng: 180 }, { defaultPrice: 100 }), { lat: 0, lng: 180 }, {
    maxResults: 1,
    maxDistanceKm: 20,
  });
  assert.ok(mall.distanceKm < 0.5, 'still next to the user across the date line');
});

test('demo restrooms surround the user and are sorted by distance', () => {
  const here = { lat: -6.17, lng: 35.74 };
  const nearest = findNearest(demoRestroomsAround(here, { defaultPrice: 100 }), here, {
    maxResults: 4,
    maxDistanceKm: 20,
  });
  assert.equal(nearest.length, 4);
  for (let i = 1; i < nearest.length; i++) assert.ok(nearest[i - 1].distanceKm <= nearest[i].distanceKm);
  assert.ok(nearest[0].distanceKm < 0.5);
});

test('loadRestrooms validates the data file', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tapflash-'));
  const file = path.join(dir, 'restrooms.json');
  const opts = { imagesDir: dir, defaultPrice: 100 };

  assert.deepEqual(loadRestrooms(path.join(dir, 'missing.json'), opts), []);

  fs.writeFileSync(file, JSON.stringify([{ id: 'a', name: 'A', lat: 1, lng: 2 }]));
  const [a] = loadRestrooms(file, opts);
  assert.equal(a.price, 100, 'default price');
  assert.equal(a.imagePath, null);

  fs.writeFileSync(file, JSON.stringify([{ id: 'a', name: 'A', lat: 'x', lng: 2 }]));
  assert.throws(() => loadRestrooms(file, opts), /"lat" and "lng"/);

  fs.writeFileSync(file, JSON.stringify([{ id: 'a', name: 'A', lat: 1, lng: 2 }, { id: 'a', name: 'B', lat: 1, lng: 2 }]));
  assert.throws(() => loadRestrooms(file, opts), /duplicate id/);

  fs.writeFileSync(file, '{ not json');
  assert.throws(() => loadRestrooms(file, opts), /Could not read/);

  fs.rmSync(dir, { recursive: true, force: true });
});

test('the bundled data/restrooms.json is valid', () => {
  const list = loadRestrooms(path.join(__dirname, '..', 'data', 'restrooms.json'), {
    imagesDir: path.join(__dirname, '..', 'data', 'images'),
    defaultPrice: 100,
  });
  assert.ok(list.length >= 4);
});

test('directions link opens walking directions without leaking the user location', () => {
  const url = directionsUrl({ lat: -1.28, lng: 36.82 });
  assert.equal(url, 'https://www.google.com/maps/dir/?api=1&destination=-1.280000%2C36.820000&travelmode=walking');
});

test('user queue runs one user in order but users in parallel', async () => {
  const enqueue = createUserQueue();
  const log = [];
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  await Promise.all([
    enqueue('a', async () => { await wait(30); log.push('a1'); }),
    enqueue('a', async () => { log.push('a2'); }),
    enqueue('b', async () => { log.push('b1'); }),
  ]);
  assert.deepEqual(log, ['b1', 'a1', 'a2']);
});

test('user queue keeps going after a task fails', async () => {
  const enqueue = createUserQueue();
  await assert.rejects(enqueue('a', async () => { throw new Error('boom'); }));
  assert.equal(await enqueue('a', async () => 'ok'), 'ok');
});

test('only private chats are answered', () => {
  assert.equal(isPrivateChat('254712345678@c.us'), true);
  assert.equal(isPrivateChat('123456789012345@lid'), true);
  assert.equal(isPrivateChat('120363000000000000@g.us'), false);
  assert.equal(isPrivateChat('status@broadcast'), false);
  assert.equal(isPrivateChat('120363000000000000@newsletter'), false);
});

test('files saved with a Windows BOM still load', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tapflash-'));
  const json = path.join(dir, 'restrooms.json');
  fs.writeFileSync(json, `﻿${JSON.stringify([{ id: 'a', name: 'A', lat: 1, lng: 2 }])}`);
  assert.equal(loadRestrooms(json, { imagesDir: dir, defaultPrice: 100 }).length, 1);

  const env = path.join(dir, '.env');
  fs.writeFileSync(env, '﻿TAPFLASH_TEST_FIRST=yes\nTAPFLASH_TEST_SECOND=ok\n');
  loadEnvFile(env);
  assert.equal(process.env.TAPFLASH_TEST_FIRST, 'yes');
  assert.equal(process.env.TAPFLASH_TEST_SECOND, 'ok');
  delete process.env.TAPFLASH_TEST_FIRST;
  delete process.env.TAPFLASH_TEST_SECOND;
  fs.rmSync(dir, { recursive: true, force: true });
});

test('an invalid TIMEZONE is rejected at startup', () => {
  assert.throws(() => buildConfig({ TIMEZONE: 'Nairobi' }), /not a valid time zone/);
  assert.equal(buildConfig({ TIMEZONE: 'Africa/Dar_es_Salaam' }).timezone, 'Africa/Dar_es_Salaam');
});
