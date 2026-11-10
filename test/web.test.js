'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const sharp = require('sharp');
const { createWebConnector } = require('../src/connectors/web');

const picture = (width, height, format = 'png') =>
  sharp({ create: { width, height, channels: 3, background: '#2a7f86' } })[format]().toBuffer();

// Start the web tester on a free port with a fake engine. handler(incoming, send)
// returns nothing; it sends replies with send(reply).
async function serve(t, handler) {
  const connector = createWebConnector({ webPort: 0, botName: 'TapFlash' });
  t.mock.method(console, 'log', () => {});
  await connector.start(async (incoming) => handler(incoming, (reply) => connector.send(incoming.chatId, reply)));
  t.after(() => connector.stop());
  return `http://127.0.0.1:${connector.address().port}`;
}

function post(base, body, headers = { 'Content-Type': 'application/json' }) {
  return fetch(`${base}/api/message`, {
    method: 'POST',
    headers,
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}

test('asks the app for menus as they are', () => {
  assert.equal(createWebConnector({ webPort: 0, botName: 'TapFlash' }).supportsMenus, true);
});

test('serves the chat page with security headers', async (t) => {
  const base = await serve(t, async () => {});
  const res = await fetch(`${base}/`);
  assert.equal(res.status, 200);
  assert.match(res.headers.get('content-type'), /^text\/html/);
  assert.equal(res.headers.get('x-content-type-options'), 'nosniff');
  const csp = res.headers.get('content-security-policy');
  assert.match(csp, /default-src 'none'/);
  assert.match(csp, /img-src data: blob:/);
  assert.match(csp, /connect-src 'self'/);
  assert.match(csp, /frame-ancestors 'none'/);
  assert.match(await res.text(), /<title>TapFlash test chat<\/title>/);
});

test('turns a browser message into an incoming message', async (t) => {
  const seen = [];
  const base = await serve(t, async (incoming, send) => {
    seen.push(incoming);
    await send({ kind: 'text', text: `You said ${incoming.text ?? 'a location'}` });
  });

  const before = Date.now();
  const res = await post(base, { userId: 'abc', phone: '+254712345678', type: 'text', text: '1' });
  assert.deepEqual(await res.json(), { replies: [{ kind: 'text', text: 'You said 1' }] });
  const { receivedAt, ...rest } = seen[0];
  assert.deepEqual(rest, { userId: 'web:abc', chatId: 'web:abc', phone: '+254712345678', type: 'text', text: '1' });
  assert.ok(receivedAt >= before && receivedAt <= Date.now(), 'stamped when it arrived');

  await post(base, { userId: 'abc', type: 'location', lat: '-1.285', lng: 36.82 });
  assert.deepEqual(seen[1].location, { lat: -1.285, lng: 36.82 });
  assert.equal(seen[1].type, 'location');
});

test('empty, null or true coordinates are not a place', async (t) => {
  const seen = [];
  const base = await serve(t, async (incoming) => seen.push(incoming));
  for (const [lat, lng] of [[null, null], ['', ''], [true, false], [[1], [2]], [undefined, undefined]]) {
    await post(base, { userId: 'abc', type: 'location', lat, lng });
    const { location } = seen.at(-1);
    const sent = JSON.stringify([lat, lng]);
    assert.ok(Number.isNaN(location.lat) && Number.isNaN(location.lng), `${sent} -> ${JSON.stringify(location)}`);
  }
});

test('a body that is not a JSON object is refused with 400, not a crash', async (t) => {
  const seen = [];
  const base = await serve(t, async (incoming) => seen.push(incoming));
  const errors = t.mock.method(console, 'error', () => {});
  for (const body of ['null', '[]', '"hi"', '5', 'true']) {
    const res = await post(base, body);
    assert.equal(res.status, 400, body);
    assert.deepEqual(await res.json(), { error: 'Send a JSON object' });
  }
  assert.equal(errors.mock.callCount(), 0, 'nothing logged as a server error');
  assert.equal(seen.length, 0);
});

test('a browser that disconnects mid-send is not logged as a server error', async (t) => {
  const base = await serve(t, async () => {});
  const errors = t.mock.method(console, 'error', () => {});
  const { port } = new URL(base);
  await new Promise((resolve, reject) => {
    const socket = net.connect(Number(port), '127.0.0.1', () => {
      socket.write(
        'POST /api/message HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n' +
          'Content-Length: 1000\r\n\r\n{"userId":"u","ty',
      );
      setTimeout(() => socket.destroy(), 50);
    });
    socket.on('close', resolve);
    socket.on('error', reject);
  });
  await new Promise((resolve) => setTimeout(resolve, 150));
  assert.equal(errors.mock.callCount(), 0);

  // still works afterwards
  assert.equal((await post(base, { userId: 'u', type: 'text', text: 'hi' })).status, 200);
});

test('sends menus as tappable choices and leaves out the picture of the choices', async (t) => {
  const photo = await picture(360, 240);
  const badge = await picture(128, 128);
  const map = await picture(800, 500, 'jpeg');
  const list = await picture(800, 640);
  const base = await serve(t, async (incoming, send) => {
    await send({
      kind: 'menu',
      text: 'Here are the closest restrooms.',
      image: { kind: 'image', buffer: map, mimetype: 'image/jpeg', filename: 'route-preview.png' },
      optionsImage: { kind: 'image', buffer: list, mimetype: 'image/png', filename: 'restrooms.png' },
      options: [
        { id: 1, title: 'City Market Restroom', description: '280 m, KES 100', icon: { buffer: photo, mimetype: 'image/png' } },
        { id: '2', title: 'M-Pesa', icon: { buffer: badge, mimetype: 'image/png' } },
        { id: '0', title: 'Back', secondary: true },
      ],
      input: { placeholder: '07XX XXX XXX' },
    });
  });

  const { replies } = await (await post(base, { userId: 'u', type: 'text', text: 'hi' })).json();
  assert.equal(replies.length, 1);
  const [menu] = replies;
  assert.equal(menu.kind, 'menu');
  assert.equal(menu.text, 'Here are the closest restrooms.');
  assert.equal('optionsImage' in menu, false);
  assert.match(menu.image, /^data:image\/jpeg;base64,/);
  assert.deepEqual([menu.imageWidth, menu.imageHeight, menu.imageAlt], [800, 500, 'Route preview']);
  assert.deepEqual(menu.input, { placeholder: '07XX XXX XXX' });

  const [restroom, mpesa, back] = menu.options;
  assert.equal(restroom.id, '1');
  assert.equal(restroom.title, 'City Market Restroom');
  assert.equal(restroom.description, '280 m, KES 100');
  assert.equal(restroom.icon, `data:image/png;base64,${photo.toString('base64')}`);
  assert.deepEqual([restroom.iconWidth, restroom.iconHeight], [360, 240]);
  assert.equal(restroom.secondary, false);
  assert.deepEqual([mpesa.iconWidth, mpesa.iconHeight, mpesa.description], [128, 128, '']);
  assert.deepEqual(back, {
    id: '0',
    title: 'Back',
    description: '',
    secondary: true,
  });
  // The picture of the choices is never sent to the page.
  assert.equal(JSON.stringify(replies).includes(list.toString('base64')), false);
});

test('sends pictures as data URLs, from a buffer or a file', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tapflash-web-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'restroom-city-market.jpg');
  fs.writeFileSync(file, await picture(800, 500, 'jpeg'));
  const qr = await picture(512, 512);

  const base = await serve(t, async (incoming, send) => {
    await send({ kind: 'image', buffer: qr, mimetype: 'image/png', filename: 'receipt-TF8E2258190E.png' });
    await send({ kind: 'image', path: file, caption: 'Finding the shortest route to *City Market*' });
    await send({ kind: 'image', path: path.join(dir, 'missing.png'), caption: 'Text still arrives' });
    await send({ kind: 'image', path: path.join(dir, 'missing.png') });
  });
  t.mock.method(console, 'warn', () => {});

  const { replies } = await (await post(base, { userId: 'u', type: 'text', text: 'pay' })).json();
  assert.equal(replies.length, 3);
  assert.deepEqual(
    { ...replies[0], src: replies[0].src.slice(0, 22) },
    { kind: 'image', src: 'data:image/png;base64,', width: 512, height: 512, alt: 'Receipt', caption: '' },
  );
  assert.match(replies[1].src, /^data:image\/jpeg;base64,/);
  assert.deepEqual([replies[1].width, replies[1].height], [800, 500]);
  assert.equal(replies[1].caption, 'Finding the shortest route to *City Market*');
  assert.deepEqual(replies[2], { kind: 'text', text: 'Text still arrives' });
});

