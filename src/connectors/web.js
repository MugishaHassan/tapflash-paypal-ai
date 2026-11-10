'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');

// Test the bot in a browser with a WhatsApp-style chat page. No phone needed.
// Each browser tab is its own "user". Menus show as tappable choices, and
// pictures (route maps, QR codes, access cards) show inline.

const PAGE = path.join(__dirname, '..', '..', 'web', 'index.html');
const MAX_BODY_BYTES = 64 * 1024;

// The page is one inline file. It may load Barlow from Google Fonts, show
// data: images and talk to this server, nothing else.
const SECURITY_HEADERS = {
  'Content-Security-Policy': [
    "default-src 'none'",
    "script-src 'unsafe-inline'",
    "style-src 'unsafe-inline' https://fonts.googleapis.com",
    'font-src https://fonts.gstatic.com',
    'img-src data: blob:',
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join('; '),
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Cross-Origin-Opener-Policy': 'same-origin',
};

const MIME_BY_EXT = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
};

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// The browser went away before it finished sending (the tab was closed).
// Nothing went wrong here, and nobody is left to answer.
class ClientGone extends Error {}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const type = String(req.headers['content-type'] || '');
    // Only JSON, so a web page on another site can't post here with a plain form.
    if (!/^application\/json\b/i.test(type)) {
      req.resume();
      reject(new HttpError(415, 'Send JSON (Content-Type: application/json)'));
      return;
    }
    const chunks = [];
    let size = 0;
    let tooLarge = false;
    req.on('data', (chunk) => {
      if (tooLarge) return;
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        tooLarge = true;
        reject(new HttpError(413, 'Message too large'));
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (tooLarge) return;
      let body;
      try {
        body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
      } catch {
        reject(new HttpError(400, 'Invalid JSON'));
        return;
      }
      // A message is an object: null, arrays, strings and numbers are not.
      if (!body || typeof body !== 'object' || Array.isArray(body)) {
        reject(new HttpError(400, 'Send a JSON object'));
        return;
      }
      resolve(body);
    });
    req.on('error', (err) => reject(err.code === 'ECONNRESET' ? new ClientGone(err.message) : err));
  });
}

// A coordinate from the page: a number, or a string holding one. Number()
// would turn null, '', true and [1] into 0 or 1, a real place on the map.
function coordinate(value) {
  if (typeof value === 'number') return value;
  if (typeof value === 'string' && value.trim() !== '') return Number(value);
  return NaN;
}

