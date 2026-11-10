'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { buildConfig } = require('../src/config');
const { createBot } = require('../src/bot/engine');
const { createRestroomService } = require('../src/services/restrooms');
const { createMockPayments } = require('../src/payments/mock');
const { createWebConnector } = require('../src/connectors/web');
const { createMessageHandler } = require('../src/index');
const { createRepeatFilter, QUICKEST_ANSWER_MS } = require('../src/util/repeats');

// The real bot behind the web tester's server, wired up as in index.js.
async function serveBot(t) {
  const config = buildConfig({ REPLY_DELAY_MS: '0', WEB_PORT: '0' });
  const saved = { payments: [] };
  const bot = createBot({
    config,
    restrooms: createRestroomService(config),
    payments: createMockPayments(),
    records: { savePayment: async (e) => saved.payments.push(e), saveReport: async () => {} },
    // Midday in Nairobi, so the demo restrooms with opening hours are open.
    now: () => new Date('2026-01-15T09:00:00Z'),
  });
  const connector = createWebConnector(config);
  t.mock.method(console, 'log', () => {});
  await connector.start(createMessageHandler({ bot, connector }));
  t.after(() => connector.stop());
  const base = `http://127.0.0.1:${connector.address().port}`;
  const send = (body) =>
    fetch(`${base}/api/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userId: 'twice', ...body }),
    })
      .then((res) => res.json())
      .then(({ replies }) => replies.map((r) => r.text || '').join('\n---\n'));
  return { send, saved };
}

test('the same message sent twice at once is answered once', async (t) => {
  const { send, saved } = await serveBot(t);
  await send({ type: 'text', text: 'hi' });
  await send({ type: 'location', lat: -1.2833, lng: 36.8219 });

  const [first, second] = await Promise.all([send({ type: 'text', text: '2' }), send({ type: 'text', text: '2' })]);
  assert.match(first, /Central Bus Station Restroom/);
  assert.equal(second, '', 'the copy is dropped, not taken as Exit');

  await send({ type: 'text', text: '1' }); // pay
  await send({ type: 'text', text: '3' }); // card
  const answers = await Promise.all([1, 2, 3].map(() => send({ type: 'text', text: '1' })));
  assert.match(answers[0], /Payment received/);
  assert.deepEqual(answers.slice(1), ['', '']);
  assert.equal(saved.payments.length, 1);

  // A real answer to the next question, once the user has read it, still works.
  await new Promise((resolve) => setTimeout(resolve, QUICKEST_ANSWER_MS + 50));
  assert.match(await send({ type: 'text', text: '1' }), /You have arrived/);
});

test('a repeat is only dropped if the user could not have seen the answer yet', () => {
  const repeats = createRepeatFilter({ now: () => 1000 });
  const msg = (text, receivedAt) => ({ userId: 'u', type: 'text', text, receivedAt });

  repeats.answered(msg('1', 900));
  assert.equal(repeats.isRepeat(msg('1', 950)), true, 'sent while the bot was still answering');
  assert.equal(repeats.isRepeat(msg('1', 1010)), true, 'arrived just after the answer went out');
  assert.equal(repeats.isRepeat(msg('1', 1000 + QUICKEST_ANSWER_MS)), false, 'time to read it: a new choice');
  assert.equal(repeats.isRepeat(msg('2', 950)), false, 'a different message');
  assert.equal(repeats.isRepeat(msg('1', undefined)), false, 'no arrival time (the simulator)');
  assert.equal(repeats.isRepeat({ ...msg('1', 950), userId: 'other' }), false, 'another user');
});
