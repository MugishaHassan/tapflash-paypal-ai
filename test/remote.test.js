'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('http');
const sharp = require('sharp');
const { buildConfig } = require('../src/config');
const { createBot } = require('../src/bot/engine');
const { createRemoteBot, DOWN_TEXT, MAX_IMAGE_BYTES } = require('../src/bot/remote');
const { createRestroomService } = require('../src/services/restrooms');
const { createMockPayments } = require('../src/payments/mock');
const { createImages } = require('../src/services/images');
const { createWebConnector } = require('../src/connectors/web');
const { createMessageHandler } = require('../src/index');
const { QUICKEST_ANSWER_MS } = require('../src/util/repeats');

const CHAT = '254712345678@c.us';
const PHONE = '+254712345678';
const NAIROBI = { lat: -1.2864, lng: 36.8172 };
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47]);

// The real bot behind the web test chat's server, standing in for the remote
// TapFlash server. Maps are off, so no internet is needed.
async function serveRealBot(t) {
  const config = buildConfig({ REPLY_DELAY_MS: '0', WEB_PORT: '0', MAP_IMAGES: 'false' });
  const saved = { payments: [] };
  const bot = createBot({
    config,
    restrooms: createRestroomService(config),
    payments: createMockPayments(),
    records: { savePayment: async (e) => saved.payments.push(e), saveReport: async () => {} },
    // Midday in Nairobi, so the demo restrooms with opening hours are open.
    now: () => new Date('2026-01-15T09:00:00Z'),
    images: createImages(config),
    makeOtp: () => '123456',
  });
  const connector = createWebConnector(config);
  t.mock.method(console, 'log', () => {});
  await connector.start(createMessageHandler({ bot, connector }));
  t.after(() => connector.stop());
  return { base: `http://127.0.0.1:${connector.address().port}`, saved };
}

// The bridge as index.js wires it: the remote bot behind a connector that,
// like WhatsApp Web, can't show menus. Returns what one message sends.
function bridgeTo(url, options = {}) {
  const bot = createRemoteBot({ url, ...options });
  const sent = [];
  const connector = { name: 'whatsapp-web', send: async (chatId, reply) => sent.push({ chatId, ...reply }) };
  const handler = createMessageHandler({ bot, connector });
  const message = async (incoming) => {
    sent.length = 0;
    await handler({ userId: CHAT, chatId: CHAT, phone: PHONE, ...incoming });
    return sent.slice();
  };
  return {
    bot,
    say: (text) => message({ type: 'text', text }),
    shareLocation: (loc = NAIROBI) => message({ type: 'location', location: loc }),
  };
}

// A plain server answering with handler(req, body, res).
async function serve(t, handler) {
  const seen = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const body = Buffer.concat(chunks).toString('utf8');
      seen.push({ headers: req.headers, url: req.url, method: req.method, body: body ? JSON.parse(body) : null });
      handler(req, res);
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  });
  return { url: `http://127.0.0.1:${server.address().port}`, seen };
}

const answer = (data, status = 200) => (req, res) => {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(typeof data === 'string' ? data : JSON.stringify(data));
};

const png = () => sharp({ create: { width: 40, height: 30, channels: 3, background: '#2a7f86' } }).png().toBuffer();
const dataUrl = (buffer, mime = 'image/png') => `data:${mime};base64,${buffer.toString('base64')}`;

// Everything the user reads, one message per entry.
const readable = (sent) => sent.map((r) => (r.kind === 'text' ? r.text : `[picture ${r.filename}] ${r.caption || ''}`));

