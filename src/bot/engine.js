'use strict';

const crypto = require('crypto');
const { createMessages } = require('./messages');
const { createSessionStore, createPassStore } = require('./sessions');
const { feeFor, formatDateTime, normalizePhone } = require('../services/format');
const { directionsUrl } = require('../services/maps');
const { qrImage } = require('../services/qr');
const { isValidLocation, isOpenOnArrival, nextClosing, walkingMinutes } = require('../services/restrooms');

// The conversation, step by step. This file knows nothing about WhatsApp:
// it takes one incoming message and returns the list of replies to send
// (text, images and menus; see replies.js).
//
// incoming: { userId, phone?, type: 'text' | 'location' | 'other', text?, location?: { lat, lng } }
//
// `images` (optional) draws the pictures from the demo video: restroom photos,
// route maps, payment icons, the access card. Without it the bot sends text only.

const STEP = Object.freeze({
  AWAIT_LOCATION: 'await_location',
  CHOOSE_RESTROOM: 'choose_restroom',
  CONFIRM_PAY: 'confirm_pay',
  CHOOSE_METHOD: 'choose_method',
  AWAIT_PHONE: 'await_phone',
  AUTHORIZE: 'authorize',
  PAYMENT_FAILED: 'payment_failed',
  NAVIGATING: 'navigating',
  ARRIVED: 'arrived',
  ACCESS: 'access',
  REPORT: 'report',
  PASS_EXPIRED: 'pass_expired',
});

// Once paid, a new location must not throw the user back to the restroom list.
const PAID_STEPS = new Set([STEP.NAVIGATING, STEP.ARRIVED, STEP.ACCESS, STEP.REPORT]);
// The steps that need a pass that still works.
const PASS_STEPS = new Set([STEP.NAVIGATING, STEP.ARRIVED, STEP.ACCESS]);
// Steps between choosing a restroom and paying for it.
const PAYING_STEPS = new Set([
  STEP.CONFIRM_PAY,
  STEP.CHOOSE_METHOD,
  STEP.AWAIT_PHONE,
  STEP.AUTHORIZE,
  STEP.PAYMENT_FAILED,
]);

const GREETINGS = new Set(['hi', 'hello', 'hey', 'hi there', 'start', 'menu', 'restart', 'jambo', 'habari']);
const EXITS = new Set(['exit', 'stop', 'cancel', 'quit', 'bye', 'goodbye']);
const PASS_WORDS = new Set(['pass', 'my pass', 'ticket']);
const HELP_WORDS = new Set(['help']);

const text = (t) => ({ kind: 'text', text: t });

function option(id, title, extra = {}) {
  return { id: String(id), title, ...extra };
}

// A question with choices. Pictures that could not be drawn are left out.
function menu(question, options, { image, optionsImage, input } = {}) {
  const m = { kind: 'menu', text: question, options };
  if (image) m.image = image;
  if (optionsImage) m.optionsImage = optionsImage;
  if (input) m.input = input;
  return m;
}

const BACK = option(0, 'Back', { secondary: true });
const EXIT = option('exit', 'Exit', { secondary: true, textHint: 'Reply *exit* to leave.' });

// "1", "1.", "1)", " 2 " and the keycap emoji "1️⃣" all mean option 1.
function parseChoice(input) {
  const cleaned = input.replace(/[️⃣]/g, '').trim();
  const match = /^(\d{1,2})[.)]?$/.exec(cleaned);
  return match ? Number(match[1]) : null;
}

// "Hi!" -> "hi", "help?" -> "help", "  My   Pass " -> "my pass". Commands are
// short, so long messages are skipped (and can't make the trailing-punctuation
// regex slow).
const MAX_COMMAND_LENGTH = 40;
function normalizeWords(input) {
  if (input.length > MAX_COMMAND_LENGTH) return '';
  return input.toLowerCase().replace(/\s+/g, ' ').replace(/[!.,?]+$/, '').trim();
}

// The names of the numbered choices in the last menu of a reply, so typing a
// name ("back", "cancel") works like its number: { 'back' -> '0', ... }.
function choicesIn(replies) {
  const last = replies.filter((r) => r.kind === 'menu').pop();
  if (!last) return null;
  return new Map(last.options.filter((o) => /^\d+$/.test(o.id)).map((o) => [normalizeWords(o.title), o.id]));
}

