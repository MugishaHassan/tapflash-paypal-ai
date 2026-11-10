'use strict';

const fs = require('fs');
const path = require('path');
const sharp = require('sharp');
const { formatMoney, formatTime } = require('./format');
const { describeDistance } = require('./restrooms');

// Pictures the bot sends in the chat: the restroom list, a restroom photo, the
// payment method list and the access card. Everything is drawn as SVG and
// rendered to PNG with sharp. No emoji in here: they render monochrome, so
// icons are drawn with shapes.

const WIDTH = 800;
const PAD = 16; // grey margin around the white card
const INSET = 28; // space between the card edge and its content
const LEFT = PAD + INSET;
const RIGHT = WIDTH - PAD - INSET;
const FONT = 'Segoe UI, Arial, Liberation Sans, Helvetica, sans-serif';

const COLOR = {
  bg: '#f0f2f5',
  card: '#ffffff',
  text: '#111b21',
  muted: '#667781',
  green: '#1DAA61',
  blue: '#0b84d8',
  line: '#e9edef',
};

const THUMB_W = 180;
const THUMB_H = 120;
const ICON_SIZE = 64;
const QR_SIZE = 260;
const PHOTO_W = 800;
const PHOTO_H = 500;

// ---------------------------------------------------------------- text helpers

