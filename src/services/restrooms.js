'use strict';

const fs = require('fs');
const path = require('path');
const { readTextFile } = require('../config');

const EARTH_RADIUS_KM = 6371;
const WALKING_SPEED_KMH = 5;
// Streets are never a straight line, so pad the as-the-crow-flies distance.
const DETOUR_FACTOR = 1.3;

function toRadians(deg) {
  return (deg * Math.PI) / 180;
}

// Great-circle distance between two { lat, lng } points, in km.
function distanceKm(a, b) {
  const dLat = toRadians(b.lat - a.lat);
  const dLng = toRadians(b.lng - a.lng);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRadians(a.lat)) * Math.cos(toRadians(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

function walkingMinutes(km) {
  return Math.max(1, Math.round(((km * DETOUR_FACTOR) / WALKING_SPEED_KMH) * 60));
}

// "45 min", "2 h" or "3 h 18 min"
function describeMinutes(minutes) {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
}

// "350 m, about 6 min walk" or "17.9 km, about 4 h 40 min walk"
function describeDistance(km) {
  const where = km < 1 ? `${Math.max(10, Math.round((km * 1000) / 10) * 10)} m` : `${km.toFixed(1)} km`;
  return `${where}, about ${describeMinutes(walkingMinutes(km))} walk`;
}

function isValidLocation(loc) {
  return (
    loc != null &&
    Number.isFinite(loc.lat) &&
    Number.isFinite(loc.lng) &&
    Math.abs(loc.lat) <= 90 &&
    Math.abs(loc.lng) <= 180
  );
}

// Opening hours such as "6:00 AM – 9:00 PM", "06:00-21:00" or "24 hours", as
// minutes after midnight: { opens, closes }. Null when they can't be read.
const CLOCK_TIME = String.raw`(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?`;
const HOURS_RANGE = new RegExp(String.raw`^${CLOCK_TIME}\s*(?:–|—|-|to)\s*${CLOCK_TIME}$`);

function parseHours(hours) {
  const value = String(hours || '').trim().toLowerCase();
  if (/^(open )?24 ?(hours|hrs|h)$|^24\/7$/.test(value)) return { opens: 0, closes: 24 * 60 };
  const match = HOURS_RANGE.exec(value);
  if (!match) return null;
  const opens = minutesAfterMidnight(match[1], match[2], match[3]);
  const closes = minutesAfterMidnight(match[4], match[5], match[6]);
  return opens === null || closes === null ? null : { opens, closes };
}

function minutesAfterMidnight(hour, minute = '0', half = '') {
  let h = Number(hour);
  const m = Number(minute);
  if (m > 59) return null;
  if (half) {
    if (h < 1 || h > 12) return null;
    h = (h % 12) + (half === 'pm' ? 12 : 0);
  } else if (h > 24) {
    return null;
  }
  return h * 60 + m;
}

const DAY_MINUTES = 24 * 60;

// The time of day in the bot's time zone, as minutes after midnight.
function minuteOfDay(date, timezone) {
  const clock = new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone: timezone,
  });
  const [h, m] = clock.format(date).split(':').map(Number);
  return h * 60 + m;
}

// Is a restroom open at this moment, in the bot's time zone? Hours that are
// missing or can't be read count as open. "6:00 PM – 2:00 AM" runs past midnight.
function isOpenAt(hours, date, timezone) {
  const span = parseHours(hours);
  if (!span || span.opens === span.closes) return true;
  const now = minuteOfDay(date, timezone);
  return span.opens < span.closes ? now >= span.opens && now < span.closes : now >= span.opens || now < span.closes;
}

// The next time a restroom closes after `date`, as a Date. Null when it never
// closes ("24 hours", or hours that can't be read).
function nextClosing(hours, date, timezone) {
  const span = parseHours(hours);
  if (!span || span.opens % DAY_MINUTES === span.closes % DAY_MINUTES) return null;
  const minutesLeft = (span.closes - minuteOfDay(date, timezone) + DAY_MINUTES) % DAY_MINUTES || DAY_MINUTES;
  const startOfMinute = date.getTime() - (date.getTime() % 60_000);
  return new Date(startOfMinute + minutesLeft * 60_000);
}

// Will this restroom (with its distanceKm) still be open when the user gets there on foot?
function isOpenOnArrival(restroom, now, timezone) {
  const arrival = new Date(now.getTime() + walkingMinutes(restroom.distanceKm) * 60_000);
  return isOpenAt(restroom.hours, arrival, timezone);
}