// A problem report has to say something: "99" or a thumbs-up does not.
const describesProblem = (input) => /\p{L}/u.test(input);

function defaultOtp() {
  return String(crypto.randomInt(0, 1_000_000)).padStart(6, '0');
}

function createBot({ config, restrooms, payments, records, images, now = () => new Date(), makeOtp = defaultOtp }) {
  const msg = createMessages(config);
  const sessions = createSessionStore({ timeoutMs: config.sessionTimeoutMinutes * 60_000, now });
  const passes = createPassStore({ now });
  const methods = [
    { id: 'mpesa', label: 'M-Pesa', needsPhone: true, feePercent: 0 },
    { id: 'airtel', label: 'Airtel Money', needsPhone: true, feePercent: 0 },
    { id: 'card', label: 'Card (Visa / Mastercard)', needsPhone: false, feePercent: config.cardFeePercent },
    { id: 'paypal', label: 'PayPal', needsPhone: false, feePercent: config.cardFeePercent },
  ];

  async function handle(incoming) {
    const replies = await respond(incoming);
    // Remember the choices now on screen, for users who type a choice's name.
    const session = sessions.get(incoming.userId);
    if (session) session.choices = choicesIn(replies);
    return replies;
  }

  async function respond(incoming) {
    const { userId } = incoming;
    const session = sessions.get(userId);
    if (session) {
      sessions.touch(session);
      session.whatsappPhone = normalizePhone(incoming.phone || '', config.countryCode);
    }

    if (incoming.type === 'location') return onLocation(incoming, session);

    const raw = incoming.type === 'text' ? (incoming.text || '').trim() : '';
    if (!raw) {
      // A sticker, voice note, photo, ...
      if (!session) return restart(incoming, '');
      return [text(msg.textOnly()), ...(await prompt(session))];
    }

    const words = normalizeWords(raw);
    const named = session?.choices?.get(words);
    if (named !== undefined) return onReply(session, named, userId);
    if (GREETINGS.has(words)) return startConversation(incoming);
    // "cancel" while writing a problem report only leaves the report.
    if (EXITS.has(words) && !(words === 'cancel' && session?.step === STEP.REPORT)) return exit(userId);
    if (PASS_WORDS.has(words)) return showPass(userId, session);
    if (HELP_WORDS.has(words)) return [text(msg.help()), ...(session ? await prompt(session) : [])];
    if (!session) return restart(incoming, raw);

    return onReply(session, raw, userId);
  }

  // A chat that timed out mid-way is explained, not silently restarted. Only
  // an answer uses this up: after "help" or "pass", the next answer still
  // hears that the chat timed out.
  function restart(incoming, raw) {
    const old = sessions.takeTimedOut(incoming.userId);
    return old ? afterTimeout(incoming, old, raw) : startConversation(incoming);
  }

  // A picture, or null when there are no images or drawing fails (no internet
  // for the map, for example). The conversation never depends on a picture.
  async function draw(make) {
    if (!images) return null;
    try {
      return (await make(images)) || null;
    } catch (err) {
      console.error('[images] could not draw an image:', err.message);
      return null;
    }
  }

  // A picture with a caption, or the caption alone.
  async function captioned(make, caption) {
    const image = await draw(make);
    return image ? { ...image, caption } : text(caption);
  }

  function newSession(incoming, opening = msg.greeting()) {
    const session = sessions.start(incoming.userId);
    session.whatsappPhone = normalizePhone(incoming.phone || '', config.countryCode);
    const out = [text(opening)];
    for (const pass of passes.active(incoming.userId)) out.push(text(msg.activePassReminder(pass)));
    return { session, out };
  }

  function startConversation(incoming) {
    const { session, out } = newSession(incoming);
    session.step = STEP.AWAIT_LOCATION;
    return [...out, locationMenu()];
  }

  // The user answered a question from a chat that has timed out. Say so (and
  // that nothing was charged) before starting again. A problem report they
  // were writing is still saved.
  async function afterTimeout(incoming, old, raw) {
    const out = [];
    if (old.step === STEP.REPORT && describesProblem(raw)) {
      await saveReport(old, raw, incoming.userId);
      out.push(text(msg.reportThanks()));
    }
    const started = newSession(incoming, msg.timedOut(PAYING_STEPS.has(old.step)));
    started.session.step = STEP.AWAIT_LOCATION;
    return [...out, ...started.out, locationMenu()];
  }

  async function onLocation(incoming, session) {
    const loc = incoming.location;
    if (!isValidLocation(loc)) {
      if (!session) return startConversation(incoming);
      // Repeat the question the user is on, so its choices still do what they say.
      return [text(msg.badLocation()), ...(await prompt(session))];
    }
    if (!session) {
      const started = newSession(incoming);
      started.session.location = loc;
      return [...started.out, ...(await showRestrooms(started.session, incoming.userId))];
    }
    // After paying, the journey goes on, but the new location is kept: another
    // restroom is then looked for from where the user is now.
    session.location = loc;
    if (PAID_STEPS.has(session.step)) return [text(msg.locationNoted()), ...(await prompt(session))];
    return showRestrooms(session, incoming.userId);
  }

  async function showRestrooms(session, userId) {
    // A restroom the user already has a working pass for is left out, so it
    // is never paid for twice.
    const paidFor = new Set(passes.active(userId).map((pass) => pass.facility.id));
    session.options = restrooms.nearest(session.location, now(), paidFor);
    if (session.options.length === 0) {
      session.step = STEP.AWAIT_LOCATION;
      if (paidFor.size > 0 && restrooms.nearest(session.location, now()).length > 0) {
        return [text(msg.allPaidFor())];
      }
      // Without the time, closed restrooms count too: are there any at all?
      const anyNearby = restrooms.nearest(session.location).length > 0;
      return [text(anyNearby ? msg.allClosed() : msg.noneNearby())];
    }
    session.step = STEP.CHOOSE_RESTROOM;
    return [await restroomMenu(session, { pictures: true })];
  }

  // ---- Menus. `pictures: false` is used when repeating a question, so big
  // pictures (maps, cards) are not sent again.

  function locationMenu(question = msg.askLocation()) {
    return menu(question, [option(1, 'Continue'), option(2, 'Exit', { secondary: true })]);
  }

  async function restroomMenu(session, { pictures = false } = {}) {
    const choices = await Promise.all(
      session.options.map(async (r, i) =>
        option(i + 1, r.name, {
          description: msg.restroomDetails(r),
          icon: (await draw((im) => im.restroomThumb(r))) || undefined,
        }),
      ),
    );
    const optionsImage = pictures ? await draw((im) => im.restroomList(session.options)) : null;
    return menu(msg.chooseRestroom(), [...choices, EXIT], { optionsImage });
  }

  async function payMenu(session, { pictures = false } = {}) {
    const r = session.restroom;
    const image = pictures ? await draw((im) => im.routePreview(session.location, r)) : null;
    return menu(
      msg.payPrompt(r),
      [option(1, 'Pay'), option(2, 'Exit'), option(3, 'Choose a different restroom')],
      { image },
    );
  }

  async function methodMenu({ pictures = false } = {}) {
    const choices = await Promise.all(
      methods.map(async (m, i) =>
        option(i + 1, m.label, {
          description: msg.methodFee(m),
          icon: (await draw((im) => im.paymentIcon(m))) || undefined,
        }),
      ),
    );
    const optionsImage = pictures ? await draw((im) => im.paymentMethods(methods)) : null;
    return menu(msg.choosePaymentMethod(), [...choices, BACK], { optionsImage });
  }

  function phoneMenu(session, question = msg.askPhone(session.method)) {
    const choices = session.whatsappPhone ? [option(1, msg.useWhatsAppNumber(session.whatsappPhone))] : [];
    return menu(question, [...choices, BACK], { input: { placeholder: '0712 345 678' } });
  }

  function summaryMenu(session) {
    return menu(msg.paymentSummary(session.payment, payments.isTestMode), [
      option(1, 'Authorize payment'),
      option(2, 'Cancel', { secondary: true }),
    ]);
  }

  function failedMenu(session) {
    return menu(msg.paymentFailed(session.failReason), [
      option(1, 'Try again'),
      option(2, 'Choose another payment method'),
      option(3, 'Exit', { secondary: true }),
    ]);
  }

  async function journeyMenu(session, { pictures = false } = {}) {
    const r = session.restroom;
    const image = pictures ? await draw((im) => im.routeMap(session.location, r)) : null;
    return menu(
      msg.startJourney(directionsUrl(r)),
      [
        option(1, 'I have arrived'),
        option(2, 'Report a location problem', { secondary: true }),
        option(3, 'Choose a different restroom', { secondary: true }),
      ],
      { image },
    );
  }

  function arrivedMenu(session) {
    return menu(msg.arrived(session.restroom), [
      option(1, 'Unlock / enter'),
      option(2, 'Report a location problem', { secondary: true }),
      option(3, 'Choose a different restroom', { secondary: true }),
    ]);
  }

  const accessChoices = () => [
    option(1, 'View QR code'),
    option(2, 'View OTP'),
    option(3, 'Done'),
    option(4, 'Report a problem', { secondary: true }),
  ];

  function reportMenu() {
    return menu(msg.askReport(), [BACK], { input: { placeholder: 'Describe the problem' } });
  }

  function expiredMenu(session) {
    return menu(msg.passExpired(session.pass), [
      option(1, 'Report a problem'),
      option(2, 'Find a restroom'),
      option(3, 'Exit', { secondary: true }),
    ]);
  }

  // A journey can't go on once its pass has run out: switch to saying so,
  // so no menu offers to unlock with an expired pass. True if it switched.
  function passRanOut(session) {
    if (!PASS_STEPS.has(session.step) || passes.isActive(session.pass)) return false;
    session.step = STEP.PASS_EXPIRED;
    return true;
  }

  // Repeat the question for the step the user is on.
  async function prompt(session) {
    passRanOut(session);
    switch (session.step) {
      case STEP.CHOOSE_RESTROOM:
        return [await restroomMenu(session)];
      case STEP.CONFIRM_PAY:
        return [await payMenu(session)];
      case STEP.CHOOSE_METHOD:
        return [await methodMenu()];
      case STEP.AWAIT_PHONE:
        return [phoneMenu(session)];
      case STEP.AUTHORIZE:
        return [summaryMenu(session)];
      case STEP.PAYMENT_FAILED:
        return [failedMenu(session)];
      case STEP.NAVIGATING:
        return [await journeyMenu(session)];
      case STEP.ARRIVED:
        return [arrivedMenu(session)];
      case STEP.ACCESS:
        return [menu(msg.whatNext(), accessChoices())];
      case STEP.REPORT:
        return [reportMenu()];
      case STEP.PASS_EXPIRED:
        return [expiredMenu(session)];
      default:
        return [locationMenu()];
    }
  }

  async function didNotUnderstand(session) {
    return [text(msg.didNotUnderstand()), ...(await prompt(session))];
  }

  function exit(userId) {
    sessions.end(userId);
    return [text(msg.goodbye(passes.active(userId).at(-1)))];
  }

  async function onReply(session, raw, userId) {
    const choice = parseChoice(raw);
    // The user hasn't seen that the pass ran out yet: tell them first.
    if (passRanOut(session)) return prompt(session);

    switch (session.step) {
      case STEP.AWAIT_LOCATION:
        if (choice === 2) return exit(userId);
        if (!session.location) return [locationMenu(msg.needLocation())];
        if (choice === 1) return showRestrooms(session, userId);
        return didNotUnderstand(session);

      case STEP.CHOOSE_RESTROOM:
        if (choice >= 1 && choice <= session.options.length) {
          session.restroom = session.options[choice - 1];
          session.step = STEP.CONFIRM_PAY;
          return [await payMenu(session, { pictures: true })];
        }
        return didNotUnderstand(session);

      case STEP.CONFIRM_PAY:
        if (choice === 1) {
          session.step = STEP.CHOOSE_METHOD;
          return [await methodMenu({ pictures: true })];
        }
        if (choice === 2) return exit(userId);
        if (choice === 3) {
          session.step = STEP.CHOOSE_RESTROOM;
          return [await restroomMenu(session, { pictures: true })];
        }
        return didNotUnderstand(session);

      case STEP.CHOOSE_METHOD:
        if (choice === 0) {
          session.step = STEP.CONFIRM_PAY;
          return prompt(session);
        }
        if (choice >= 1 && choice <= methods.length) {
          session.method = methods[choice - 1];
          if (!session.method.needsPhone) return preparePayment(session, null);
          session.step = STEP.AWAIT_PHONE;
          return prompt(session);
        }
        return didNotUnderstand(session);

      case STEP.AWAIT_PHONE: {
        if (choice === 0) {
          session.step = STEP.CHOOSE_METHOD;
          return prompt(session);
        }
        if (choice === 1 && session.whatsappPhone) return preparePayment(session, session.whatsappPhone);
        const phone = normalizePhone(raw, config.countryCode);
        if (!phone) return [phoneMenu(session, msg.badPhone(session.method))];
        return preparePayment(session, phone);
      }

      case STEP.AUTHORIZE:
        if (choice === 1) return authorize(session, userId);
        if (choice === 2) {
          session.step = STEP.CONFIRM_PAY;
          return prompt(session);
        }
        return didNotUnderstand(session);

      case STEP.PAYMENT_FAILED:
        if (choice === 1) return authorize(session, userId);
        if (choice === 2) {
          session.step = STEP.CHOOSE_METHOD;
          return prompt(session);
        }
        if (choice === 3) return exit(userId);
        return didNotUnderstand(session);

      // "Choose a different restroom" means paying again; the pass already
      // bought stays valid.
      case STEP.NAVIGATING:
        if (choice === 1) {
          session.step = STEP.ARRIVED;
          return prompt(session);
        }
        if (choice === 2) return askReport(session);
        if (choice === 3) return showRestrooms(session, userId);
        return didNotUnderstand(session);

      case STEP.ARRIVED:
        if (choice === 1) return showAccess(session);
        if (choice === 2) return askReport(session);
        if (choice === 3) return showRestrooms(session, userId);
        return didNotUnderstand(session);

      case STEP.ACCESS: {
        const { pass } = session;
        if (choice === 1) return [await accessQr(pass), menu(msg.whatNext(), accessChoices())];
        if (choice === 2) return [menu(msg.otp(pass), accessChoices())];
        if (choice === 3) {
          sessions.end(userId);
          return [text(msg.done(pass))];
        }
        if (choice === 4) return askReport(session);
        return didNotUnderstand(session);
      }

      case STEP.PASS_EXPIRED:
        if (choice === 1) return askReport(session);
        if (choice === 2) return showRestrooms(session, userId);
        if (choice === 3) return exit(userId);
        return didNotUnderstand(session);

      case STEP.REPORT:
        if (choice === 0 || normalizeWords(raw) === 'cancel') {
          session.step = session.returnStep;
          return prompt(session);
        }
        if (!describesProblem(raw)) return [text(msg.describeInWords()), reportMenu()];
        await saveReport(session, raw, userId);
        session.step = session.returnStep;
        return [text(msg.reportThanks()), ...(await prompt(session))];

      default:
        return startConversation({ userId });
    }
  }

  // When a pass bought at `from` ends: after the walk there and then
  // ACCESS_MINUTES (so a restroom further away can still be used), or when
  // the restroom closes, if that comes first.
  function passEnd(facility, from) {
    const passMinutes = walkingMinutes(facility.distanceKm) + config.accessMinutes;
    const full = new Date(from.getTime() + passMinutes * 60_000);
    const closes = nextClosing(facility.hours, from, config.timezone);
    return closes && closes < full ? { at: closes, atClosing: true } : { at: full, atClosing: false };
  }

  function preparePayment(session, phone) {
    const price = session.restroom.price;
    const fee = feeFor(price, session.method.feePercent);
    const end = passEnd(session.restroom, now());
    session.payment = {
      facility: session.restroom,
      method: session.method,
      phone,
      price,
      fee,
      total: Math.round((price + fee) * 100) / 100,
      // Set when closing time cuts the pass short, so the summary says so.
      closesAt: end.atClosing ? end.at : null,
    };
    session.step = STEP.AUTHORIZE;
    return [summaryMenu(session)];
  }

  async function authorize(session, userId) {
    const p = session.payment;
    // The list may be a while old: never charge for a restroom that will be
    // closed by the time the user gets there.
    if (!isOpenOnArrival(p.facility, now(), config.timezone)) {
      return [text(msg.closedOnArrival(p.facility)), ...(await showRestrooms(session, userId))];
    }
    let result;
    try {
      result = await payments.charge({
        userId,
        method: p.method.id,
        phone: p.phone,
        amount: p.total,
        currency: config.currency,
        facilityId: p.facility.id,
      });
    } catch (err) {
      console.error('[payments] charge failed:', err);
      result = { ok: false, reason: 'The payment could not be completed right now.' };
    }

    if (!result.ok) {
      session.step = STEP.PAYMENT_FAILED;
      session.failReason = result.reason;
      return [failedMenu(session)];
    }

    const paidAt = now();
    const pass = {
      reference: result.reference,
      facility: p.facility,
      method: p.method,
      phone: p.phone,
      price: p.price,
      fee: p.fee,
      total: p.total,
      otp: makeOtp(),
      paidAt,
      expiresAt: passEnd(p.facility, paidAt).at,
    };
    passes.save(userId, pass);
    session.pass = pass;
    // The chat stays open for the whole walk, however long.
    session.keepUntil = pass.expiresAt.getTime();
    await saveRecord('savePayment', {
      at: paidAt.toISOString(),
      userId,
      reference: pass.reference,
      provider: payments.name,
      method: p.method.id,
      phone: p.phone,
      facilityId: p.facility.id,
      facilityName: p.facility.name,
      price: p.price,
      fee: p.fee,
      total: p.total,
      currency: config.currency,
      expiresAt: pass.expiresAt.toISOString(),
    });

    session.step = STEP.NAVIGATING;
    const receipt = await qrImage(
      msg.receiptText(pass, formatDateTime(paidAt, config.timezone)),
      `receipt-${pass.reference}.png`,
    );
    return [
      text(msg.paymentReceived(pass.reference)),
      receipt,
      await captioned((im) => im.restroomPhoto(p.facility), msg.searchingRoute(p.facility)),
      await journeyMenu(session, { pictures: true }),
    ];
  }

  function askReport(session) {
    session.returnStep = session.step;
    session.step = STEP.REPORT;
    return [reportMenu()];
  }

  function accessQr(pass) {
    const payload = ['TAPFLASH-ACCESS', pass.reference, pass.facility.id, pass.otp, pass.expiresAt.toISOString()];
    return qrImage(payload.join('|'), `access-${pass.reference}.png`);
  }

  // The access card (which shows the details and the QR code), or the details
  // as text with a plain QR code if the card can't be drawn.
  async function accessCard(pass) {
    const qr = await accessQr(pass);
    const card = await draw((im) => im.accessCard(pass, qr.buffer));
    return card ? { image: card, text: msg.accessCardCaption(pass) } : { image: qr, text: msg.accessDetails(pass) };
  }

  async function showAccess(session) {
    session.step = STEP.ACCESS;
    const card = await accessCard(session.pass);
    return [menu(card.text, accessChoices(), { image: card.image })];
  }

  // Every pass that still works, or when the last one ran out.
  async function showPass(userId, session) {
    const active = passes.active(userId);
    const rest = session ? await prompt(session) : [];
    if (active.length === 0) {
      // The expired-pass menu already says when it ran out.
      if (session?.step === STEP.PASS_EXPIRED) return rest;
      const last = passes.latest(userId);
      return [text(last ? msg.passEnded(last) : msg.noPass()), ...rest];
    }
    const cards = await Promise.all(active.map(accessCard));
    return [...cards.map((card) => ({ ...card.image, caption: card.text })), ...rest];
  }

  function saveReport(session, message, userId) {
    return saveRecord('saveReport', {
      at: now().toISOString(),
      userId,
      facilityId: session.restroom?.id,
      facilityName: session.restroom?.name,
      step: session.returnStep,
      message,
    });
  }

  // Saving a record must never break the conversation.
  async function saveRecord(kind, entry) {
    try {
      await records[kind](entry);
    } catch (err) {
      console.error(`[records] ${kind} failed:`, err);
    }
  }

  // Conversations and passes as plain data, so they survive a restart.
  function saveState() {
    return { sessions: sessions.dump(), passes: passes.dump() };
  }
  function loadState(data) {
    sessions.load(data?.sessions);
    passes.load(data?.passes);
  }

  return { handle, saveState, loadState };
}

module.exports = { createBot, STEP, parseChoice, normalizeWords };