test('a whole conversation runs on the remote server, with menus as numbered text', async (t) => {
  const { base, saved } = await serveRealBot(t);
  const wa = bridgeTo(base, { token: 'secret' });
  // The server drops a copy of the last message sent too soon to be a real
  // answer (a double tap), so pause like a person reading the question.
  const choose = async (text) => {
    await new Promise((resolve) => setTimeout(resolve, QUICKEST_ANSWER_MS + 50));
    return wa.say(text);
  };

  const hi = await wa.say('hi');
  assert.ok(hi.every((r) => r.chatId === CHAT && (r.kind === 'text' || r.kind === 'image')), 'only text and pictures reach WhatsApp');
  assert.match(hi.at(-1).text, /\*1\.\* Continue\n\*2\.\* Exit$/);

  const list = await wa.shareLocation();
  assert.equal(list.length, 1);
  assert.equal(list[0].kind, 'text');
  assert.match(list[0].text, /\*1\.\* .+\n_.+_/, 'restroom 1 with its details');
  assert.match(list[0].text, /\*4\.\* /);
  assert.match(list[0].text, /Reply \*exit\* to leave\.$/, 'written like the local bot, not "*exit.* Exit"');

  const pay = await choose('1');
  assert.match(readable(pay).join('\n'), /\*1\.\* Pay\n\*2\.\* Exit\n\*3\.\* Choose a different restroom/);

  const methods = await choose('1');
  const methodText = readable(methods).join('\n');
  assert.match(methodText, /\*1\.\* M-Pesa/);
  assert.match(methodText, /\*4\.\* PayPal/);
  assert.match(methodText, /\*0\.\* Back$/);

  const phone = await choose('1');
  assert.match(readable(phone).join('\n'), /\*1\.\* Use \+254712345678/, 'the WhatsApp number reached the server');

  const summary = await choose('1');
  assert.match(readable(summary).join('\n'), /\*1\.\* Authorize payment/);

  const paid = await choose('1');
  assert.match(paid[0].text, /Payment received/);
  const receipt = paid[1];
  assert.equal(receipt.kind, 'image');
  assert.equal(receipt.mimetype, 'image/png');
  assert.equal(receipt.filename, 'receipt.png');
  assert.ok(Buffer.isBuffer(receipt.buffer) && receipt.buffer.subarray(0, 4).equals(PNG_MAGIC), 'a real PNG');
  assert.ok(paid.some((r) => r.kind === 'image' && /Finding the shortest route/.test(r.caption || '')), 'restroom photo with caption');
  assert.match(readable(paid).join('\n'), /\*1\.\* I have arrived/);
  assert.equal(saved.payments.length, 1, 'paid once, on the server');
  assert.equal(saved.payments[0].phone, PHONE);

  await choose('1'); // I have arrived
  const access = await choose('1'); // Unlock / enter
  const card = access.find((r) => r.kind === 'image');
  assert.ok(card, 'the access card picture');
  assert.ok(card.buffer.length > 1000);
  assert.match(card.caption, /\*1\.\* View QR code/, 'the menu is written under its picture');
  assert.match(readable(await choose('2')).join('\n'), /123456/, 'View OTP');
});

test('posts each message with the bearer token and the WhatsApp chat id', async (t) => {
  const server = await serve(t, answer({ replies: [{ kind: 'text', text: 'ok' }] }));
  const wa = bridgeTo(`${server.url}/`, { token: 'secret-token' });

  assert.deepEqual(readable(await wa.say('hi')), ['ok']);
  await wa.shareLocation();
  await wa.bot.handle({ userId: CHAT, chatId: CHAT, phone: PHONE, type: 'other' });

  const [text, location, sticker] = server.seen;
  assert.equal(text.method, 'POST');
  assert.equal(text.url, '/api/message', 'no double slash');
  assert.equal(text.headers.authorization, 'Bearer secret-token');
  assert.match(text.headers['content-type'], /^application\/json/);
  assert.deepEqual(text.body, { userId: CHAT, phone: PHONE, type: 'text', text: 'hi' });
  assert.deepEqual(location.body, { userId: CHAT, phone: PHONE, type: 'location', lat: NAIROBI.lat, lng: NAIROBI.lng });
  assert.deepEqual(sticker.body, { userId: CHAT, phone: PHONE, type: 'text', text: '' }, 'answered with "please send text"');
});