test('answers each message with its own replies, even when sent at once', async (t) => {
  const base = await serve(t, async (incoming, send) => {
    const wait = incoming.text === 'slow' ? 80 : 0;
    await new Promise((resolve) => setTimeout(resolve, wait));
    await send({ kind: 'text', text: `reply to ${incoming.text}` });
  });

  const [slow, fast] = await Promise.all([
    post(base, { userId: 'same', type: 'text', text: 'slow' }).then((r) => r.json()),
    post(base, { userId: 'same', type: 'text', text: 'fast' }).then((r) => r.json()),
  ]);
  assert.deepEqual(slow.replies, [{ kind: 'text', text: 'reply to slow' }]);
  assert.deepEqual(fast.replies, [{ kind: 'text', text: 'reply to fast' }]);
});

test('refuses requests it should not handle', async (t) => {
  const base = await serve(t, async (incoming, send) => send({ kind: 'text', text: 'ok' }));

  assert.equal((await post(base, '{"type":"text"}', { 'Content-Type': 'text/plain' })).status, 415);
  assert.equal((await post(base, '{not json')).status, 400);
  assert.equal((await post(base, { userId: 'u', type: 'text', text: 'x'.repeat(70 * 1024) })).status, 413);
  assert.equal((await fetch(`${base}/api/message`)).status, 405);
  assert.equal((await fetch(`${base}/nope`)).status, 404);

  // still works afterwards
  const res = await post(base, { userId: 'u', type: 'text', text: 'hi' });
  assert.deepEqual(await res.json(), { replies: [{ kind: 'text', text: 'ok' }] });
});

test('reports an engine error instead of hanging', async (t) => {
  const base = await serve(t, async () => {
    throw new Error('engine broke');
  });
  t.mock.method(console, 'error', () => {});
  const res = await post(base, { userId: 'u', type: 'text', text: 'hi' });
  assert.equal(res.status, 500);
  assert.deepEqual(await res.json(), { error: 'engine broke' });
});