// Drop characters XML cannot hold and colour emoji (they would come out as
// grey outlines or empty boxes), then squash whitespace.
function clean(value) {
  return String(value ?? '')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F￾￿]/g, '')
    .replace(/[\u{1F000}-\u{1FAFF}\u{E0020}-\u{E007F}️‍⃣]/gu, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// & < > " ' made safe for SVG text and attributes.
function escapeXml(value) {
  return clean(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Advance widths of Segoe UI for ASCII 32-126, in 1/100 em (measured).
const ASCII_REGULAR = [
  27, 28, 39, 59, 54, 82, 80, 23, 30, 30, 42, 68, 22, 40, 22, 39, 54, 54, 54, 54, 54, 54, 54, 54,
  54, 54, 22, 22, 68, 68, 68, 45, 96, 65, 57, 59, 70, 51, 49, 69, 71, 27, 32, 58, 47, 90, 75, 75,
  56, 75, 60, 53, 54, 69, 62, 93, 59, 55, 57, 30, 38, 30, 68, 42, 27, 51, 59, 46, 59, 52, 31, 59,
  57, 24, 24, 50, 24, 86, 57, 59, 59, 59, 35, 42, 34, 57, 48, 72, 46, 48, 45, 30, 24, 30, 68,
];
const ASCII_BOLD = [
  28, 33, 49, 59, 58, 87, 85, 29, 37, 37, 46, 71, 27, 40, 27, 44, 58, 58, 58, 58, 58, 58, 58, 58,
  58, 58, 27, 27, 71, 71, 71, 44, 95, 70, 64, 59, 74, 53, 52, 71, 77, 32, 42, 65, 51, 96, 79, 76,
  61, 76, 65, 56, 61, 72, 67, 100, 66, 61, 61, 37, 44, 37, 71, 42, 31, 54, 62, 48, 62, 54, 38, 62,
  60, 28, 30, 56, 28, 92, 61, 61, 62, 62, 40, 44, 39, 61, 54, 80, 55, 54, 48, 37, 33, 37, 71,
];
const SPECIAL = { '·': 27, '…': 91, '–': 50, '—': 100, '‘': 23, '’': 23, '“': 40, '”': 40, '€': 58 };

// Width of one character in em. Accented letters count as their base letter.
function glyphEm(ch, bold) {
  const table = bold ? ASCII_BOLD : ASCII_REGULAR;
  const base = ch.normalize('NFD')[0];
  const code = base.charCodeAt(0);
  if (code >= 32 && code < 127) return table[code - 32] / 100;
  if (SPECIAL[ch]) return SPECIAL[ch] / 100;
  if (ch.codePointAt(0) >= 0x2e80) return 1; // CJK and other wide scripts
  return bold ? 0.75 : 0.68; // Cyrillic, Greek, symbols: err on the wide side
}

// Rendered width in pixels, within a few percent. Segoe UI comes with Windows;
// elsewhere (a Linux server) Arial or Liberation Sans is drawn instead, which is
// up to 8% wider on some letters, so there text is cut slightly early rather
// than overflow the card.
const FALLBACK_ROOM = process.platform === 'win32' ? 1 : 1.08;
function textWidth(value, size, bold = false) {
  let em = 0;
  for (const ch of String(value)) em += glyphEm(ch, bold);
  return em * size * FALLBACK_ROOM;
}

// Cut text with an ellipsis so it fits maxWidth pixels. Prefers cutting between
// words, unless that would throw away too much.
function fitText(value, maxWidth, size, bold = false) {
  const text = clean(value);
  if (textWidth(text, size, bold) <= maxWidth) return text;
  const all = Array.from(text);
  const chars = [...all];
  while (chars.length > 1 && textWidth(`${chars.join('')}…`, size, bold) > maxWidth) chars.pop();
  let kept = chars.join('');
  const midWord = all[chars.length] !== ' ';
  const lastSpace = kept.lastIndexOf(' ');
  if (midWord && lastSpace > kept.length * 0.5) kept = kept.slice(0, lastSpace);
  return `${kept.replace(/[\s.,;:·–—&+/(-]+$/, '')}…`;
}

// One SVG <text>. Pass maxWidth to truncate with an ellipsis.
function textEl(x, y, value, { size, weight = 400, fill = COLOR.text, anchor = 'start', maxWidth, spacing } = {}) {
  const shown = maxWidth ? fitText(value, maxWidth, size, weight >= 600) : clean(value);
  const extra = spacing ? ` letter-spacing="${spacing}"` : '';
  return (
    `<text x="${x}" y="${y}" font-size="${size}" font-weight="${weight}" fill="${fill}" ` +
    `text-anchor="${anchor}"${extra}>${escapeXml(shown)}</text>`
  );
}

// ---------------------------------------------------------------- svg helpers

function svgDoc(width, height, body) {
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" ` +
    `viewBox="0 0 ${width} ${height}" font-family="${FONT}">${body}</svg>`
  );
}

// White rounded card on the light chat-grey background.
function cardDoc(height, body) {
  return svgDoc(
    WIDTH,
    height,
    `<rect width="${WIDTH}" height="${height}" fill="${COLOR.bg}"/>` +
      `<rect x="${PAD + 0.5}" y="${PAD + 0.5}" width="${WIDTH - 2 * PAD - 1}" height="${height - 2 * PAD - 1}" ` +
      `rx="24" fill="${COLOR.card}" stroke="#e4e8eb"/>${body}`,
  );
}

function separator(y) {
  return `<line x1="${LEFT}" y1="${y}" x2="${RIGHT}" y2="${y}" stroke="${COLOR.line}" stroke-width="2"/>`;
}

async function render(svg) {
  return sharp(Buffer.from(svg)).png().toBuffer();
}

function pngImage(buffer, filename) {
  return { kind: 'image', buffer, mimetype: 'image/png', filename };
}

// Keep ids safe to use in a file name.
function slug(value) {
  return clean(value).replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'x';
}

// A photo from disk cropped to fill w x h, as a sharp pipeline. Null if it
// cannot be read. Read into a buffer first so Windows never keeps the file locked.
async function loadPhoto(file, w, h) {
  if (!file) return null;
  try {
    const source = await fs.promises.readFile(file);
    const jpeg = await sharp(source).rotate().resize(w, h, { fit: 'cover' }).jpeg({ quality: 90 }).toBuffer();
    return jpeg;
  } catch (err) {
    console.warn(`[cards] could not use image ${file}: ${err.message}`);
    return null;
  }
}

function dataUri(buffer, mimetype) {
  return `data:${mimetype};base64,${buffer.toString('base64')}`;
}

// ----------------------------------------------------------------- placeholder

let uid = 0;

// Two simple figures on a sign plate, in a 120 x 100 box.
function restroomSign(x, y, scale) {
  return (
    `<g transform="translate(${x.toFixed(2)} ${y.toFixed(2)}) scale(${scale.toFixed(4)})">` +
    '<rect x="0" y="5" width="120" height="100" rx="18" fill="#0b3b40" opacity="0.14"/>' +
    '<rect width="120" height="100" rx="18" fill="#ffffff" opacity="0.96"/>' +
    '<line x1="60" y1="17" x2="60" y2="83" stroke="#b9d5d7" stroke-width="2" stroke-linecap="round"/>' +
    '<g fill="#2a7f86">' +
    '<circle cx="30" cy="26" r="8.5"/>' +
    '<rect x="19" y="39" width="22" height="27" rx="8"/>' +
    '<rect x="22" y="58" width="7" height="26" rx="3.5"/>' +
    '<rect x="31" y="58" width="7" height="26" rx="3.5"/>' +
    '<circle cx="90" cy="26" r="8.5"/>' +
    '<path d="M90 37.5C85 37.5 82 40.5 80 46L73 68H107L100 46C98 40.5 95 37.5 90 37.5Z"/>' +
    '<rect x="83" y="66" width="6" height="18" rx="3"/>' +
    '<rect x="91" y="66" width="6" height="18" rx="3"/>' +
    '</g></g>'
  );
}

// Soft tiled picture with a restroom sign, for restrooms without a photo.
// Draws inside the box (x, y, w, h); the caller clips the corners if needed.
function placeholder({ x, y, w, h, name = '', caption = 'Photo coming soon' }) {
  const id = `ph${uid++}`;
  const tile = Math.max(12, Math.round(w / 10));
  const hasName = Boolean(clean(name));

  const signH = h * (hasName ? 0.36 : 0.5);
  const scale = signH / 100;
  const signW = 120 * scale;
  const nameSize = Math.round(h * 0.075);
  const capSize = hasName ? Math.round(h * 0.044) : Math.max(10, Math.round(h * 0.085));
  const gap = h * (hasName ? 0.065 : 0.06);
  const block = signH + gap + (hasName ? nameSize + h * 0.03 : 0) + capSize;
  const top = y + (h - block) / 2;

  let labels = '';
  let cursor = top + signH + gap;
  if (hasName) {
    cursor += nameSize * 0.8;
    labels += textEl(x + w / 2, cursor.toFixed(1), name, {
      size: nameSize,
      weight: 700,
      fill: '#1b4f55',
      anchor: 'middle',
      maxWidth: w - 80,
    });
    cursor += nameSize * 0.2 + h * 0.03;
  }
  cursor += capSize * 0.8;
  labels += textEl(x + w / 2, cursor.toFixed(1), caption, {
    size: capSize,
    fill: '#4f7e83',
    anchor: 'middle',
  });

  return (
    '<defs>' +
    `<linearGradient id="${id}g" x1="0" y1="0" x2="1" y2="1">` +
    '<stop offset="0" stop-color="#bfe3df"/><stop offset="1" stop-color="#d9dfe3"/></linearGradient>' +
    `<pattern id="${id}t" width="${tile}" height="${tile}" patternUnits="userSpaceOnUse" x="${x}" y="${y}">` +
    `<path d="M${tile} 0V${tile}H0" fill="none" stroke="#ffffff" stroke-opacity="0.38" stroke-width="1.5"/></pattern>` +
    '</defs>' +
    `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="url(#${id}g)"/>` +
    `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="url(#${id}t)"/>` +
    `<circle cx="${x + w * 0.9}" cy="${y + h * 0.08}" r="${h * 0.42}" fill="#ffffff" opacity="0.22"/>` +
    `<circle cx="${x + w * 0.08}" cy="${y + h * 0.98}" r="${h * 0.36}" fill="#ffffff" opacity="0.18"/>` +
    restroomSign(x + (w - signW) / 2, top, scale) +
    labels
  );
}

// ------------------------------------------------------------------- the cards

function createCards({ currency, timezone, paymentIconsDir } = {}) {
  const money = (amount) => (Number.isFinite(amount) ? formatMoney(amount, currency) : '');
  const time = (date) => (date instanceof Date && !Number.isNaN(date.getTime()) ? formatTime(date, timezone) : '');
  const distance = (r) => (Number.isFinite(r.distanceKm) ? describeDistance(r.distanceKm) : '');

  // A rounded thumbnail: the photo if there is one, else the placeholder.
  async function thumbnail(restroom, index, x, y) {
    const clipId = `thumb${index}`;
    const photo = await loadPhoto(restroom.imagePath, THUMB_W, THUMB_H);
    const inner = photo
      ? `<image href="${dataUri(photo, 'image/jpeg')}" x="${x}" y="${y}" width="${THUMB_W}" height="${THUMB_H}" ` +
        'preserveAspectRatio="none"/>'
      : placeholder({ x, y, w: THUMB_W, h: THUMB_H });
    return (
      `<clipPath id="${clipId}"><rect x="${x}" y="${y}" width="${THUMB_W}" height="${THUMB_H}" rx="14"/></clipPath>` +
      `<g clip-path="url(#${clipId})">${inner}</g>` +
      `<rect x="${x + 0.5}" y="${y + 0.5}" width="${THUMB_W - 1}" height="${THUMB_H - 1}" rx="14" fill="none" ` +
      'stroke="#000000" stroke-opacity="0.06"/>'
    );
  }

  // "Area · KES 100", cutting the area (not the price) when the line is too long.
  function areaAndPrice(r, maxWidth, size) {
    const price = money(r.price);
    const area = clean(r.area);
    if (!area) return price;
    if (!price) return fitText(area, maxWidth, size);
    const room = maxWidth - textWidth(` · ${price}`, size);
    return `${fitText(area, room, size)} · ${price}`;
  }

  // "Please select from the following recommendations": one row per restroom.
  async function restroomList(options) {
    const list = options || [];
    const rowH = 156;
    const headH = 112;
    const thumbX = LEFT + 60;
    const textX = thumbX + THUMB_W + 24;
    const textW = RIGHT - textX;
    const height = PAD + headH + rowH * list.length + PAD + (list.length ? 6 : 0);

    let body =
      textEl(LEFT, PAD + 58, 'Please select from the following recommendations', {
        size: 24,
        weight: 700,
        maxWidth: RIGHT - LEFT,
      }) +
      textEl(LEFT, PAD + 90, 'Based on the distance from your location', { size: 19, fill: COLOR.muted });

    for (let i = 0; i < list.length; i++) {
      const r = list[i];
      const top = PAD + headH + i * rowH;
      const thumbY = top + (rowH - THUMB_H) / 2;
      const lines = [
        { value: r.name, size: 25, weight: 700 },
        { value: distance(r), size: 21 },
        { value: areaAndPrice(r, textW, 20), size: 20, fill: COLOR.muted },
      ].filter((line) => clean(line.value));

      body += separator(top);
      body += textEl(LEFT, thumbY + THUMB_H / 2 + 14, `${i + 1}.`, { size: 40, weight: 700, fill: COLOR.green });
      body += await thumbnail(r, i, thumbX, thumbY);
      // Three lines sit 34 px apart, centred on the thumbnail.
      let y = thumbY + THUMB_H / 2 + 8 - ((lines.length - 1) * 34) / 2;
      for (const line of lines) {
        body += textEl(textX, y, line.value, { ...line, maxWidth: textW });
        y += 34;
      }
    }
    return pngImage(await render(cardDoc(height, body)), 'restrooms.png');
  }

  // The photo shown while the bot says "Searching for the shortest route":
  // the restroom photo with its name on a dark fade, or the illustration.
  async function restroomPhoto(restroom) {
    const filename = `restroom-${slug(restroom.id)}.png`;
    const photo = await loadPhoto(restroom.imagePath, PHOTO_W, PHOTO_H);
    if (!photo) {
      const svg = svgDoc(PHOTO_W, PHOTO_H, placeholder({ x: 0, y: 0, w: PHOTO_W, h: PHOTO_H, name: restroom.name }));
      return pngImage(await render(svg), filename);
    }

    const sub = [distance(restroom), clean(restroom.area)].filter(Boolean).join(' · ');
    const fadeH = sub ? 170 : 130;
    const overlay = svgDoc(
      PHOTO_W,
      PHOTO_H,
      '<defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0" stop-color="#000000" stop-opacity="0"/>' +
        '<stop offset="1" stop-color="#000000" stop-opacity="0.66"/></linearGradient></defs>' +
        `<rect y="${PHOTO_H - fadeH}" width="${PHOTO_W}" height="${fadeH}" fill="url(#fade)"/>` +
        textEl(32, PHOTO_H - (sub ? 66 : 32), restroom.name, {
          size: 34,
          weight: 700,
          fill: '#ffffff',
          maxWidth: PHOTO_W - 64,
        }) +
        (sub ? textEl(32, PHOTO_H - 30, sub, { size: 21, fill: '#e9edef', maxWidth: PHOTO_W - 64 }) : ''),
    );
    const buffer = await sharp(photo)
      .composite([{ input: Buffer.from(overlay) }])
      .png()
      .toBuffer();
    return pngImage(buffer, filename);
  }

  // A custom logo dropped into paymentIconsDir as <id>.png, as a data: URI.
  async function customIcon(id) {
    if (!paymentIconsDir || !/^[A-Za-z0-9_-]+$/.test(String(id))) return null;
    const file = path.join(paymentIconsDir, `${id}.png`);
    if (!fs.existsSync(file)) return null;
    try {
      const source = await fs.promises.readFile(file);
      const png = await sharp(source)
        .resize(ICON_SIZE * 2, ICON_SIZE * 2, { fit: 'contain', background: '#ffffff' })
        .flatten({ background: '#ffffff' })
        .png()
        .toBuffer();
      return dataUri(png, 'image/png');
    } catch (err) {
      console.warn(`[cards] could not use payment icon ${file}: ${err.message}`);
      return null;
    }
  }

  // Generic badge, drawn in a 64 x 64 box. Not anyone's logo artwork.
  function badge(method) {
    const c = ICON_SIZE / 2;
    const white = '#ffffff';
    switch (method.id) {
      case 'mpesa':
        return (
          // Neutral colours and plain lettering: no one else's brand colours or wordmark.
          '<rect width="64" height="64" fill="#1C7C7D"/>' +
          `<text x="${c}" y="31" font-size="13" font-weight="700" fill="${white}" text-anchor="middle">Mobile</text>` +
          `<text x="${c}" y="47" font-size="13" font-weight="700" fill="${white}" text-anchor="middle">money</text>`
        );
      case 'airtel':
        return (
          '<rect width="64" height="64" fill="#3E5A69"/>' +
          `<text x="${c}" y="31" font-size="13" font-weight="700" fill="${white}" text-anchor="middle">Mobile</text>` +
          `<text x="${c}" y="47" font-size="13" font-weight="700" fill="${white}" text-anchor="middle">money</text>`
        );
      case 'card':
        return (
          '<rect width="64" height="64" fill="#223a66"/>' +
          `<rect x="11" y="18" width="42" height="29" rx="5" fill="${white}"/>` +
          '<rect x="11" y="24" width="42" height="7" fill="#223a66"/>' +
          '<rect x="16" y="37" width="11" height="5" rx="2" fill="#9aa8c4"/>'
        );
      case 'paypal':
        return (
          '<rect width="64" height="64" fill="#003087"/>' +
          `<text x="${c}" y="38" font-size="15" font-weight="700" fill="${white}" text-anchor="middle">PayPal</text>`
        );
      default: {
        const initial = Array.from(clean(method.label))[0] || '?';
        return (
          `<rect width="64" height="64" fill="${COLOR.muted}"/>` +
          `<text x="${c}" y="42" font-size="30" font-weight="700" fill="${white}" text-anchor="middle">` +
          `${escapeXml(initial.toUpperCase())}</text>`
        );
      }
    }
  }

  // Payment method icon at (x, y), with rounded corners.
  async function methodIcon(method, index, x, y) {
    const clipId = `icon${index}`;
    const uri = await customIcon(method.id);
    const inner = uri
      ? `<image href="${uri}" width="${ICON_SIZE}" height="${ICON_SIZE}" preserveAspectRatio="none"/>`
      : badge(method);
    return (
      `<clipPath id="${clipId}"><rect width="${ICON_SIZE}" height="${ICON_SIZE}" rx="14"/></clipPath>` +
      `<g transform="translate(${x} ${y})"><g clip-path="url(#${clipId})">${inner}</g>` +
      `<rect x="0.5" y="0.5" width="${ICON_SIZE - 1}" height="${ICON_SIZE - 1}" rx="14" fill="none" ` +
      'stroke="#000000" stroke-opacity="0.08"/></g>'
    );
  }

  // "Select the payment method you would prefer to use", one row per method.
  // The number on the right is what the user replies with.
  async function paymentMethods(methods) {
    const list = methods || [];
    const rowH = 96;
    const headH = 84;
    const textX = LEFT + ICON_SIZE + 24;
    const numR = 18;
    const textW = RIGHT - 2 * numR - 20 - textX;
    const height = PAD + headH + rowH * list.length + PAD + (list.length ? 4 : 0);

    let body = textEl(LEFT, PAD + 58, 'Select the payment method you would prefer to use', {
      size: 24,
      weight: 700,
      maxWidth: RIGHT - LEFT,
    });

    for (let i = 0; i < list.length; i++) {
      const m = list[i];
      const top = PAD + headH + i * rowH;
      const mid = top + rowH / 2;
      const fee = Number(m.feePercent) > 0 ? `${m.feePercent}% fee` : 'No fees';
      body += separator(top);
      body += await methodIcon(m, i, LEFT, mid - ICON_SIZE / 2);
      body += textEl(textX, mid - 4, m.label, { size: 26, weight: 700, maxWidth: textW });
      body += textEl(textX, mid + 26, `Instant · ${fee}`, { size: 20, fill: COLOR.muted, maxWidth: textW });
      body +=
        `<circle cx="${RIGHT - numR}" cy="${mid}" r="${numR}" fill="${COLOR.bg}"/>` +
        textEl(RIGHT - numR, mid + 7, String(i + 1), { size: 19, weight: 700, fill: COLOR.muted, anchor: 'middle' });
    }
    return pngImage(await render(cardDoc(height, body)), 'payment-methods.png');
  }

  // "123456" -> "123 456"
  function spacedOtp(otp) {
    const digits = clean(otp).replace(/\s+/g, '');
    if (digits.length === 6) return `${digits.slice(0, 3)} ${digits.slice(3)}`;
    if (digits.length === 8) return `${digits.slice(0, 4)} ${digits.slice(4)}`;
    return digits;
  }

  // The QR code without its own white margin, scaled to QR_SIZE, as a data:
  // URI. Null if the buffer is not an image.
  async function qrUri(qrBuffer) {
    if (!qrBuffer) return null;
    try {
      let source = qrBuffer;
      try {
        source = await sharp(qrBuffer).trim({ background: '#ffffff', threshold: 60 }).png().toBuffer();
      } catch {
        // nothing to trim (e.g. a plain white image): use it as it is
      }
      const { width } = await sharp(source).metadata();
      const png = await sharp(source)
        .flatten({ background: '#ffffff' })
        .resize(QR_SIZE, QR_SIZE, { fit: 'fill', kernel: width < QR_SIZE ? 'nearest' : 'lanczos3' })
        .png()
        .toBuffer();
      return dataUri(png, 'image/png');
    } catch (err) {
      console.warn(`[cards] could not use QR code: ${err.message}`);
      return null;
    }
  }

  // "Your access details": amount, facility, expiry, OTP and QR code.
  async function accessCard(pass, qrBuffer) {
    const qr = await qrUri(qrBuffer);
    const cx = WIDTH / 2;
    const facility = (pass.facility && pass.facility.name) || '';

    // Amount on the left, expiry on the right, the facility gets the room between.
    const amount = money(pass.total);
    const expiry = time(pass.expiresAt);
    const colW = (label, value, min, max) =>
      Math.ceil(Math.min(max, Math.max(min, textWidth(label, 18), textWidth(value, 26, true))));
    const amountW = colW('Amount paid', amount, 150, 220);
    const expiryW = colW('Session expiry', expiry, 0, 160);
    const facilityX = LEFT + amountW + 44;
    const cols = [
      { x: LEFT, w: amountW, label: 'Amount paid', value: amount },
      { x: facilityX, w: RIGHT - expiryW - 36 - facilityX, label: 'Facility', value: facility },
      { x: RIGHT - expiryW, w: expiryW, label: 'Session expiry', value: expiry },
    ];

    const titleY = PAD + 64;
    const colsY = PAD + 128;
    const otpTop = PAD + 210;
    const otpH = 100;
    const qrPad = 20;
    const qrBox = QR_SIZE + 2 * qrPad;
    const qrTop = otpTop + otpH + 28;
    const footerY = (qr ? qrTop + qrBox : otpTop + otpH) + 50;
    const height = footerY + 34 + PAD;

    let body =
      // green check badge + title
      `<circle cx="${LEFT + 22}" cy="${titleY - 10}" r="22" fill="${COLOR.green}"/>` +
      `<path d="M${LEFT + 12} ${titleY - 9}l7.5 8 13-15" fill="none" stroke="#ffffff" stroke-width="4.5" ` +
      'stroke-linecap="round" stroke-linejoin="round"/>' +
      textEl(LEFT + 58, titleY, 'Your access details', { size: 32, weight: 700 }) +
      separator(PAD + 94);

    for (const col of cols) {
      body += textEl(col.x, colsY, col.label, { size: 18, fill: COLOR.muted });
      body += textEl(col.x, colsY + 38, col.value || '-', { size: 26, weight: 700, fill: COLOR.blue, maxWidth: col.w });
    }

    // Access code in a light green pill
    const pillW = 560;
    body +=
      `<rect x="${cx - pillW / 2}" y="${otpTop}" width="${pillW}" height="${otpH}" rx="22" fill="#eaf8f0"/>` +
      `<text x="${cx}" y="${otpTop + 70}" text-anchor="middle">` +
      `<tspan font-size="26" font-weight="700" fill="${COLOR.green}">OTP</tspan>` +
      `<tspan dx="20" font-size="58" font-weight="700" fill="${COLOR.text}" letter-spacing="5">` +
      `${escapeXml(spacedOtp(pass.otp))}</tspan></text>`;

    if (qr) {
      body +=
        `<rect x="${cx - qrBox / 2}" y="${qrTop}" width="${qrBox}" height="${qrBox}" rx="20" fill="#ffffff" ` +
        `stroke="${COLOR.line}" stroke-width="2"/>` +
        `<image href="${qr}" x="${cx - QR_SIZE / 2}" y="${qrTop + qrPad}" width="${QR_SIZE}" height="${QR_SIZE}"/>`;
    }

    body += textEl(cx, footerY, `Show this at the entrance · Ref ${clean(pass.reference)}`, {
      size: 20,
      fill: COLOR.muted,
      anchor: 'middle',
      maxWidth: RIGHT - LEFT,
    });

    return pngImage(await render(cardDoc(height, body)), `access-${slug(pass.reference)}.png`);
  }

  // Small pictures for tappable options (web tester, WhatsApp lists later),
  // drawn at twice their size so they stay sharp on phones.
  async function restroomThumb(restroom) {
    const svg = svgDoc(THUMB_W, THUMB_H, await thumbnail(restroom, 0, 0, 0));
    const buffer = await sharp(Buffer.from(svg), { density: 144 }).png().toBuffer();
    return pngImage(buffer, `thumb-${slug(restroom.id)}.png`);
  }

  async function paymentIcon(method) {
    const svg = svgDoc(ICON_SIZE, ICON_SIZE, await methodIcon(method, 0, 0, 0));
    const buffer = await sharp(Buffer.from(svg), { density: 144 }).png().toBuffer();
    return pngImage(buffer, `icon-${slug(method.id)}.png`);
  }

  return { restroomList, restroomPhoto, paymentMethods, accessCard, restroomThumb, paymentIcon };
}

module.exports = { createCards, escapeXml, fitText, FALLBACK_ROOM };