test('a photo is sent to the server, for cleaning checks', async (t) => {
  const server = await serve(t, answer({ replies: [{ kind: 'text', text: 'Approved!' }] }));
  const wa = bridgeTo(server.url);
  const replies = await wa.bot.handle({
    userId: CHAT, chatId: CHAT, phone: PHONE, type: 'image', image: 'iVBORw0KGgo=', mimetype: 'image/png',
  });
  assert.deepEqual(readable(replies), ['Approved!']);
  assert.deepEqual(server.seen[0].body, {
    userId: CHAT, phone: PHONE, type: 'image', image: 'iVBORw0KGgo=', mimetype: 'image/png',
  });
});

test('sends no Authorization header without a token', async (t) => {
  const server = await serve(t, answer({ replies: [] }));
  const replies = await createRemoteBot({ url: server.url }).handle({ userId: CHAT, type: 'text', text: 'hi' });
  assert.deepEqual(replies, []);
  assert.equal('authorization' in server.seen[0].headers, false);
  assert.deepEqual(server.seen[0].body, { userId: CHAT, type: 'text', text: 'hi' }, 'no phone when WhatsApp gave none');
});

test('turns pictures and menus back into engine replies', async (t) => {
  const picture = await png();
  const server = await serve(
    t,
    answer({
      replies: [
        { kind: 'image', src: dataUrl(picture), alt: 'Route map', caption: 'Your route' },
        { kind: 'image', src: dataUrl(picture, 'image/jpeg'), caption: 'x'.repeat(1500) },
        {
          kind: 'menu',
          text: 'Choose',
          image: dataUrl(picture),
          imageAlt: 'Access card',
          options: [
            { id: '1', title: 'Pay', description: 'KES 100', icon: dataUrl(picture), secondary: false },
            { id: 0, title: 'Back', secondary: true },
          ],
          input: { placeholder: '0712 345 678' },
        },
        { kind: 'video', src: 'x' },
      ],
    }),
  );
  const warn = t.mock.method(console, 'warn', () => {});
  const [route, long, longCaption, menu, ...rest] = await createRemoteBot({ url: server.url }).handle({
    userId: CHAT,
    type: 'text',
    text: 'hi',
  });
  assert.deepEqual(
    { ...route, buffer: undefined },
    { kind: 'image', buffer: undefined, mimetype: 'image/png', filename: 'route-map.png', caption: 'Your route' },
  );
  assert.ok(route.buffer.equals(picture));
  assert.equal(long.filename, 'picture.jpg');
  assert.equal(long.caption, undefined, 'a caption too long for WhatsApp goes as its own message');
  assert.deepEqual(longCaption, { kind: 'text', text: 'x'.repeat(1500) });
  assert.equal(menu.kind, 'menu');
  assert.equal(menu.image.filename, 'access-card.png');
  assert.ok(menu.image.buffer.equals(picture));
  assert.deepEqual(menu.options, [
    { id: '1', title: 'Pay', description: 'KES 100' },
    { id: '0', title: 'Back', secondary: true },
  ]);
  assert.deepEqual(menu.input, { placeholder: '0712 345 678' });
  assert.deepEqual(rest, [], 'an unknown kind is skipped');
  assert.equal(warn.mock.callCount(), 1);
});

test('a server that floods replies is cut short', async (t) => {
  const replies = Array.from({ length: 30 }, (_, i) => ({ kind: 'text', text: `message ${i}` }));
  const server = await serve(t, answer({ replies }));
  t.mock.method(console, 'warn', () => {});
  const out = await createRemoteBot({ url: server.url }).handle({ userId: CHAT, type: 'text', text: 'hi' });
  assert.equal(out.length, 10);
});

