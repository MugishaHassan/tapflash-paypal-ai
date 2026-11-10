'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { buildConfig } = require('../src/config');
const { createBot, STEP, parseChoice } = require('../src/bot/engine');
const { createRestroomService } = require('../src/services/restrooms');
const { createMockPayments } = require('../src/payments/mock');
const { menuText } = require('../src/bot/replies');

const NAIROBI = { lat: -1.2864, lng: 36.8172 };
const USER = 'user-1';
const PHONE = '+254712345678';

// A bot with a controllable clock, captured records and a fixed OTP.
function setup(env = {}, overrides = {}) {
  const config = buildConfig({ REPLY_DELAY_MS: '0', ...env });
  let clock = new Date('2026-10-10T07:00:00Z');
  const saved = { payments: [], reports: [] };
  const bot = createBot({
    config,
    restrooms: createRestroomService(config),
    payments: createMockPayments(),
    records: {
      savePayment: async (e) => saved.payments.push(e),
      saveReport: async (e) => saved.reports.push(e),
    },
    now: () => clock,
    makeOtp: () => '123456',
    ...overrides,
  });
  const say = (body) => bot.handle({ userId: USER, phone: PHONE, type: 'text', text: body });
  const shareLocation = (loc = NAIROBI) => bot.handle({ userId: USER, phone: PHONE, type: 'location', location: loc });
  const advance = (minutes) => {
    clock = new Date(clock.getTime() + minutes * 60_000);
  };
  return { bot, config, say, shareLocation, saved, advance };
}

// Everything the user would read: texts, menus written out, picture captions.
const texts = (replies) =>
  replies
    .map((r) => (r.kind === 'text' ? r.text : r.kind === 'menu' ? menuText(r) : r.caption || ''))
    .filter(Boolean)
    .join('\n---\n');
// Pictures the user sees, including the one at the top of a menu.
const images = (replies) =>
  replies.flatMap((r) => (r.kind === 'image' ? [r] : r.kind === 'menu' && r.image ? [r.image] : []));

// Walk from "hi" to the payment summary with M-Pesa.
async function reachPaymentSummary(t, phone = '0712345678') {
  await t.say('hi');
  await t.shareLocation();
  await t.say('1'); // first restroom
  await t.say('1'); // pay
  await t.say('1'); // M-Pesa
  return t.say(phone);
}

