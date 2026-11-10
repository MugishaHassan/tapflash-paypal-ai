'use strict';

const { withCaption } = require('./replies');

// Bridge: the free WhatsApp Web connection runs on this PC, but the
// conversation runs on a remote TapFlash server (for example the Django bot on
// shared hosting, which can't run Chrome). Each message is posted to the
// server's test chat endpoint, POST <url>/api/message, and the replies come
// back in the engine's shape, so the rest of the app works as with createBot().
//
// The server answers with { replies: [...] }, where a reply is
//   { kind: 'text', text }
//   { kind: 'image', src: 'data:image/png;base64,...', caption }
//   { kind: 'menu', text, image?, options: [{ id, title, description, secondary }], input? }

const DOWN_TEXT = "The TapFlash server isn't answering right now. Please try again in a minute.";
const DEFAULT_TIMEOUT_MS = 30_000;
// One picture, and the whole answer (it can hold several pictures).
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 25 * 1024 * 1024;
// A broken server must not make the bot flood a chat (WhatsApp bans spam).
const MAX_REPLIES = 10;

const IMAGE_TYPES = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp' };

// Something in the server's answer we can't use. The user gets DOWN_TEXT.
class BadAnswer extends Error {}

// A data URL from the server as an engine image. Only real pictures, and not
// too big, since they are sent on to WhatsApp as they are.
function decodeImage(src, alt) {
  const match = /^data:([a-z0-9.+/-]+);base64,(.*)$/is.exec(typeof src === 'string' ? src : '');
  if (!match) throw new BadAnswer('a picture is not a data URL');
  const mimetype = match[1].toLowerCase();
  const ext = IMAGE_TYPES[mimetype];
  if (!ext) throw new BadAnswer(`a picture has type ${mimetype.slice(0, 40)}, not png, jpeg, gif or webp`);
  const base64 = match[2].replace(/\s+/g, '');
  if (base64.length > (MAX_IMAGE_BYTES * 4) / 3 + 4) throw new BadAnswer('a picture is too big');
  if (!/^[a-z0-9+/]+={0,2}$/i.test(base64)) throw new BadAnswer('a picture is not valid base64');
  const buffer = Buffer.from(base64, 'base64');
  if (buffer.length === 0 || buffer.length > MAX_IMAGE_BYTES) throw new BadAnswer('a picture is empty or too big');
  // A plain file name from the alt text ("Route map" -> route-map.png). The
  // console simulator saves pictures under this name.
  const name = String(alt || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
  return { kind: 'image', buffer, mimetype, filename: `${name || 'picture'}.${ext}` };
}

// The web endpoint leaves out the engine's text hints, so a word choice such
// as "exit" is written the way the local bot writes it.
function hintFor(id, title) {
  if (/^\d+$/.test(id)) return undefined;
  return id === 'exit' ? 'Reply *exit* to leave.' : `Reply *${id}* for ${title}.`;
}

function toOption(option) {
  if (!option || typeof option !== 'object' || option.id === undefined || option.id === null) {
    throw new BadAnswer('a menu choice has no id');
  }
  const id = String(option.id);
  const title = String(option.title ?? id);
  const out = { id, title };
  if (option.description) out.description = String(option.description);
  if (option.secondary) out.secondary = true;
  const textHint = hintFor(id, title);
  if (textHint) out.textHint = textHint;
  return out;
}

// One reply from the server as engine replies (none for a kind we don't know).
function toReplies(reply) {
  if (!reply || typeof reply !== 'object') throw new BadAnswer('a reply is not an object');
  if (reply.kind === 'text') {
    const text = String(reply.text ?? '');
    return text ? [{ kind: 'text', text }] : [];
  }
  if (reply.kind === 'image') {
    const image = decodeImage(reply.src, reply.alt);
    const caption = String(reply.caption ?? '');
    return caption ? withCaption(image, caption) : [image];
  }
  if (reply.kind === 'menu') {
    if (reply.options !== undefined && !Array.isArray(reply.options)) throw new BadAnswer('menu choices are not a list');
    const menu = { kind: 'menu', text: String(reply.text ?? ''), options: (reply.options || []).map(toOption) };
    if (reply.image) menu.image = decodeImage(reply.image, reply.imageAlt);
    if (reply.input && typeof reply.input === 'object') menu.input = { placeholder: String(reply.input.placeholder ?? '') };
    return [menu];
  }
  console.warn(`[bridge] skipped a reply of unknown kind "${String(reply.kind).slice(0, 40)}"`);
  return [];
}

// The answer's text, stopping early if it is far too big.
async function readBody(res) {
  if (Number(res.headers.get('content-length')) > MAX_RESPONSE_BYTES) throw new BadAnswer('the answer is too big');
  if (!res.body) return res.text();
  const chunks = [];
  let size = 0;
  for await (const chunk of res.body) {
    size += chunk.length;
    if (size > MAX_RESPONSE_BYTES) throw new BadAnswer('the answer is too big');
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString('utf8');
}

// What we post for an incoming message, in the web test chat's shape.
function toRequest(incoming) {
  const base = { userId: incoming.userId, phone: incoming.phone };
  if (incoming.type === 'location') {
    return { ...base, type: 'location', lat: incoming.location?.lat, lng: incoming.location?.lng };
  }
  if (incoming.type === 'image' && incoming.image) {
    return { ...base, type: 'image', image: incoming.image, mimetype: incoming.mimetype };
  }
  // A sticker or voice note goes as an empty text, which the bot answers
  // with "please send text".
  return { ...base, type: 'text', text: incoming.type === 'text' ? String(incoming.text ?? '') : '' };
}

function createRemoteBot({ url, token, fetch = globalThis.fetch, timeoutMs = DEFAULT_TIMEOUT_MS }) {
  if (!url) throw new Error('REMOTE_BOT_URL is missing (example: https://bot.example.com)');
  const endpoint = `${String(url).replace(/\/+$/, '')}/api/message`;
  const headers = { 'Content-Type': 'application/json', Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;

  async function ask(incoming) {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify(toRequest(incoming)),
      signal: AbortSignal.timeout(timeoutMs),
      redirect: 'error',
    });
    if (res.status !== 200) {
      const hint = res.status === 401 || res.status === 403 ? ' (check REMOTE_BOT_TOKEN)' : '';
      throw new BadAnswer(`the server answered with status ${res.status}${hint}`);
    }
    const raw = await readBody(res);
    let body;
    try {
      body = JSON.parse(raw);
    } catch {
      throw new BadAnswer('the answer is not valid JSON');
    }
    if (!body || typeof body !== 'object' || !Array.isArray(body.replies)) {
      throw new BadAnswer('the answer has no list of replies');
    }
    const replies = body.replies.flatMap(toReplies);
    if (replies.length > MAX_REPLIES) {
      console.warn(`[bridge] the server sent ${replies.length} replies, only the first ${MAX_REPLIES} are sent`);
    }
    return replies.slice(0, MAX_REPLIES);
  }

  // Same contract as the local engine's handle(): replies, never a crash.
  async function handle(incoming) {
    try {
      return await ask(incoming);
    } catch (err) {
      const reason =
        err instanceof BadAnswer
          ? err.message
          : err.name === 'TimeoutError'
            ? `no answer within ${timeoutMs} ms`
            : /redirect/i.test(err.message)
              ? 'the server redirected the request (check REMOTE_BOT_URL and the /api/message path)'
              : `could not reach the server: ${err.cause?.code || err.cause?.message || err.message}`;
      console.error(`[bridge] ${endpoint}: ${reason}`);
      return [{ kind: 'text', text: DOWN_TEXT }];
    }
  }

  // Replies the server sent on its own, such as the receipt once PayPal
  // confirms a payment: [{ userId, replies }]. Each is given out only once.
  const outboxUrl = `${String(url).replace(/\/+$/, '')}/api/outbox`;
  async function collect() {
    const res = await fetch(outboxUrl, {
      headers: { Accept: 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      signal: AbortSignal.timeout(timeoutMs),
      redirect: 'error',
    });
    if (res.status !== 200) throw new BadAnswer(`the outbox answered with status ${res.status}`);
    let body;
    try {
      body = JSON.parse(await readBody(res));
    } catch {
      throw new BadAnswer('the outbox answer is not valid JSON');
    }
    if (!body || !Array.isArray(body.messages)) throw new BadAnswer('the outbox answer has no list of messages');
    return body.messages
      .filter((message) => message && typeof message.userId === 'string' && Array.isArray(message.replies))
      .map((message) => ({ userId: message.userId, replies: message.replies.flatMap(toReplies).slice(0, MAX_REPLIES) }));
  }

  return { handle, collect };
}

module.exports = { createRemoteBot, DOWN_TEXT, MAX_IMAGE_BYTES };
