'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const sharp = require('sharp');
const QRCode = require('qrcode');
const { createCards, escapeXml, fitText, FALLBACK_ROOM } = require('../src/services/cards');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'tapflash-cards-'));
test.after(() => fs.rmSync(tmp, { recursive: true, force: true }));

// A flat colour PNG, for photos and icons.
async function solidPng(file, { r, g, b }, size = 300) {
  await sharp({ create: { width: size, height: size * 0.7, channels: 3, background: { r, g, b } } })
    .png()
    .toFile(file);
  return file;
}

// How many pixels are close to the given colour.
async function countPixels(buffer, { r, g, b }, tolerance = 40) {
  const { data, info } = await sharp(buffer).removeAlpha().raw().toBuffer({ resolveWithObject: true });
  let count = 0;
  for (let i = 0; i < data.length; i += info.channels) {
    if (
      Math.abs(data[i] - r) <= tolerance &&
      Math.abs(data[i + 1] - g) <= tolerance &&
      Math.abs(data[i + 2] - b) <= tolerance
    ) {
      count++;
    }
  }
  return count;
}

// Pixels in a region that are clearly not white (text, lines, pictures).
async function inkPixels(buffer, region) {
  const { data, info } = await sharp(buffer).extract(region).removeAlpha().raw().toBuffer({ resolveWithObject: true });
  let count = 0;
  for (let i = 0; i < data.length; i += info.channels) {
    if (data[i] < 200 || data[i + 1] < 200 || data[i + 2] < 200) count++;
  }
  return count;
}

async function assertPng(img, { width = 800, height } = {}) {
  assert.equal(img.kind, 'image');
  assert.equal(img.mimetype, 'image/png');
  assert.match(img.filename, /\.png$/);
  assert.ok(Buffer.isBuffer(img.buffer));
  const meta = await sharp(img.buffer).metadata();
  assert.equal(meta.format, 'png');
  assert.equal(meta.width, width);
  if (height) assert.equal(meta.height, height);
  return meta;
}

const restroom = (n, extra = {}) => ({
  id: `r${n}`,
  name: `Restroom ${n}`,
  area: 'Main road',
  lat: -1.29,
  lng: 36.82,
  price: 100,
  hours: '6:00 AM – 10:00 PM',
  imagePath: null,
  distanceKm: 0.2 * n,
  ...extra,
});

const METHODS = [
  { id: 'mpesa', label: 'M-Pesa', needsPhone: true, feePercent: 0 },
  { id: 'airtel', label: 'Airtel Money', needsPhone: true, feePercent: 0 },
  { id: 'card', label: 'Card (Visa / Mastercard)', needsPhone: false, feePercent: 1 },
  { id: 'paypal', label: 'PayPal', needsPhone: false, feePercent: 1 },
];

const NASTY = `Tom & Jerry's <b>"Place"</b> & more`;

function newCards(extra = {}) {
  return createCards({ currency: 'KES', timezone: 'Africa/Nairobi', paymentIconsDir: null, ...extra });
}

function newPass(extra = {}) {
  return {
    reference: 'TF0DC8FDE4DE',
    facility: restroom(1),
    total: 101,
    otp: '123456',
    paidAt: new Date('2026-10-10T10:00:00Z'),
    expiresAt: new Date('2026-10-10T11:00:00Z'),
    ...extra,
  };
}

test('escapeXml and fitText', () => {
  assert.equal(escapeXml(`a & b < c > "d" 'e'`), 'a &amp; b &lt; c &gt; &quot;d&quot; &#39;e&#39;');
  assert.equal(escapeXml('bad\u0000char'), 'badchar');
  assert.equal(fitText('Short', 400, 26, true), 'Short');
  const cut = fitText('Central Bus Station Restroom (demo) with a very long tail', 300, 26, true);
  assert.ok(cut.endsWith('…'));
  assert.ok(cut.length < 30);
  // (widths are for Segoe UI; without it, text gets FALLBACK_ROOM more room)
  assert.equal(fitText('Central Bus Station Restroom (demo)', 330 * FALLBACK_ROOM, 26, true), 'Central Bus Station…');
  assert.equal(fitText('Central Bus Station Restroom (demo)', 448 * FALLBACK_ROOM, 25, true), 'Central Bus Station Restroom (demo)');
  // emoji are dropped, they would render as grey outlines
  assert.equal(fitText('Mall 🚻 Restroom ✨', 400, 26), 'Mall Restroom ✨');
});

test('restroomList renders a PNG and grows with the number of options', async () => {
  const cards = newCards();
  const one = await cards.restroomList([restroom(1)]);
  const four = await cards.restroomList([1, 2, 3, 4].map((n) => restroom(n)));
  const m1 = await assertPng(one);
  const m4 = await assertPng(four);
  assert.ok(m4.height > m1.height + 300, `${m4.height} should be much taller than ${m1.height}`);
  assert.equal(one.filename, 'restrooms.png');
});

test('restroomList copes with an empty list and missing details', async () => {
  const cards = newCards();
  await assertPng(await cards.restroomList([]));
  await assertPng(await cards.restroomList([{ id: 'x', name: 'Bare restroom' }]));
});

test('restroomList uses the photo at imagePath', async () => {
  const cards = newCards();
  const red = { r: 220, g: 20, b: 20 };
  const photo = await solidPng(path.join(tmp, 'photo.png'), red);

  const without = await cards.restroomList([restroom(1)]);
  const withPhoto = await cards.restroomList([restroom(1, { imagePath: photo })]);
  await assertPng(withPhoto);
  assert.equal(await countPixels(without.buffer, red), 0);
  assert.ok((await countPixels(withPhoto.buffer, red)) > 15000, 'thumbnail should be filled with the photo');
});

