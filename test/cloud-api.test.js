'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { interactiveFor, toIncoming, validSignature, createCloudApiConnector } = require('../src/connectors/cloud-api');
const { createStateFile } = require('../src/services/state-file');
const { buildConfig } = require('../src/config');

test('three short choices become WhatsApp reply buttons', () => {
  const m = { kind: 'menu', text: 'Ready?', options: [{ id: '1', title: 'Pay' }, { id: '2', title: 'Exit' }] };
  const i = interactiveFor(m);
  assert.equal(i.type, 'button');
  assert.deepEqual(i.action.buttons[0], { type: 'reply', reply: { id: '1', title: 'Pay' } });
});

test('more or longer choices become a list, keeping long wording in the description', () => {
  const m = {
    kind: 'menu',
    text: 'Pick one',
    options: [
      { id: '1', title: 'Pay' },
      { id: '2', title: 'Exit' },
      { id: '3', title: 'Choose a different restroom' },
    ],
  };
  const i = interactiveFor(m);
  assert.equal(i.type, 'list');
  const row = i.action.sections[0].rows[2];
  assert.ok(row.title.length <= 24);
  assert.equal(row.description, 'Choose a different restroom');
});

test('more than 10 choices fall back to text', () => {
  const options = Array.from({ length: 11 }, (_, n) => ({ id: String(n), title: `Option ${n}` }));
  assert.equal(interactiveFor({ kind: 'menu', text: 'x', options }), null);
});

test('Meta messages become incoming messages; taps send back the option id', () => {
  assert.deepEqual(toIncoming({ from: '254712345678', type: 'text', text: { body: 'hi' } }), {
    userId: '254712345678', chatId: '254712345678', phone: '+254712345678', type: 'text', text: 'hi',
  });
  const tap = toIncoming({ from: '254712345678', type: 'interactive', interactive: { type: 'list_reply', list_reply: { id: '3', title: 'x' } } });
  assert.equal(tap.text, '3');
  const loc = toIncoming({ from: '254712345678', type: 'location', location: { latitude: -1.28, longitude: 36.82 } });
  assert.deepEqual(loc.location, { lat: -1.28, lng: 36.82 });
  assert.equal(toIncoming({ from: '254712345678', type: 'sticker' }).type, 'other');
  assert.equal(toIncoming({ from: 'bad', type: 'text' }), null);
});

test('webhook signatures are checked against the app secret', () => {
  const body = Buffer.from('{"a":1}');
  const sig = `sha256=${crypto.createHmac('sha256', 's3cret').update(body).digest('hex')}`;
  assert.equal(validSignature(body, sig, 's3cret'), true);
  assert.equal(validSignature(body, sig, 'other'), false);
  assert.equal(validSignature(body, undefined, 's3cret'), false);
});

test('the connector refuses to start without its Meta settings', () => {
  assert.throws(() => createCloudApiConnector(buildConfig({})), /WHATSAPP_TOKEN is missing/);
});

test('webhook verification and message delivery over HTTP', async () => {
  const config = buildConfig({
    WHATSAPP_TOKEN: 't', WHATSAPP_PHONE_NUMBER_ID: '1', WHATSAPP_VERIFY_TOKEN: 'v', WHATSAPP_APP_SECRET: 's', PORT: '0',
  });
  const connector = createCloudApiConnector(config);
  const got = [];
  await connector.start(async (m) => got.push(m));
  const address = connector.address();
  const base = `http://127.0.0.1:${address.port}`;

  let res = await fetch(`${base}/webhook?hub.mode=subscribe&hub.verify_token=v&hub.challenge=42`);
  assert.equal(await res.text(), '42');
  res = await fetch(`${base}/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=42`);
  assert.equal(res.status, 403);

  const message = { id: 'm1', from: '254712345678', timestamp: String(Math.floor(Date.now() / 1000)), type: 'text', text: { body: 'hi' } };
  const body = JSON.stringify({ entry: [{ changes: [{ value: { messages: [message] } }] }] });
  const sig = `sha256=${crypto.createHmac('sha256', 's').update(body).digest('hex')}`;
  res = await fetch(`${base}/webhook`, { method: 'POST', body, headers: { 'X-Hub-Signature-256': sig } });
  assert.equal(res.status, 200);
  await fetch(`${base}/webhook`, { method: 'POST', body, headers: { 'X-Hub-Signature-256': sig } }); // Meta retry
  res = await fetch(`${base}/webhook`, { method: 'POST', body, headers: { 'X-Hub-Signature-256': 'sha256=bad' } });
  assert.equal(res.status, 401);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(got.length, 1, 'the retried message is answered once');
  assert.equal(got[0].text, 'hi');
  await connector.stop();
});

test('saved state comes back with its dates', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tapflash-state-'));
  const file = createStateFile(path.join(dir, 'state.json'));
  assert.equal(file.read(), null);
  const when = new Date('2026-10-20T10:00:00.000Z');
  await file.write({ passes: [['u', [{ expiresAt: when, name: 'x' }]]] });
  const back = file.read();
  assert.ok(back.passes[0][1][0].expiresAt instanceof Date);
  assert.equal(back.passes[0][1][0].expiresAt.getTime(), when.getTime());
  fs.rmSync(dir, { recursive: true, force: true });
});

test('a chat in the middle of paying survives a restart', async () => {
  const { createBot } = require('../src/bot/engine');
  const { createRestroomService } = require('../src/services/restrooms');
  const { createMockPayments } = require('../src/payments/mock');
  const { replace, revive } = require('../src/services/state-file');
  const config = buildConfig({});
  const make = () =>
    createBot({
      config,
      restrooms: createRestroomService(config),
      payments: createMockPayments(),
      records: { savePayment: async () => {}, saveReport: async () => {} },
    });
  const restart = (old) => {
    const bot = make();
    bot.loadState(JSON.parse(JSON.stringify(old.saveState(), replace), revive));
    return bot;
  };
  const user = { userId: 'u', phone: '+254712345678' };
  let bot = make();
  const say = (text) => bot.handle({ ...user, type: 'text', text });
  await say('hi');
  await bot.handle({ ...user, type: 'location', location: { lat: -1.285, lng: 36.82 } });
  for (const t of ['1', '1', '3']) await say(t);
  bot = restart(bot);
  let out = await say('1');
  assert.match(out[0].text, /Payment received/);
  bot = restart(bot);
  out = await say('pass');
  assert.match(out[0].caption || out[0].text, /Access code \(OTP\)/);
});
