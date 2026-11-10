'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { menuText, menuAsMessages, flattenMenus } = require('../src/bot/replies');

const png = (filename) => ({ kind: 'image', buffer: Buffer.from('png'), mimetype: 'image/png', filename });

const payment = {
  kind: 'menu',
  text: 'How would you like to pay?',
  options: [
    { id: '1', title: 'M-Pesa', description: 'No fees' },
    { id: '2', title: 'Card', description: '1% fee' },
    { id: '0', title: 'Back', secondary: true },
  ],
};

test('menu as text: question, choices with descriptions, then quiet choices', () => {
  assert.equal(
    menuText(payment),
    'How would you like to pay?\n\n*1.* M-Pesa\n_No fees_\n\n*2.* Card\n_1% fee_\n\n*0.* Back',
  );
  const simple = { kind: 'menu', text: 'Ready?', options: [{ id: '1', title: 'Yes' }, { id: '2', title: 'No' }] };
  assert.equal(menuText(simple), 'Ready?\n\n*1.* Yes\n*2.* No');
});

test('options typed as a word show their hint instead of a number', () => {
  const m = { kind: 'menu', text: 'Pick one', options: [{ id: '1', title: 'A' }, { id: 'exit', title: 'Exit', secondary: true, textHint: 'Reply *exit* to leave.' }] };
  assert.equal(menuText(m), 'Pick one\n\n*1.* A\nReply *exit* to leave.');
});

test('on WhatsApp, a picture of the choices is not repeated as text', () => {
  const [msg] = menuAsMessages({ ...payment, optionsImage: png('methods.png') });
  assert.equal(msg.filename, 'methods.png');
  assert.equal(msg.caption, 'How would you like to pay?\n\nReply with a number (1–2).\n\n*0.* Back');
  assert.doesNotMatch(msg.caption, /M-Pesa/);
});

test('a header picture carries the whole menu as its caption', () => {
  const [msg] = menuAsMessages({ ...payment, image: png('map.png') });
  assert.equal(msg.filename, 'map.png');
  assert.equal(msg.caption, menuText(payment));
});

test('a caption too long for WhatsApp becomes a separate text', () => {
  const long = { ...payment, text: 'x'.repeat(1200), image: png('map.png') };
  const out = menuAsMessages(long);
  assert.equal(out.length, 2);
  assert.equal(out[0].caption, undefined);
  assert.equal(out[1].kind, 'text');
});

test('flattenMenus leaves other replies alone', () => {
  const out = flattenMenus([{ kind: 'text', text: 'hi' }, payment, png('qr.png')]);
  assert.deepEqual(out.map((r) => r.kind), ['text', 'text', 'image']);
});
