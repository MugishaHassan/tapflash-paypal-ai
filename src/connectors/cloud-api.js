'use strict';

const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const { menuText } = require('../bot/replies');

// Official connector: Meta's WhatsApp Cloud API. Meta sends each customer
// message to our webhook (an HTTPS address on your domain); we answer through
// Meta's Graph API. No Chrome, so it runs on shared hosting (cPanel Node.js).
//
// Menus become real WhatsApp messages: up to 3 short choices are reply
// buttons, more become a tap list.

const MAX_BODY_BYTES = 1024 * 1024;
// Meta retries a webhook it thinks failed; skip anything older than this.
const MAX_MESSAGE_AGE_SECONDS = 5 * 60;

// WhatsApp's limits for interactive messages.
const LIMIT = { buttons: 3, buttonTitle: 20, rows: 10, rowTitle: 24, rowDescription: 72, buttonBody: 1024, listBody: 4096, caption: 1024 };

function cut(text, max) {
  const s = String(text);
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}

// Is the X-Hub-Signature-256 header Meta's signature of this exact body?
function validSignature(rawBody, header, appSecret) {
  if (!appSecret) return true;
  const expected = `sha256=${crypto.createHmac('sha256', appSecret).update(rawBody).digest('hex')}`;
  const given = String(header || '');
  return given.length === expected.length && crypto.timingSafeEqual(Buffer.from(given), Buffer.from(expected));
}

// A Meta message as the engine's incoming message (or null to ignore it).
function toIncoming(message) {
  const from = String(message.from || '');
  if (!/^\d{6,15}$/.test(from)) return null;
  const base = { userId: from, chatId: from, phone: `+${from}` };
  switch (message.type) {
    case 'text':
      return { ...base, type: 'text', text: message.text?.body || '' };
    case 'interactive': {
      // A tapped button or list row sends back the option id we gave it.
      const reply = message.interactive?.button_reply || message.interactive?.list_reply;
      return { ...base, type: 'text', text: reply?.id || '' };
    }
    case 'button':
      return { ...base, type: 'text', text: message.button?.payload || message.button?.text || '' };
    case 'location':
      return {
        ...base,
        type: 'location',
        location: { lat: Number(message.location?.latitude), lng: Number(message.location?.longitude) },
      };
    default:
      return { ...base, type: 'other' };
  }
}

// How to show a menu: reply buttons, a list, or (too many choices) text.
function interactiveFor(menu) {
  const options = menu.options;
  const asButtons =
    options.length <= LIMIT.buttons &&
    options.every((o) => o.title.length <= LIMIT.buttonTitle) &&
    menu.text.length <= LIMIT.buttonBody;
  if (asButtons) {
    return {
      type: 'button',
      body: { text: menu.text },
      action: { buttons: options.map((o) => ({ type: 'reply', reply: { id: o.id, title: o.title } })) },
    };
  }
  if (options.length <= LIMIT.rows && menu.text.length <= LIMIT.listBody) {
    return {
      type: 'list',
      body: { text: menu.text },
      action: {
        button: 'Choose',
        sections: [
          {
            rows: options.map((o) => {
              // A title too long for the row keeps its full wording as the description.
              const long = o.title.length > LIMIT.rowTitle;
              const description = o.description || (long ? o.title : '');
              const row = { id: o.id, title: cut(o.title, LIMIT.rowTitle) };
              if (description) row.description = cut(description, LIMIT.rowDescription);
              return row;
            }),
          },
        ],
      },
    };
  }
  return null;
}