test('restroomList falls back to the illustration when the photo cannot be read', async () => {
  const cards = newCards();
  const broken = path.join(tmp, 'broken.png');
  fs.writeFileSync(broken, 'not an image');
  const warn = console.warn;
  console.warn = () => {};
  try {
    await assertPng(await cards.restroomList([restroom(1, { imagePath: broken })]));
    await assertPng(await cards.restroomPhoto(restroom(1, { imagePath: path.join(tmp, 'missing.jpg') })));
  } finally {
    console.warn = warn;
  }
});

test('names with & < > " \' do not break rendering', async () => {
  const cards = newCards();
  const nasty = restroom(1, { name: NASTY, area: `Behind "Joe's" <shop> & café` });
  await assertPng(await cards.restroomList([nasty, restroom(2, { name: '<script>x</script>' })]));
  await assertPng(await cards.restroomPhoto(nasty), { height: 500 });
  await assertPng(await cards.paymentMethods([{ ...METHODS[0], label: NASTY }]));
  await assertPng(await cards.accessCard(newPass({ facility: nasty, reference: 'A&B<1>' }), await QRCode.toBuffer('x')));
});

test('very long names are truncated instead of overflowing', async () => {
  const cards = newCards();
  const long = restroom(1, { name: 'W'.repeat(200), area: 'Z'.repeat(300) });
  const a = await cards.restroomList([long]);
  const b = await cards.restroomList([restroom(1)]);
  const meta = await sharp(a.buffer).metadata();
  assert.equal(meta.height, (await sharp(b.buffer).metadata()).height);
  // the strip between the text column and the card edge stays empty
  const strip = { left: 762, top: 140, width: 16, height: meta.height - 170 };
  assert.equal(await inkPixels(a.buffer, strip), 0);
  await assertPng(await cards.restroomPhoto(long), { height: 500 });
  await assertPng(await cards.accessCard(newPass({ facility: long }), await QRCode.toBuffer('x')));
});

test('restroomPhoto draws the illustration or the photo at 800x500', async () => {
  const cards = newCards();
  const placeholder = await cards.restroomPhoto(restroom(1));
  await assertPng(placeholder, { height: 500 });
  assert.equal(placeholder.filename, 'restroom-r1.png');

  const blue = { r: 20, g: 40, b: 220 };
  const photo = await solidPng(path.join(tmp, 'wide.png'), blue, 1000);
  const real = await cards.restroomPhoto(restroom(1, { imagePath: photo }));
  await assertPng(real, { height: 500 });
  // the photo fills the picture; the name sits in white on a dark fade at the bottom
  assert.ok((await countPixels(real.buffer, blue)) > 250000);
  const bottom = await sharp(real.buffer).extract({ left: 0, top: 380, width: 800, height: 120 }).png().toBuffer();
  assert.ok((await countPixels(bottom, { r: 255, g: 255, b: 255 }, 30)) > 500, 'name should be drawn on the photo');
});

test('paymentMethods renders with a missing or unused paymentIconsDir', async () => {
  const noDir = await newCards({ paymentIconsDir: undefined }).paymentMethods(METHODS);
  const missing = await newCards({ paymentIconsDir: path.join(tmp, 'does-not-exist') }).paymentMethods(METHODS);
  const m = await assertPng(noDir);
  assert.ok(m.height > 380);
  await assertPng(missing, { height: m.height });
  await assertPng(await newCards().paymentMethods([]));
  // an id the bot does not know still gets a badge
  await assertPng(await newCards().paymentMethods([{ id: 'bank', label: 'Bank transfer', feePercent: 2.5 }]));
});

test('paymentMethods uses a custom icon from paymentIconsDir', async () => {
  const dir = path.join(tmp, 'icons');
  fs.mkdirSync(dir);
  const magenta = { r: 255, g: 0, b: 255 };
  await sharp({ create: { width: 128, height: 128, channels: 3, background: magenta } })
    .png()
    .toFile(path.join(dir, 'mpesa.png'));

  const plain = await newCards().paymentMethods(METHODS);
  const custom = await newCards({ paymentIconsDir: dir }).paymentMethods(METHODS);
  await assertPng(custom);
  assert.equal(await countPixels(plain.buffer, magenta), 0);
  assert.ok((await countPixels(custom.buffer, magenta)) > 3000, 'custom icon should be drawn');
});

test('accessCard renders the pass with a QR code', async () => {
  const cards = newCards();
  const qr = await QRCode.toBuffer('x');
  const card = await cards.accessCard(newPass(), qr);
  await assertPng(card);
  assert.equal(card.filename, 'access-TF0DC8FDE4DE.png');

  // a QR code makes the card taller than one without
  const noQr = await cards.accessCard(newPass(), null);
  const withQr = (await sharp(card.buffer).metadata()).height;
  assert.ok(withQr > (await sharp(noQr.buffer).metadata()).height + 200);
  assert.ok((await countPixels(card.buffer, { r: 0, g: 0, b: 0 }, 30)) > 4000, 'QR modules should be on the card');
});

test('accessCard copes with missing values and a buffer that is not an image', async () => {
  const cards = newCards();
  const warn = console.warn;
  console.warn = () => {};
  try {
    await assertPng(await cards.accessCard(newPass({ facility: null, total: undefined, expiresAt: null }), null));
    await assertPng(await cards.accessCard(newPass(), Buffer.from('not a png')));
  } finally {
    console.warn = warn;
  }
});