function sendJson(res, status, data) {
  res.writeHead(status, {
    ...SECURITY_HEADERS,
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(JSON.stringify(data));
}

// Width and height of a PNG or JPEG, so the page can keep space for a picture
// before it has loaded. Null for anything else.
function imageSize(buffer) {
  if (!buffer || buffer.length < 24) return null;
  if (buffer.readUInt32BE(0) === 0x89504e47 && buffer.toString('latin1', 12, 16) === 'IHDR') {
    return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
  }
  if (buffer[0] === 0xff && buffer[1] === 0xd8) {
    let i = 2;
    while (i + 9 < buffer.length) {
      if (buffer[i] !== 0xff) return null;
      const marker = buffer[i + 1];
      const length = buffer.readUInt16BE(i + 2);
      // SOF0-SOF15 hold the size; C4 (DHT), C8 (JPG) and CC (DAC) are not frames.
      if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
        return { width: buffer.readUInt16BE(i + 7), height: buffer.readUInt16BE(i + 5) };
      }
      i += 2 + length;
    }
  }
  return null;
}

// A short description for screen readers, from the file name the engine
// gave the picture: "route-map.png" -> "Route map". Reference codes are dropped.
function altFor(image) {
  const name = path.parse(String(image.filename || image.path || '')).name
    .replace(/[-_]+/g, ' ')
    .replace(/\b(?=[A-Z0-9]*\d)[A-Z0-9]{6,}\b/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return name ? name[0].toUpperCase() + name.slice(1) : '';
}

function mimeFor(image, file) {
  if (/^image\/[a-z0-9.+-]+$/i.test(String(image.mimetype || ''))) return image.mimetype;
  return MIME_BY_EXT[path.extname(String(file || '')).toLowerCase()] || 'image/png';
}

// An image reply ({ buffer | path, mimetype }) as { src: data URL, width, height }.
// Null when there is no picture to show.
function toPicture(image) {
  if (!image || (!image.buffer && !image.path)) return null;
  try {
    const buffer = image.buffer ? Buffer.from(image.buffer) : fs.readFileSync(image.path);
    const size = imageSize(buffer);
    return {
      src: `data:${mimeFor(image, image.path || image.filename)};base64,${buffer.toString('base64')}`,
      width: size ? size.width : undefined,
      height: size ? size.height : undefined,
      alt: altFor(image),
    };
  } catch (err) {
    console.warn(`[web] could not read image ${image.path || image.filename || ''}: ${err.message}`);
    return null;
  }
}

function toOption(option) {
  const icon = toPicture(option.icon);
  return {
    id: String(option.id),
    title: String(option.title ?? option.id),
    description: option.description ? String(option.description) : '',
    icon: icon ? icon.src : undefined,
    iconWidth: icon ? icon.width : undefined,
    iconHeight: icon ? icon.height : undefined,
    secondary: Boolean(option.secondary),
  };
}

// A reply from the engine in the shape the page draws. Menus keep their
// choices tappable, so optionsImage (a picture of the choices, for platforms
// that can't show them) is left out.
function toPageReply(reply) {
  if (!reply) return null;
  if (reply.kind === 'text') return { kind: 'text', text: String(reply.text ?? '') };

  if (reply.kind === 'menu') {
    const options = Array.isArray(reply.options) ? reply.options.map(toOption) : [];
    const image = toPicture(reply.image);
    return {
      kind: 'menu',
      text: String(reply.text ?? ''),
      image: image ? image.src : undefined,
      imageWidth: image ? image.width : undefined,
      imageHeight: image ? image.height : undefined,
      imageAlt: image ? image.alt : undefined,
      options,
      input: reply.input ? { placeholder: String(reply.input.placeholder ?? '') } : undefined,
    };
  }

  if (reply.kind === 'image' || reply.buffer || reply.path) {
    const picture = toPicture(reply);
    const caption = String(reply.caption ?? '');
    if (!picture) return caption ? { kind: 'text', text: caption } : null;
    return { kind: 'image', src: picture.src, width: picture.width, height: picture.height, alt: picture.alt, caption };
  }

  return reply.text ? { kind: 'text', text: String(reply.text) } : null;
}

function createWebConnector(config) {
  // Replies collected while one browser message is being handled. Messages
  // from the same chat are handled one at a time, so each request gets
  // exactly the replies to its own message.
  const outboxes = new Map();
  const chatQueues = new Map();
  let server;
  let onMessageHandler = async () => {};

  function inTurn(chatId, task) {
    const previous = chatQueues.get(chatId) || Promise.resolve();
    const current = previous.then(task);
    const tail = current.catch(() => {});
    chatQueues.set(chatId, tail);
    tail.then(() => {
      if (chatQueues.get(chatId) === tail) chatQueues.delete(chatId);
    });
    return current;
  }

  function toIncoming(body) {
    const userId = `web:${String(body.userId || 'anon').slice(0, 64)}`;
    const base = {
      userId,
      chatId: userId,
      phone: body.phone ? String(body.phone).slice(0, 20) : undefined,
      receivedAt: Date.now(),
    };
    if (body.type === 'location') {
      return { ...base, type: 'location', location: { lat: coordinate(body.lat), lng: coordinate(body.lng) } };
    }
    return { ...base, type: 'text', text: String(body.text ?? '').slice(0, 4096) };
  }

  async function handle(req, res) {
    const url = (req.url || '/').split('?')[0];
    if (req.method === 'GET' && (url === '/' || url === '/index.html')) {
      res.writeHead(200, {
        ...SECURITY_HEADERS,
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-cache',
      });
      res.end(fs.readFileSync(PAGE));
      return;
    }
    if (url === '/api/message') {
      if (req.method !== 'POST') throw new HttpError(405, 'Use POST');
      const incoming = toIncoming(await readJson(req));
      const replies = await inTurn(incoming.chatId, async () => {
        const outbox = [];
        outboxes.set(incoming.chatId, outbox);
        try {
          await onMessageHandler(incoming);
        } finally {
          outboxes.delete(incoming.chatId);
        }
        return outbox;
      });
      sendJson(res, 200, { replies });
      return;
    }
    throw new HttpError(404, 'Not found');
  }

  async function start(onMessage) {
    onMessageHandler = onMessage;
    server = http.createServer((req, res) => {
      handle(req, res).catch((err) => {
        if (err instanceof ClientGone) return;
        const status = err instanceof HttpError ? err.status : 500;
        if (status === 500) console.error('[web] error while handling a message:', err);
        if (res.headersSent) return res.end();
        // Any unread body is drained and dropped by Node after this.
        sendJson(res, status, { error: err.message });
      });
    });

    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(config.webPort, '127.0.0.1', () => {
        server.off('error', reject);
        resolve();
      });
    });
    const { port } = server.address();
    console.log(`\n${config.botName} web tester: http://localhost:${port}`);
    console.log('   Open it in your browser. Press Ctrl+C here to stop.\n');
  }

  async function send(chatId, reply) {
    const outbox = outboxes.get(chatId);
    if (!outbox) return;
    const pageReply = toPageReply(reply);
    if (pageReply) outbox.push(pageReply);
  }

  async function stop() {
    if (server) await new Promise((resolve) => server.close(resolve));
  }

  // The port actually in use (useful with webPort 0).
  function address() {
    return server ? server.address() : null;
  }

  // supportsMenus: the page shows menus as tappable choices, so the app sends
  // them as they are instead of flattening them to pictures and numbered text.
  return { name: 'web', supportsMenus: true, start, send, stop, address };
}

module.exports = { createWebConnector };