test('greets and asks for location on the first message', async () => {
  const t = setup();
  const out = await t.say('Hi!');
  assert.match(texts(out), /I'm \*TapFlash\*/);
  assert.match(texts(out), /Share your location/);
  assert.match(texts(out), /\*1\.\* Continue\n\*2\.\* Exit/);
});

test('any first message starts the conversation', async () => {
  const t = setup();
  const out = await t.say('where is the toilet');
  assert.match(texts(out), /Share your location/);
});

test('pressing Continue without a location explains how to share it', async () => {
  const t = setup();
  await t.say('hi');
  const out = await t.say('1');
  assert.match(texts(out), /don't have your location yet/);
});

test('sharing a location lists the nearest restrooms, closest first', async () => {
  const t = setup();
  await t.say('hi');
  const out = await t.shareLocation();
  const body = texts(out);
  assert.match(body, /\*1\.\* City Mall Restroom \(demo\)/);
  assert.match(body, /\*4\.\*/);
  assert.doesNotMatch(body, /\*5\.\*/, 'only MAX_RESULTS restrooms are shown');
  assert.match(body, /Which one would you like\?/);
});

test('a location as the very first message greets and lists restrooms', async () => {
  const t = setup();
  const out = await t.shareLocation();
  assert.match(texts(out), /Hello!/);
  assert.match(texts(out), /City Mall Restroom/);
});

test('real data file is used when demo mode is off', async () => {
  const t = setup({ DEMO_RESTROOMS_NEAR_USER: 'false' });
  await t.say('hi');
  const out = await t.shareLocation();
  assert.match(texts(out), /Kenyatta Avenue Restroom \(sample\)/);
});

test('no restrooms nearby is explained', async () => {
  const t = setup({ DEMO_RESTROOMS_NEAR_USER: 'false' });
  await t.say('hi');
  const out = await t.shareLocation({ lat: 51.5, lng: -0.12 }); // London
  assert.match(texts(out), /couldn't find a restroom within 20 km/);
});

test('full happy path: choose, pay with M-Pesa, navigate, arrive, unlock', async () => {
  const t = setup();
  await t.say('hi');
  await t.shareLocation();

  let out = await t.say('1');
  assert.match(texts(out), /^\*City Mall Restroom \(demo\)\*$/m);
  assert.match(texts(out), /Price: KES 100/);
  assert.match(texts(out), /\*1\.\* Pay\n\*2\.\* Exit/);

  out = await t.say('1');
  assert.match(texts(out), /\*1\.\* M-Pesa\n_No fees_/);
  assert.match(texts(out), /\*4\.\* PayPal\n_1% fee_/);

  out = await t.say('1');
  assert.match(texts(out), /Enter your M-Pesa number/);
  assert.match(texts(out), /\*1\.\* Use \+254712345678/);

  out = await t.say('0712 345 678');
  assert.match(texts(out), /Method: M-Pesa \(\+254712345678\)/);
  assert.match(texts(out), /\*Total: KES 100\*/);
  assert.match(texts(out), /Test mode/);

  out = await t.say('1');
  assert.match(texts(out), /Payment received\. Reference \*TF[0-9A-F]{10}\*/);
  assert.equal(images(out).length, 1, 'receipt QR code is sent');
  assert.equal(images(out)[0].mimetype, 'image/png');
  assert.ok(images(out)[0].buffer.length > 100);
  assert.match(texts(out), /Finding the shortest route/);
  assert.match(texts(out), /https:\/\/www\.google\.com\/maps\/dir\/\?api=1&destination=/);
  assert.match(texts(out), /travelmode=walking/);
  assert.equal(t.saved.payments.length, 1);
  assert.equal(t.saved.payments[0].phone, '+254712345678');
  assert.equal(t.saved.payments[0].total, 100);

  out = await t.say('1');
  assert.match(texts(out), /You have arrived at \*City Mall Restroom \(demo\)\*/);

  out = await t.say('1');
  assert.match(texts(out), /Your access details/);
  assert.match(texts(out), /Access code \(OTP\): \*123456\*/);
  assert.match(texts(out), /Valid until: \*10:36\*/, '07:00 UTC is 10:00 in Nairobi, +6 min walk +30 min');
  assert.equal(images(out).length, 1, 'access QR code is sent');

  out = await t.say('2');
  assert.match(texts(out), /Your access code \(OTP\) is \*123456\*/);

  out = await t.say('1');
  assert.equal(images(out).length, 1);

  out = await t.say('3');
  assert.match(texts(out), /Enjoy!/);

  out = await t.say('pass');
  assert.match(texts(out), /Your access details/, 'pass still available after finishing');
});

test('use this WhatsApp number shortcut', async () => {
  const t = setup();
  const out = await reachPaymentSummary(t, '1');
  assert.match(texts(out), /Method: M-Pesa \(\+254712345678\)/);
});

test('card payment adds the 1% fee and skips the phone number', async () => {
  const t = setup();
  await t.say('hi');
  await t.shareLocation();
  await t.say('1');
  await t.say('1');
  const out = await t.say('3'); // card
  assert.match(texts(out), /Method: Card \(Visa \/ Mastercard\)\n/);
  assert.match(texts(out), /Fee: KES 1\n/);
  assert.match(texts(out), /\*Total: KES 101\*/);
});

test('invalid phone number is rejected and can be retried', async () => {
  const t = setup();
  let out = await reachPaymentSummary(t, '12345');
  assert.match(texts(out), /doesn't look like an M-Pesa number/);
  out = await t.say('+254 798 765 432');
  assert.match(texts(out), /\(\+254798765432\)/);
});

test('declined payment offers retry, another method or exit', async () => {
  const t = setup();
  await reachPaymentSummary(t, '0712340000');
  let out = await t.say('1');
  assert.match(texts(out), /declined/);
  assert.match(texts(out), /\*1\.\* Try again\n\*2\.\* Choose another payment method\n\*3\.\* Exit/);
  assert.equal(t.saved.payments.length, 0);

  out = await t.say('2');
  assert.match(texts(out), /How would you like to pay?/);
  await t.say('3'); // card
  out = await t.say('1');
  assert.match(texts(out), /Payment received/);
});

test('payment provider throwing is handled as a failed payment', async () => {
  const t = setup({}, {
    payments: { name: 'broken', isTestMode: false, charge: async () => { throw new Error('network down'); } },
  });
  await reachPaymentSummary(t);
  const out = await t.say('1');
  assert.match(texts(out), /could not be completed/);
});

test('cancel at the payment summary goes back to Pay / Exit', async () => {
  const t = setup();
  await reachPaymentSummary(t);
  const out = await t.say('2');
  assert.match(texts(out), /Pay to get the route/);
});

test('choose a different restroom from the pay prompt', async () => {
  const t = setup();
  await t.say('hi');
  await t.shareLocation();
  await t.say('2');
  let out = await t.say('3');
  assert.match(texts(out), /closest restrooms/);
  out = await t.say('4');
  assert.match(texts(out), /^\*Riverside Park Restroom \(demo\)\*$/m);
});

test('back from payment methods and from phone entry', async () => {
  const t = setup();
  await t.say('hi');
  await t.shareLocation();
  await t.say('1');
  await t.say('1');
  let out = await t.say('0');
  assert.match(texts(out), /Pay to get the route/);
  await t.say('1');
  await t.say('2'); // Airtel
  out = await t.say('0');
  assert.match(texts(out), /How would you like to pay?/);
});

test('unknown replies repeat the current question', async () => {
  const t = setup();
  await t.say('hi');
  await t.shareLocation();
  const out = await t.say('9');
  assert.match(texts(out), /didn't get that/);
  assert.match(texts(out), /Which one would you like?/);
});

test('exit works at any step and says goodbye', async () => {
  const t = setup();
  await t.say('hi');
  await t.shareLocation();
  await t.say('1');
  let out = await t.say('EXIT');
  assert.match(texts(out), /Thanks for using TapFlash/);
  out = await t.say('1');
  assert.match(texts(out), /Hello!/, 'next message starts a new conversation');
});

test('report a location problem while navigating, then continue', async () => {
  const t = setup();
  await reachPaymentSummary(t);
  await t.say('1'); // pay
  let out = await t.say('2');
  assert.match(texts(out), /Describe it in one message/);
  out = await t.say('The gate is locked');
  assert.match(texts(out), /Our team has your report/);
  assert.match(texts(out), /I have arrived/, 'back to the navigation question');
  assert.equal(t.saved.reports.length, 1);
  assert.equal(t.saved.reports[0].message, 'The gate is locked');
  assert.equal(t.saved.reports[0].step, STEP.NAVIGATING);
});

test('a new location after paying does not reset the journey', async () => {
  const t = setup();
  await reachPaymentSummary(t);
  await t.say('1');
  const out = await t.shareLocation();
  assert.match(texts(out), /Got your location/);
  assert.match(texts(out), /I have arrived/);
});

test('hi after paying reminds the user of their pass', async () => {
  const t = setup();
  await reachPaymentSummary(t);
  await t.say('1');
  const out = await t.say('hi');
  assert.match(texts(out), /You still have an access pass for \*City Mall Restroom \(demo\)\* until 10:36/);
});

test('pass expires ACCESS_MINUTES after the walk there', async () => {
  const t = setup();
  await reachPaymentSummary(t);
  await t.say('1');
  t.advance(35); // 6 min walk + 30 min
  assert.match(texts(await t.say('pass')), /Your access details/);
  t.advance(2);
  await t.say('exit');
  const out = await t.say('pass');
  assert.match(texts(out), /Your access pass for \*City Mall Restroom \(demo\)\* expired at 10:36\. Send \*hi\*/);
});

test('idle sessions are forgotten after SESSION_TIMEOUT_MINUTES, and the user is told', async () => {
  const t = setup();
  await t.say('hi');
  await t.shareLocation();
  t.advance(31);
  const out = await t.say('1');
  assert.match(texts(out), /^Our chat timed out, so I have started again\.\n/);
  assert.doesNotMatch(texts(out), /Nothing was charged/, 'nothing was being paid for');
  assert.match(texts(out), /Share your location/);
  assert.match(texts(await t.say('hi')), /Hello!/, 'after that, a normal start');
});

test('stickers and voice notes get a polite note and the current question', async () => {
  const t = setup();
  await t.say('hi');
  const out = await t.bot.handle({ userId: USER, type: 'other' });
  assert.match(texts(out), /only read text and shared locations/);
  assert.match(texts(out), /Share your location/);
});

test('users do not share state', async () => {
  const t = setup();
  await t.say('hi');
  await t.shareLocation();
  const other = await t.bot.handle({ userId: 'user-2', type: 'text', text: '1' });
  assert.match(texts(other), /Hello!/);
});

test('parseChoice accepts common ways of typing a number', () => {
  assert.equal(parseChoice('1'), 1);
  assert.equal(parseChoice(' 2. '), 2);
  assert.equal(parseChoice('3)'), 3);
  assert.equal(parseChoice('1️⃣'), 1);
  assert.equal(parseChoice('one'), null);
  assert.equal(parseChoice('12345'), null);
});

test('a huge message is handled quickly and does not freeze the bot', async () => {
  const t = setup();
  await t.say('hi');
  const started = Date.now();
  const out = await t.say(`${'!'.repeat(60_000)}x`);
  assert.ok(Date.now() - started < 200, `took ${Date.now() - started} ms`);
  assert.match(texts(out), /don't have your location yet/);
});

test('while reporting, commands still work and are not saved as the report', async () => {
  const t = setup();
  await reachPaymentSummary(t);
  await t.say('1'); // pay
  await t.say('2'); // report

  let out = await t.say('help');
  assert.match(texts(out), /How to use TapFlash/);
  assert.match(texts(out), /Describe it in one message/, 'still asking for the report');
  out = await t.say('pass');
  assert.match(texts(out), /Your access details/);
  out = await t.say('back');
  assert.match(texts(out), /I have arrived/, 'back is the Back option');
  await t.say('2');
  for (const notWords of ['99', '👍']) {
    out = await t.say(notWords);
    assert.match(texts(out), /Please describe the problem in words/);
  }
  out = await t.say('Hi!');
  assert.match(texts(out), /Hello!/, 'hi starts over, as the help text says');
  assert.equal(t.saved.reports.length, 0);
});

test('0 or cancel leaves the report step without saving', async () => {
  const t = setup();
  await reachPaymentSummary(t);
  await t.say('1');
  await t.say('2');
  let out = await t.say('0');
  assert.match(texts(out), /I have arrived/);
  await t.say('2');
  out = await t.say('cancel');
  assert.match(texts(out), /I have arrived/);
  assert.equal(t.saved.reports.length, 0);
});

test('exit still works while reporting', async () => {
  const t = setup();
  await reachPaymentSummary(t);
  await t.say('1');
  await t.say('2');
  const out = await t.say('exit');
  assert.match(texts(out), /Thanks for using TapFlash/);
  assert.equal(t.saved.reports.length, 0);
});

// Fake image service: every picture is a tiny "PNG" named after what was drawn.
function fakeImages(behaviour = 'ok') {
  return new Proxy({}, {
    get: (_, name) => async () => {
      if (behaviour === 'throw') throw new Error('drawing failed');
      if (behaviour === 'null') return null;
      return { kind: 'image', buffer: Buffer.from('png'), mimetype: 'image/png', filename: `${name}.png` };
    },
  });
}
const lastMenu = (replies) => replies.filter((r) => r.kind === 'menu').pop();

test('choices come as menus with pictures, without repeating them as text', async () => {
  const t = setup({}, { images: fakeImages() });
  await t.say('hi');

  let out = await t.shareLocation();
  let m = lastMenu(out);
  assert.equal(m.text, 'Here are the closest restrooms. Which one would you like?');
  assert.deepEqual(m.options.map((o) => o.id), ['1', '2', '3', '4', 'exit']);
  assert.equal(m.options[0].title, 'City Mall Restroom (demo)');
  assert.match(m.options[0].description, /^\d+ m, about \d+ min walk, KES 100$/);
  assert.equal(m.options[0].icon.filename, 'restroomThumb.png');
  assert.equal(m.options[4].secondary, true);
  assert.equal(m.optionsImage.filename, 'restroomList.png', 'for WhatsApp, which has no tappable lists');

  out = await t.say('1');
  m = lastMenu(out);
  assert.equal(m.image.filename, 'routePreview.png');
  assert.deepEqual(m.options.map((o) => o.title), ['Pay', 'Exit', 'Choose a different restroom']);

  out = await t.say('1');
  m = lastMenu(out);
  assert.deepEqual(m.options.map((o) => o.title), ['M-Pesa', 'Airtel Money', 'Card (Visa / Mastercard)', 'PayPal', 'Back']);
  assert.equal(m.options[0].icon.filename, 'paymentIcon.png');
  assert.equal(m.optionsImage.filename, 'paymentMethods.png');

  out = await t.say('1');
  m = lastMenu(out);
  assert.deepEqual(m.input, { placeholder: '0712 345 678' });

  await t.say('0712345678');
  out = await t.say('1');
  assert.match(out[1].filename, /^receipt-TF/);
  assert.equal(out[2].filename, 'restroomPhoto.png');
  assert.match(out[2].caption, /Finding the shortest route/);
  m = lastMenu(out);
  assert.equal(m.image.filename, 'routeMap.png');
  assert.match(m.text, /google\.com\/maps/);

  await t.say('1');
  out = await t.say('1');
  m = lastMenu(out);
  assert.equal(m.image.filename, 'accessCard.png');
  assert.doesNotMatch(m.text, /Amount paid/, 'the card already shows the details');
  assert.deepEqual(m.options.map((o) => o.title), ['View QR code', 'View OTP', 'Done', 'Report a problem']);

  out = await t.say('pass');
  assert.equal(out[0].filename, 'accessCard.png');
});

test('repeating a question does not resend the big pictures', async () => {
  const t = setup({}, { images: fakeImages() });
  await t.say('hi');
  await t.shareLocation();
  const out = await t.say('9');
  const m = lastMenu(out);
  assert.equal(m.optionsImage, undefined);
  assert.equal(m.options[0].icon.filename, 'restroomThumb.png', 'small icons are fine');
});

test('if a picture cannot be drawn, the menu is sent without it', async () => {
  for (const behaviour of ['throw', 'null']) {
    const t = setup({}, { images: fakeImages(behaviour) });
    await t.say('hi');
    let out = await t.shareLocation();
    const m = lastMenu(out);
    assert.equal(m.optionsImage, undefined);
    assert.equal(m.options[0].icon, undefined);
    assert.match(texts(out), /\*1\.\* City Mall Restroom \(demo\)\n_\d+ m, about/);
    out = await reachAccess(t);
    assert.match(texts(out), /Access code \(OTP\)/, 'details as text');
    assert.match(lastMenu(out).image.filename, /^access-TF/, 'plain QR code instead of the card');
  }
});

async function reachAccess(t) {
  await t.say('1');
  await t.say('1');
  await t.say('1');
  await t.say('0712345678');
  await t.say('1');
  await t.say('1');
  return t.say('1');
}

// ---- Bug hunt, round 1

// Walk from "hi" to a paid journey with the card (no phone number needed).
async function payByCard(t, restroom = '1') {
  await t.say('hi');
  await t.shareLocation();
  await t.say(restroom);
  await t.say('1'); // pay
  await t.say('3'); // card
  return t.say('1'); // authorize
}

test('an unreadable location keeps the current question, so its choices still mean what they say', async () => {
  const t = setup();
  await reachPaymentSummary(t);
  let out = await t.shareLocation({ lat: NaN, lng: NaN });
  assert.match(texts(out), /couldn't read that location/);
  assert.doesNotMatch(texts(out), /don't have your location yet/);
  assert.deepEqual(lastMenu(out).options.map((o) => o.title), ['Authorize payment', 'Cancel']);
  out = await t.say('1');
  assert.match(texts(out), /Payment received/, 'option 1 is Authorize payment, as shown');
  assert.equal(t.saved.payments.length, 1);

  // On the restroom list, 2 is still the second restroom (City Mall, paid
  // for above, is left out of it).
  await t.say('hi');
  out = await t.shareLocation();
  assert.match(texts(out), /\*2\.\* Market Square Restroom \(demo\)/);
  await t.shareLocation({ lat: NaN, lng: NaN });
  out = await t.say('2');
  assert.match(texts(out), /^\*Market Square Restroom \(demo\)\*$/m);
});

test('a chat that times out at the payment summary says so, and that nothing was charged', async () => {
  const t = setup();
  await reachPaymentSummary(t);
  t.advance(31);
  const out = await t.say('1');
  assert.match(texts(out), /Our chat timed out, so I have started again\. Nothing was charged\./);
  assert.match(texts(out), /Share your location/);
  assert.equal(t.saved.payments.length, 0);
});

test('a paid journey does not time out while its pass works, and a late report is still saved', async () => {
  const t = setup();
  await payByCard(t);
  await t.say('2'); // report
  t.advance(31); // longer than SESSION_TIMEOUT_MINUTES, but the pass still works
  let out = await t.say('The door is locked');
  assert.match(texts(out), /Our team has your report/);
  assert.equal(t.saved.reports.at(-1).message, 'The door is locked');

  await t.say('2'); // report again
  t.advance(70); // the pass, and then the chat, have both run out
  out = await t.say('Nobody opened it');
  assert.match(texts(out), /Our team has your report/);
  assert.match(texts(out), /Our chat timed out/);
  assert.equal(t.saved.reports.at(-1).message, 'Nobody opened it');
  assert.equal(t.saved.reports.at(-1).facilityName, 'City Mall Restroom (demo)');
});

test('an expired pass is named with its expiry time, and Unlock is no longer offered', async () => {
  const t = setup();
  await payByCard(t);
  t.advance(37); // the 6 min walk and 30 min have passed
  let out = await t.say('1'); // I have arrived
  assert.match(texts(out), /Your access pass for \*City Mall Restroom \(demo\)\* expired at 10:36/);
  assert.doesNotMatch(texts(out), /Unlock|don't have an active access pass/);
  assert.deepEqual(lastMenu(out).options.map((o) => o.title), ['Report a problem', 'Find a restroom', 'Exit']);

  out = await t.say('pass');
  assert.match(texts(out), /expired at 10:36/);
  await t.say('1');
  out = await t.say('I was late');
  assert.match(texts(out), /Our team has your report/);
  assert.equal(t.saved.reports[0].step, STEP.PASS_EXPIRED);
  out = await t.say('2');
  assert.match(texts(out), /Here are the closest restrooms/);
});

test('the access screen says when the pass has expired', async () => {
  const t = setup();
  await payByCard(t);
  await t.say('1');
  await t.say('1'); // unlock: the access screen
  t.advance(40);
  const out = await t.say('2'); // View OTP
  assert.match(texts(out), /expired at 10:36/);
  assert.doesNotMatch(texts(out), /123456/);
});

test('a far restroom shows the walk in hours, and the pass and the chat last for the walk', async () => {
  const t = setup({ DEMO_RESTROOMS_NEAR_USER: 'false' });
  await t.say('hi');
  let out = await t.shareLocation({ lat: -1.2, lng: 36.9 });
  assert.match(texts(out), /12\.7 km, about 3 h 18 min walk/);
  await t.say('1');
  await t.say('1');
  await t.say('3');
  await t.say('1'); // paid at 10:00
  t.advance(200); // still walking
  out = await t.say('1');
  assert.match(texts(out), /You have arrived/);
  out = await t.say('1');
  assert.match(texts(out), /Valid until: \*13:48\*/, '10:00 + 198 min walk + 30 min');
});

test('restrooms that are closed when the user would arrive are not listed or charged for', async () => {
  // 20:30 in Nairobi: Uhuru Park (6 AM to 6 PM) and Jeevanjee (to 7 PM) are closed.
  let t = setup({ DEMO_RESTROOMS_NEAR_USER: 'false' });
  t.advance(10 * 60 + 30);
  await t.say('hi');
  let out = await t.shareLocation({ lat: -1.2885, lng: 36.8131 });
  assert.doesNotMatch(texts(out), /Uhuru Park|Jeevanjee/);
  assert.match(texts(out), /\*1\.\* Kenyatta Avenue Restroom \(sample\)/);

  // 17:50: Uhuru Park is listed, but it closes before the payment goes through.
  t = setup({ DEMO_RESTROOMS_NEAR_USER: 'false' });
  t.advance(7 * 60 + 50);
  await t.say('hi');
  out = await t.shareLocation({ lat: -1.2885, lng: 36.8131 });
  assert.match(texts(out), /\*1\.\* Uhuru Park Restroom \(sample\)/);
  await t.say('1');
  await t.say('1');
  await t.say('3');
  t.advance(11);
  out = await t.say('1');
  assert.match(texts(out), /\*Uhuru Park Restroom \(sample\)\* will be closed by the time you get there/);
  assert.match(texts(out), /Here are the closest restrooms/);
  assert.equal(t.saved.payments.length, 0);
});

test('when every restroom nearby is closed, the bot says so', async () => {
  const closed = { id: 'x', name: 'X', ...NAIROBI, price: 100, hours: '6:00 AM – 7:00 AM', distanceKm: 0 };
  const t = setup({}, { restrooms: { nearest: (loc, at) => (at ? [] : [closed]) } });
  await t.say('hi');
  const out = await t.shareLocation();
  assert.match(texts(out), /restrooms near you are closed now/);
  assert.doesNotMatch(texts(out), /couldn't find a restroom within/);
});

test('typing the word on a choice works like its number', async () => {
  const t = setup();
  await t.say('hi');
  await t.shareLocation();
  await t.say('1');
  await t.say('1'); // payment methods, with 0: Back
  let out = await t.say('back');
  assert.match(texts(out), /Pay to get the route/);
  await t.say('pay');
  await t.say('m-pesa');
  out = await t.say('Back');
  assert.match(texts(out), /How would you like to pay\?/, 'back on the phone question');
  await t.say('1');
  await t.say('0712345678');
  out = await t.say('cancel'); // the summary's Cancel option, not "exit"
  assert.match(texts(out), /Pay to get the route/);
  assert.doesNotMatch(texts(out), /Thanks for using/);
  out = await t.say('help?');
  assert.match(texts(out), /How to use TapFlash/);
});

test('a second pass does not replace the first one', async () => {
  const t = setup();
  await payByCard(t, '1'); // City Mall
  await t.say('3'); // choose a different restroom while walking
  await t.say('1'); // Central Bus Station (City Mall, already paid for, is left out)
  await t.say('1');
  await t.say('3');
  await t.say('1');
  assert.equal(t.saved.payments.length, 2);
  const out = await t.say('pass');
  assert.match(texts(out), /Restroom: City Mall Restroom \(demo\)/);
  assert.match(texts(out), /Restroom: Central Bus Station Restroom \(demo\)/);
  const reminders = texts(await t.say('hi'));
  assert.match(reminders, /access pass for \*City Mall Restroom \(demo\)\*/);
  assert.match(reminders, /access pass for \*Central Bus Station Restroom \(demo\)\*/);
});

test('after a problem report the user can choose another restroom, and can report from the access screen', async () => {
  const t = setup();
  await payByCard(t);
  await t.say('2');
  let out = await t.say('The restroom is closed');
  assert.match(texts(out), /Our team has your report/);
  assert.match(texts(out), /\*3\.\* Choose a different restroom/);
  out = await t.say('3');
  assert.match(texts(out), /Here are the closest restrooms/);

  const u = setup();
  await payByCard(u);
  await u.say('1');
  out = await u.say('1'); // access screen
  assert.match(texts(out), /\*4\.\* Report a problem/);
  await u.say('4');
  out = await u.say('The code does not work');
  assert.match(texts(out), /Our team has your report/);
  assert.equal(u.saved.reports[0].step, STEP.ACCESS);
  assert.match(texts(out), /\*1\.\* View QR code/, 'back on the access screen');
});

// ---- Bug hunt, round 2

const FAR_AWAY = { lat: -1.33, lng: 36.88 }; // about 8 km from Nairobi CBD

// From City Market, pay by card for Kenyatta Avenue (280 m, 4 min walk).
async function payForKenyatta(t) {
  await t.say('hi');
  await t.shareLocation({ lat: -1.2833, lng: 36.8219 });
  await t.say('2');
  await t.say('1');
  await t.say('3');
  return t.say('1');
}

test('a location shared after paying is kept, so a different restroom is looked for from there', async () => {
  const t = setup({ DEMO_RESTROOMS_NEAR_USER: 'false' });
  await payForKenyatta(t);
  let out = await t.shareLocation(FAR_AWAY);
  assert.match(texts(out), /Got your location/);
  assert.match(texts(out), /I have arrived/, 'the journey goes on');

  out = await t.say('3'); // choose a different restroom
  assert.match(texts(out), /\*1\.\* Railways Bus Terminus Restroom \(sample\)\n_7\.4 km, about 1 h 55 min walk/);
  assert.doesNotMatch(texts(out), / m, about/, 'nothing is a few metres away any more');
  await t.say('1');
  await t.say('1');
  await t.say('3');
  await t.say('1'); // paid at 10:00
  await t.say('1');
  out = await t.say('1');
  assert.match(texts(out), /Valid until: \*12:25\*/, '10:00 + 115 min walk from where the user is now + 30 min');
});

test('Find a restroom after the pass ran out uses the location shared on the way', async () => {
  const t = setup({ DEMO_RESTROOMS_NEAR_USER: 'false' });
  await payForKenyatta(t);
  await t.shareLocation(FAR_AWAY);
  t.advance(40); // the 4 min walk and 30 min have passed
  await t.say('1'); // the pass has run out
  const out = await t.say('2'); // Find a restroom
  assert.match(texts(out), /\*1\.\* Railways Bus Terminus Restroom \(sample\)\n_7\.4 km/);
});

test('Choose a different restroom leaves out the one the user already has a pass for', async () => {
  const t = setup();
  await payByCard(t); // City Mall
  let out = await t.say('3');
  assert.doesNotMatch(texts(out), /City Mall/);
  assert.match(texts(out), /\*1\.\* Central Bus Station Restroom \(demo\)/);
  assert.match(texts(out), /\*4\.\* Fuel Station Restroom \(demo\)/, 'the next closest takes its place');

  // A new search leaves it out too, and reminds the user of the pass.
  out = await t.say('hi');
  assert.match(texts(out), /You still have an access pass for \*City Mall Restroom \(demo\)\*/);
  out = await t.shareLocation();
  assert.doesNotMatch(texts(out), /City Mall/);

  // Once the pass has run out, it can be bought again.
  t.advance(37);
  await t.say('hi');
  out = await t.shareLocation();
  assert.match(texts(out), /\*1\.\* City Mall Restroom \(demo\)/);
  assert.equal(t.saved.payments.length, 1);
});

test('when the user has a pass for every open restroom nearby, the bot says so', async () => {
  const only = { id: 'only', name: 'Only Restroom', ...NAIROBI, price: 100, hours: '24 hours', distanceKm: 0.1 };
  const t = setup({}, { restrooms: { nearest: (loc, at, leaveOut) => (leaveOut?.has(only.id) ? [] : [{ ...only }]) } });
  await payByCard(t);
  const out = await t.say('3'); // choose a different restroom
  assert.match(texts(out), /You already have an access pass for the open restrooms near you/);
  assert.doesNotMatch(texts(out), /closed now|couldn't find/);
  assert.equal(t.saved.payments.length, 1);
});

test('a pass ends when the restroom closes, and the summary says so before paying', async () => {
  const t = setup({ DEMO_RESTROOMS_NEAR_USER: 'false' });
  t.advance(7 * 60 + 45); // 17:45 in Nairobi; Uhuru Park closes at 6:00 PM
  await t.say('hi');
  await t.shareLocation({ lat: -1.2885, lng: 36.8131 });
  let out = await t.say('1');
  assert.match(texts(out), /^\*Uhuru Park Restroom \(sample\)\*$/m);
  await t.say('1');
  out = await t.say('3'); // card
  assert.match(texts(out), /The restroom closes at 18:00, so your pass ends then\./);
  await t.say('1'); // paid
  await t.say('1');
  out = await t.say('1');
  assert.match(texts(out), /Valid until: \*18:00\*/, 'not 18:16 (1 min walk + 30 min)');
  assert.equal(t.saved.payments[0].expiresAt, '2026-10-10T15:00:00.000Z');

  // Earlier in the day the pass is not cut short, and nothing is said.
  const u = setup({ DEMO_RESTROOMS_NEAR_USER: 'false' });
  await u.say('hi');
  await u.shareLocation({ lat: -1.2885, lng: 36.8131 });
  await u.say('1');
  await u.say('1');
  assert.doesNotMatch(texts(await u.say('3')), /closes at/);
});

test('after a chat timed out, help or pass do not use up the timeout notice', async () => {
  for (const command of ['pass', 'help']) {
    const t = setup();
    await reachPaymentSummary(t);
    t.advance(31);
    await t.say(command);
    const out = await t.say('1'); // meant as Authorize payment
    assert.match(texts(out), /^Our chat timed out, so I have started again\. Nothing was charged\./, command);
    assert.equal(t.saved.payments.length, 0);
  }
});

test('after exit, an old timeout is no longer mentioned', async () => {
  const t = setup();
  await reachPaymentSummary(t);
  t.advance(31);
  await t.say('exit');
  const out = await t.say('1');
  assert.match(texts(out), /^Hello!/);
  assert.doesNotMatch(texts(out), /timed out/);
});
