'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { createWebConnector } = require('../src/connectors/web');

// The web tester page in headless Chrome (installed with whatsapp-web.js),
// talking to the real web server with a fake bot. Skipped if Chrome can't start.

const GPS_DELAY_MS = 1000;
let browser;
let server;
let base;
const seen = [];

before(async () => {
  try {
    browser = await require('puppeteer').launch({ headless: true });
  } catch (err) {
    console.warn(`[web-page tests] skipped, Chrome could not start: ${err.message}`);
    return;
  }
  server = createWebConnector({ webPort: 0, botName: 'TapFlash' });
  const log = console.log;
  console.log = () => {};
  await server.start(async (incoming) => {
    seen.push(incoming);
    await server.send(incoming.chatId, { kind: 'text', text: 'Got it' });
  });
  console.log = log;
  base = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  await browser?.close();
  await server?.stop();
});

// A phone-sized page whose GPS answers after GPS_DELAY_MS, like a real fix.
async function openPage(t, viewport = { width: 390, height: 844 }) {
  if (!browser) return t.skip('Chrome could not start');
  seen.length = 0;
  const page = await browser.newPage();
  t.after(() => page.close());
  await page.setViewport(viewport);
  await page.setRequestInterception(true);
  page.on('request', (req) => (req.url().startsWith(base) ? req.continue() : req.abort()));
  await page.evaluateOnNewDocument((delay) => {
    Geolocation.prototype.getCurrentPosition = function (ok) {
      setTimeout(() => ok({ coords: { latitude: -1.29, longitude: 36.81, accuracy: 20 } }), delay);
    };
  }, GPS_DELAY_MS);
  // Fully loaded, so the page's scripts are listening before the test taps (it can be slow under load).
  await page.goto(base, { waitUntil: 'load' });
  await page.waitForSelector('#chips [data-loc="gps"]');
  return page;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
// Long enough for a late GPS fix to have been sent, if it was going to be.
const settle = () => sleep(GPS_DELAY_MS + 400);
const locations = () => seen.filter((m) => m.type === 'location');
async function until(check) {
  for (let i = 0; i < 100 && !check(); i++) await sleep(20);
}

test('taps while the location is being found send it only once', async (t) => {
  const page = await openPage(t);
  if (!page) return;
  await page.click('#chips [data-loc="gps"]');
  await page.click('#chips [data-loc="gps"]');
  await page.click('#chips [data-loc="sample"]');
  await settle();
  assert.equal(locations().length, 1);
  assert.deepEqual(locations()[0].location, { lat: -1.29, lng: 36.81 });
  assert.equal(await page.$$eval('.msg.me', (els) => els.length), 1, 'one location bubble');

  // Later, a new location can still be shared.
  await page.click('#attach');
  await page.click('#sheet [data-loc="sample"]');
  await until(() => locations().length === 2);
  assert.equal(locations().length, 2);
});

test('a location found after "Start as a new user" is not sent for the new user', async (t) => {
  const page = await openPage(t);
  if (!page) return;
  const userId = () => page.evaluate(() => localStorage.getItem('tapflash-user'));
  const oldUser = await userId();
  await page.click('#chips [data-loc="gps"]');
  await page.click('#more');
  await page.click('#tools [data-act="reset"]');
  await settle();
  assert.equal(locations().length, 0);
  assert.equal(await page.$eval('#chips', (el) => el.hidden), false, 'the location chips are still there');
  assert.match(await page.$eval('#chat', (el) => el.textContent), /Send hi to start\./);

  // The new user can share a location right away.
  const newUser = await userId();
  assert.notEqual(newUser, oldUser);
  await page.click('#chips [data-loc="gps"]');
  await until(() => locations().length === 1);
  assert.equal(locations().length, 1);
  assert.equal(locations()[0].userId, `web:${newUser}`);
});

test('on a desktop the chat frame fits the window without a page scrollbar', async (t) => {
  const page = await openPage(t, { width: 1280, height: 800 });
  if (!page) return;
  const fit = await page.evaluate(() => {
    window.scrollTo(0, 100);
    const app = document.querySelector('.app').getBoundingClientRect();
    const { scrollHeight } = document.documentElement;
    return { scrollHeight, scrollY: window.scrollY, top: app.top, bottom: app.bottom };
  });
  assert.equal(fit.scrollHeight, 800);
  assert.equal(fit.scrollY, 0);
  assert.equal(fit.top, 19, 'the gap above the frame is still there');
  assert.equal(fit.bottom, 800 - 19);
});