// Read data/restrooms.json. A missing file just means "no restrooms yet".
function loadRestrooms(file, { imagesDir, defaultPrice }) {
  if (!fs.existsSync(file)) return [];
  let list;
  try {
    list = JSON.parse(readTextFile(file));
  } catch (err) {
    throw new Error(`Could not read ${file}: ${err.message}`);
  }
  if (!Array.isArray(list)) throw new Error(`${file} must contain a JSON array of restrooms`);

  const seen = new Set();
  return list.map((r, i) => {
    const where = `${path.basename(file)} entry #${i + 1}`;
    if (!r || typeof r.id !== 'string' || !r.id) throw new Error(`${where}: "id" is required`);
    if (seen.has(r.id)) throw new Error(`${where}: duplicate id "${r.id}"`);
    seen.add(r.id);
    if (typeof r.name !== 'string' || !r.name) throw new Error(`${where}: "name" is required`);
    if (!isValidLocation(r)) throw new Error(`${where}: "lat" and "lng" must be valid numbers`);
    if (r.price !== undefined && !(Number.isFinite(r.price) && r.price >= 0)) {
      throw new Error(`${where}: "price" must be a number`);
    }
    if (r.hours && !parseHours(r.hours)) {
      console.warn(`[restrooms] ${where}: can't read the opening hours "${r.hours}", so it is treated as always open`);
    }
    let imagePath = r.image ? path.join(imagesDir, r.image) : null;
    if (imagePath && !fs.existsSync(imagePath)) {
      console.warn(`[restrooms] ${where}: image not found at ${imagePath}, sending text only`);
      imagePath = null;
    }
    return {
      id: r.id,
      name: r.name,
      area: r.area || '',
      lat: r.lat,
      lng: r.lng,
      price: r.price ?? defaultPrice,
      hours: r.hours || '',
      imagePath,
    };
  });
}

// Sample restrooms placed around the user (offsets in km north/east). Two are
// open all night, so the demo also works late.
const DEMO_PLACES = [
  { name: 'City Mall Restroom', area: 'Shopping mall, ground floor', north: 0.25, east: 0.3 },
  { name: 'Central Bus Station Restroom', area: 'Next to the ticket office', north: -0.35, east: 0.2 },
  { name: 'Market Square Restroom', area: 'Behind the fruit market', north: 0.55, east: -0.4 },
  { name: 'Riverside Park Restroom', area: 'Park main gate', north: -0.8, east: -0.7 },
  { name: 'Fuel Station Restroom', area: 'Main road fuel station', north: 1.3, east: 0.9, hours: '24 hours' },
  { name: 'Hospital Road Restroom', area: 'Opposite the hospital', north: -1.6, east: 1.2, hours: '24 hours' },
];

// Near a pole or the date line an offset can step past 90 or 180 degrees:
// keep the latitude on the globe and wrap the longitude round to -180..180.
const clampLat = (lat) => Math.min(90, Math.max(-90, lat));
const wrapLng = (lng) => ((((lng + 180) % 360) + 360) % 360) - 180;

function demoRestroomsAround(loc, { defaultPrice }) {
  const kmPerDegLat = 110.574;
  const kmPerDegLng = 111.32 * Math.cos(toRadians(loc.lat)) || 1e-6;
  return DEMO_PLACES.map((p, i) => ({
    id: `demo-${i + 1}`,
    name: `${p.name} (demo)`,
    area: p.area,
    lat: clampLat(loc.lat + p.north / kmPerDegLat),
    lng: wrapLng(loc.lng + p.east / kmPerDegLng),
    price: defaultPrice,
    hours: p.hours || '6:00 AM – 10:00 PM',
    imagePath: null,
  }));
}

// The closest restrooms. With `at` (a Date), only the ones that will still be
// open when the user gets there. `leaveOut` (a Set of ids) skips some, and the
// next closest take their place.
function findNearest(restrooms, loc, { maxResults, maxDistanceKm, at, timezone, leaveOut }) {
  return restrooms
    .filter((r) => !leaveOut?.has(r.id))
    .map((r) => ({ ...r, distanceKm: distanceKm(loc, r) }))
    .filter((r) => r.distanceKm <= maxDistanceKm)
    .filter((r) => !at || isOpenOnArrival(r, at, timezone))
    .sort((a, b) => a.distanceKm - b.distanceKm)
    .slice(0, maxResults);
}

// The restroom source the bot uses: demo data around the user, or the JSON file.
function createRestroomService(config) {
  const opts = { imagesDir: config.imagesDir, defaultPrice: config.defaultPrice };
  const fromFile = config.demoRestroomsNearUser ? [] : loadRestrooms(config.restroomsFile, opts);
  return {
    // With `at` (now), restrooms that are closed by the time the user arrives
    // are left out, and so are the ids in `leaveOut`.
    nearest(loc, at, leaveOut) {
      const all = config.demoRestroomsNearUser ? demoRestroomsAround(loc, opts) : fromFile;
      return findNearest(all, loc, { ...config, at, leaveOut });
    },
  };
}

module.exports = {
  distanceKm,
  walkingMinutes,
  describeDistance,
  isValidLocation,
  parseHours,
  isOpenAt,
  isOpenOnArrival,
  nextClosing,
  loadRestrooms,
  demoRestroomsAround,
  findNearest,
  createRestroomService,
};