function createCloudApiConnector(config) {
  for (const [key, name] of [
    ['whatsappToken', 'WHATSAPP_TOKEN'],
    ['whatsappPhoneNumberId', 'WHATSAPP_PHONE_NUMBER_ID'],
    ['whatsappVerifyToken', 'WHATSAPP_VERIFY_TOKEN'],
  ]) {
    if (!config[key]) throw new Error(`${name} is missing. Add it to .env (see .env.example).`);
  }
  if (!config.whatsappAppSecret) {
    console.warn('[cloud-api] WHATSAPP_APP_SECRET is not set, so webhook signatures are not checked.');
  }

  const graph = `https://graph.facebook.com/${config.graphApiVersion}/${config.whatsappPhoneNumberId}`;
  const seen = new Set();
  let server;

  async function callGraph(path, init) {
    const res = await fetch(`${graph}${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${config.whatsappToken}`, ...(init.headers || {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`Meta API ${res.status}: ${data.error?.message || res.statusText}`);
    return data;
  }

  const sendMessage = (to, payload) =>
    callGraph('/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messaging_product: 'whatsapp', recipient_type: 'individual', to, ...payload }),
    });

  // Pictures are uploaded to Meta first, then sent by their media id.
  async function upload(image) {
    const buffer = image.buffer || (await fs.promises.readFile(image.path));
    const type = image.mimetype || (/\.jpe?g$/i.test(image.path || '') ? 'image/jpeg' : 'image/png');
    const form = new FormData();
    form.append('messaging_product', 'whatsapp');
    form.append('type', type);
    form.append('file', new Blob([buffer], { type }), image.filename || 'image.png');
    const { id } = await callGraph('/media', { method: 'POST', body: form });
    return id;
  }

  async function sendImage(to, image, caption) {
    const id = await upload(image);
    const media = { id };
    if (caption) media.caption = cut(caption, LIMIT.caption);
    await sendMessage(to, { type: 'image', image: media });
  }

  async function send(chatId, reply) {
    if (reply.kind === 'text') {
      await sendMessage(chatId, { type: 'text', text: { body: reply.text, preview_url: true } });
    } else if (reply.kind === 'image') {
      await sendImage(chatId, reply, reply.caption);
    } else if (reply.kind === 'menu') {
      // Lists can't carry a picture, so a menu's picture goes first on its own.
      if (reply.image) await sendImage(chatId, reply.image);
      const interactive = interactiveFor(reply);
      if (interactive) await sendMessage(chatId, { type: 'interactive', interactive });
      else await sendMessage(chatId, { type: 'text', text: { body: menuText(reply), preview_url: true } });
    } else {
      throw new Error(`Unknown reply kind: ${reply.kind}`);
    }
  }

  // Meta checks the webhook once, when you save it in the app dashboard.
  function verify(url, res) {
    const ok =
      url.searchParams.get('hub.mode') === 'subscribe' &&
      url.searchParams.get('hub.verify_token') === config.whatsappVerifyToken;
    res.writeHead(ok ? 200 : 403, { 'Content-Type': 'text/plain' });
    res.end(ok ? url.searchParams.get('hub.challenge') || '' : 'Forbidden');
  }

  function readBody(req) {
    return new Promise((resolve, reject) => {
      const chunks = [];
      let size = 0;
      req.on('data', (chunk) => {
        size += chunk.length;
        if (size > MAX_BODY_BYTES) {
          reject(new Error('Body too large'));
          req.destroy();
        } else {
          chunks.push(chunk);
        }
      });
      req.on('end', () => resolve(Buffer.concat(chunks)));
      req.on('error', reject);
    });
  }

  // Every message in a webhook delivery that we should answer.
  function messagesIn(payload) {
    const out = [];
    for (const entry of payload?.entry || []) {
      for (const change of entry?.changes || []) {
        for (const message of change?.value?.messages || []) out.push(message);
      }
    }
    return out;
  }

  function isNew(message) {
    if (!message.id || seen.has(message.id)) return false;
    seen.add(message.id);
    if (seen.size > 2000) seen.delete(seen.values().next().value);
    const age = Date.now() / 1000 - Number(message.timestamp || 0);
    return age <= MAX_MESSAGE_AGE_SECONDS;
  }

  async function start(onMessage) {
    server = http.createServer(async (req, res) => {
      const url = new URL(req.url || '/', 'http://localhost');
      try {
        if (url.pathname === config.webhookPath && req.method === 'GET') return verify(url, res);

        if (url.pathname === config.webhookPath && req.method === 'POST') {
          const raw = await readBody(req);
          if (!validSignature(raw, req.headers['x-hub-signature-256'], config.whatsappAppSecret)) {
            res.writeHead(401).end();
            return;
          }
          // Answer Meta at once; replying to the customer happens afterwards.
          res.writeHead(200).end();
          let payload;
          try {
            payload = JSON.parse(raw.toString('utf8'));
          } catch {
            return;
          }
          for (const message of messagesIn(payload)) {
            if (!isNew(message)) continue;
            const incoming = toIncoming(message);
            if (incoming) onMessage(incoming).catch((err) => console.error('[cloud-api] reply failed:', err));
          }
          return;
        }

        if (url.pathname === '/' && req.method === 'GET') {
          res.writeHead(200, { 'Content-Type': 'text/plain' });
          res.end(`${config.botName} is running.`);
          return;
        }
        res.writeHead(404).end();
      } catch (err) {
        console.error('[cloud-api] request failed:', err.message);
        if (!res.headersSent) res.writeHead(400).end();
      }
    });

    // cPanel's Node.js hosting passes the address in PORT (a number or a socket).
    const port = /^\d+$/.test(config.port) ? Number(config.port) : config.port;
    await new Promise((resolve) => server.listen(port, resolve));
    console.log(`[cloud-api] ${config.botName} is listening for Meta on ${config.webhookPath}`);
  }

  async function stop() {
    if (server) await new Promise((resolve) => server.close(resolve));
  }

  // The address in use (for tests, which listen on port 0).
  const address = () => server && server.address();

  return { name: 'cloud-api', supportsMenus: true, start, send, stop, address };
}

module.exports = { createCloudApiConnector, interactiveFor, toIncoming, validSignature };