// Each of these gets one friendly reply, and the reason in the log.
const FAILURES = {
  'a 500 error': answer({ error: 'boom' }, 500),
  'a wrong token': answer({ error: 'no' }, 401),
  'an answer that is not JSON': answer('<html>Service unavailable</html>'),
  'JSON without replies': answer({ ok: true }),
  'replies that are not objects': answer({ replies: ['hi'] }),
  'a picture that is not an image': answer({ replies: [{ kind: 'image', src: 'data:text/html;base64,PGI+aGk8L2I+' }] }),
  'an SVG picture': answer({ replies: [{ kind: 'image', src: 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=' }] }),
  'a picture that is a web address': answer({ replies: [{ kind: 'image', src: 'https://example.com/a.png' }] }),
  'a picture that is not base64': answer({ replies: [{ kind: 'image', src: 'data:image/png;base64,***' }] }),
  'a picture that is too big': answer({
    replies: [{ kind: 'image', src: dataUrl(Buffer.alloc(MAX_IMAGE_BYTES + 1, 1)) }],
  }),
  'a menu with a bad picture': answer({
    replies: [{ kind: 'menu', text: 'Choose', image: 'data:application/pdf;base64,JVBERi0=', options: [] }],
  }),
  'a menu choice without an id': answer({ replies: [{ kind: 'menu', text: 'Choose', options: [{ title: 'Pay' }] }] }),
};

for (const [name, handler] of Object.entries(FAILURES)) {
  test(`${name} gives the friendly reply`, async (t) => {
    const server = await serve(t, handler);
    const errors = t.mock.method(console, 'error', () => {});
    const replies = await createRemoteBot({ url: server.url, token: 't' }).handle({ userId: CHAT, type: 'text', text: 'hi' });
    assert.deepEqual(replies, [{ kind: 'text', text: DOWN_TEXT }]);
    assert.equal(errors.mock.callCount(), 1);
    assert.match(errors.mock.calls[0].arguments[0], /^\[bridge\] http:\/\/127\.0\.0\.1:\d+\/api\/message: \S/);
  });
}

test('the log says to check the token when the server refuses it', async (t) => {
  const server = await serve(t, answer({ error: 'no' }, 403));
  const errors = t.mock.method(console, 'error', () => {});
  await createRemoteBot({ url: server.url, token: 'wrong' }).handle({ userId: CHAT, type: 'text', text: 'hi' });
  assert.match(errors.mock.calls[0].arguments[0], /status 403 \(check REMOTE_BOT_TOKEN\)/);
});

test('a server that is down gives the friendly reply', async (t) => {
  const server = http.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));

  const errors = t.mock.method(console, 'error', () => {});
  const replies = await createRemoteBot({ url: `http://127.0.0.1:${port}` }).handle({ userId: CHAT, type: 'text', text: 'hi' });
  assert.deepEqual(replies, [{ kind: 'text', text: DOWN_TEXT }]);
  assert.match(errors.mock.calls[0].arguments[0], /could not reach the server: ECONNREFUSED/);
});

test('a server that never answers gives the friendly reply after the timeout', async (t) => {
  const server = await serve(t, () => {});
  const errors = t.mock.method(console, 'error', () => {});
  const started = Date.now();
  const replies = await createRemoteBot({ url: server.url, timeoutMs: 150 }).handle({ userId: CHAT, type: 'text', text: 'hi' });
  assert.deepEqual(replies, [{ kind: 'text', text: DOWN_TEXT }]);
  assert.ok(Date.now() - started < 5000);
  assert.match(errors.mock.calls[0].arguments[0], /no answer within 150 ms/);
});

test('a server that stops halfway through its answer gives the friendly reply', async (t) => {
  const server = await serve(t, (req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.write('{"replies":[');
  });
  const errors = t.mock.method(console, 'error', () => {});
  const replies = await createRemoteBot({ url: server.url, timeoutMs: 150 }).handle({ userId: CHAT, type: 'text', text: 'hi' });
  assert.deepEqual(replies, [{ kind: 'text', text: DOWN_TEXT }]);
  assert.equal(errors.mock.callCount(), 1);
});

test('a failing fetch never crashes the bridge', async (t) => {
  const errors = t.mock.method(console, 'error', () => {});
  const fetch = async () => {
    throw new Error('socket hang up');
  };
  const replies = await createRemoteBot({ url: 'https://tapflash.example', fetch }).handle({ userId: CHAT, type: 'text', text: 'hi' });
  assert.deepEqual(replies, [{ kind: 'text', text: DOWN_TEXT }]);
  assert.match(errors.mock.calls[0].arguments[0], /https:\/\/tapflash\.example\/api\/message: could not reach the server: socket hang up/);
});

test('needs a server address', () => {
  assert.throws(() => createRemoteBot({ url: '' }), /REMOTE_BOT_URL is missing/);
});

test('bridge settings: off by default', () => {
  const config = buildConfig({});
  assert.equal(config.remoteBotUrl, '');
  assert.equal(config.remoteBotToken, '');
  assert.equal(config.remoteBotTimeoutMs, 30000);
});

test('bridge settings: the server address, token and timeout', () => {
  const config = buildConfig({
    REMOTE_BOT_URL: ' https://bot.example.com/ ',
    REMOTE_BOT_TOKEN: 'abc',
    REMOTE_BOT_TIMEOUT_MS: '5000',
  });
  assert.equal(config.remoteBotUrl, 'https://bot.example.com');
  assert.equal(config.remoteBotToken, 'abc');
  assert.equal(config.remoteBotTimeoutMs, 5000);
  assert.equal(buildConfig({ REMOTE_BOT_URL: 'https://example.com/tapflash//' }).remoteBotUrl, 'https://example.com/tapflash');
  assert.equal(buildConfig({ REMOTE_BOT_URL: 'http://localhost:8000' }).remoteBotUrl, 'http://localhost:8000');
  assert.equal(buildConfig({ REMOTE_BOT_URL: 'http://127.0.0.1:8000/' }).remoteBotUrl, 'http://127.0.0.1:8000');
});

test('bridge settings: plain http only for this PC, and only a plain address', () => {
  assert.throws(() => buildConfig({ REMOTE_BOT_URL: 'http://bot.example.com' }), /must start with https:\/\//);
  assert.throws(() => buildConfig({ REMOTE_BOT_URL: 'ftp://localhost' }), /must start with https:\/\//);
  assert.throws(() => buildConfig({ REMOTE_BOT_URL: 'bot.example.com' }), /is not a web address/);
  assert.throws(() => buildConfig({ REMOTE_BOT_URL: 'https://example.com/?x=1' }), /just the server address/);
  assert.throws(
    () => buildConfig({ REMOTE_BOT_URL: 'https://me:pa55@example.com' }),
    (e) => /just the server address/.test(e.message) && !e.message.includes('pa55'),
    'the password is not repeated in the message',
  );
  assert.throws(() => buildConfig({ REMOTE_BOT_TIMEOUT_MS: '0' }), /must be more than 0/);
  assert.throws(() => buildConfig({ REMOTE_BOT_TIMEOUT_MS: 'soon' }), /must be a number/);
});

test('the bridge collects replies the server sent on its own, with its token', async (t) => {
  const server = await serve(t, (req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ messages: [
      { userId: '254712345678@c.us', replies: [{ kind: 'text', text: 'Payment received. Reference *CAP1*.' }] },
      { userId: 7, replies: [] },
    ] }));
  });
  const waiting = await createRemoteBot({ url: server.url, token: 't' }).collect();
  assert.deepEqual(waiting, [
    { userId: '254712345678@c.us', replies: [{ kind: 'text', text: 'Payment received. Reference *CAP1*.' }] },
  ]);
  assert.equal(server.seen[0].method, 'GET');
  assert.equal(server.seen[0].url, '/api/outbox');
  assert.equal(server.seen[0].headers.authorization, 'Bearer t');
});

test('pushed replies are sent to the chat in order with its other replies', async () => {
  const sent = [];
  const connector = { name: 'whatsapp-web', send: async (chatId, reply) => sent.push({ chatId, ...reply }) };
  const handler = createMessageHandler({ bot: { handle: async () => [] }, connector });
  await handler.push(CHAT, [{ kind: 'text', text: 'Payment received.' }]);
  assert.deepEqual(sent, [{ chatId: CHAT, kind: 'text', text: 'Payment received.' }]);
});
