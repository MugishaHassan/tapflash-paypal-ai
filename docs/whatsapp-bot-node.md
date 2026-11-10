# TapFlash WhatsApp bot

A WhatsApp bot that helps people find the nearest clean restroom, pay for it,
get walking directions and receive an access code. It follows the flow in the
demo video, using numbered menus. There is no AI yet.

> **Version 1 is for testing.** Payments are simulated (no money moves), the
> restrooms are sample data, and it connects to WhatsApp through the free,
> unofficial WhatsApp Web route.

## What you need

- **Node.js 20.12 or newer** (`node --version`)
- **A phone with WhatsApp for the bot.** Use a spare number, not your personal
  one. WhatsApp can ban numbers that it thinks are spam bots.
- A second phone (or a friend) to chat with the bot

## Set up (once)

```bash
npm install
```

This also downloads a copy of Chrome that the bot uses in the background, so it
can take a few minutes.

Optional: copy `.env.example` to `.env` to change settings (currency, prices,
how long a pass lasts, …).

## Try it without a phone (simulator)

```bash
npm run simulate
```

Chat with the bot in the terminal. Type `hi` to start, `/loc` to share a
sample location (or `/loc -6.17,35.74` for a specific one) and `/quit` to leave.
QR codes are saved in the `out/` folder.

## Try it in your browser (web tester)

```bash
npm run web
```

Open http://localhost:3000. It looks like a WhatsApp chat and shows QR codes
inline. Use **📍 My location** (your real GPS) or **📍 Sample location**, and
**New user** to start over as someone else. Change the port with `WEB_PORT`.

On Windows PowerShell, if `npm` says "running scripts is disabled", use
`npm.cmd run web` (same for the other commands).

## Run it on WhatsApp

```bash
npm start
```

1. A QR code appears in the terminal.
2. On the **bot's phone**, open WhatsApp → **Settings** (or **⋮**) → **Linked devices** → **Link a device**, and scan it.
3. Wait for `✅ bot is ready`.
4. From **another phone**, send `Hi` to the bot's number.

The login is saved in `.wwebjs_auth/`, so you only scan once. Keep the
terminal open: the bot runs only while it's running. Press **Ctrl+C** to stop.
Keep `.wwebjs_auth/` private, because anyone with that folder can use the
bot's WhatsApp.

If the bot gets logged out, delete the `.wwebjs_auth` folder and run
`npm start` again to get a new QR code.

## Test on real WhatsApp with the bridge

The hosted TapFlash server (https://bot.example.com) can't run Chrome, so
it can't use the free QR code connection. The bridge lets you test on real
WhatsApp anyway, before Meta's official API is set up: the bot's WhatsApp is
linked on your PC, and your PC passes every message to the server and sends
the server's answers back. The conversation, payments, pictures and records
all happen on the server.

1. Put these lines in `.env` (copy `.env.example` to `.env` first if you
   don't have one):

   ```
   REMOTE_BOT_URL=https://bot.example.com
   REMOTE_BOT_TOKEN=the bridge token from the server's settings
   ```

2. Start the bridge:

   ```bash
   npm.cmd run bridge
   ```

3. A QR code appears. On the **bot's phone**, open WhatsApp, then
   **Settings** (or **⋮**), **Linked devices**, **Link a device**, and scan it.
4. From **another phone**, send `Hi` to the bot's number.

Good to know:
- The bot's phone must have a normal WhatsApp account (the WhatsApp or
  WhatsApp Business app). A number registered with Meta's Cloud API can't be
  linked with a QR code.
- Keep the PC on and the terminal open while you test.
- Choices arrive as numbered text, because WhatsApp Web can't show buttons.
- If the server can't be reached, people get "The TapFlash server isn't
  answering right now. Please try again in a minute." and the terminal says
  why (for example a wrong token).
- To chat with the server from the terminal instead of WhatsApp:
  `npm.cmd run bridge -- --console`.
- To run the whole bot on your PC again, empty `REMOTE_BOT_URL` in `.env`.

## The conversation

1. User says **Hi**. The bot greets them and asks for their location.
2. User shares their location (📎 → Location). The bot lists the 4 nearest restrooms.
3. User picks one, then chooses **Pay** and a payment method: M-Pesa, Airtel Money, Card or PayPal.
4. For mobile money, the bot asks for the number and shows a payment summary.
5. After payment: a **receipt QR code** and a **Google Maps walking directions** link.
6. User replies **I have arrived**, then **Unlock / enter**. The bot sends the
   **access code (OTP)**, a **QR code** and the expiry time.

At any time the user can send:
- **hi**: start over
- **exit**: stop
- **pass**: show the active access pass
- **help**: show these commands

To test a failed payment, use a phone number ending in `0000`.

## Restroom data

By default (`DEMO_RESTROOMS_NEAR_USER=true`), the bot invents sample restrooms
around whoever shares a location, so it can be tested anywhere.

To use real restrooms, set `DEMO_RESTROOMS_NEAR_USER=false` in `.env` and edit
`data/restrooms.json`:

```json
{
  "id": "city-mall",
  "name": "City Mall Restroom",
  "area": "Ground floor, next to the food court",
  "lat": -1.2833,
  "lng": 36.8219,
  "price": 100,
  "hours": "6:00 AM – 9:00 PM",
  "image": "city-mall.jpg"
}
```

`image` is optional: put the photo in `data/images/`. To find `lat` and `lng`,
long-press a spot in Google Maps.

## How choices look

Every question with choices (restrooms, payment methods, Pay / Exit, …) is a
*menu*. Each screen shows it as well as it can, without repeating anything:

| Where | What the user sees |
|---|---|
| Web tester | Tappable cards: restroom photos, payment icons, buttons |
| WhatsApp (free connection) | One picture of the choices with "Reply with a number", or numbered text when there is no picture |
| Terminal simulator | Numbered text |
| WhatsApp official API (later) | WhatsApp's own tap lists and buttons |

## Pictures

Like in the demo video, the bot sends pictures with its messages:

| When | Picture |
|---|---|
| After the location is shared | List of the nearest restrooms with photos |
| After a restroom is chosen | Blurred route map (sharp after payment) |
| Pay | Payment methods with icons |
| Payment received | Receipt QR code, then the restroom photo |
| Start the journey | Street map with the walking route |
| Unlock / enter | "Your access details" card with the OTP and QR code |

- **Restroom photos:** put them in `data/images/` and name them in `data/restrooms.json`
  (`"image": "city-mall.jpg"`). Without a photo, a "Photo coming soon" picture is used.
- **Payment logos:** the icons are simple generic badges. To use the official
  logos (with the provider's permission), save them as `data/images/payments/mpesa.png`,
  `airtel.png`, `card.png` and `paypal.png`.
- **Maps** use OpenStreetMap map tiles and its free walking-route service, so they
  need internet. They are saved in `.cache/` and reused. Without internet the bot
  draws a simple map, or sends the text only. `MAP_IMAGES=false` turns maps off;
  `IMAGES=false` turns all pictures off. OpenStreetMap's free servers are fine
  for testing, but a busy live bot should use a paid map provider.

## Records

Payments and problem reports are saved as one JSON line each in
`records/payments.jsonl` and `records/reports.jsonl`.

## Tests

```bash
npm test
```

## Project layout

```
src/
  index.js                  starts the bot and wires everything together
  config.js                 settings (from .env)
  bot/engine.js             the conversation, step by step (no WhatsApp code)
  bot/messages.js           every message the bot sends
  bot/sessions.js           who is at which step, and paid passes
  bot/remote.js             the bridge: passes messages to a remote TapFlash server
  connectors/whatsapp-web.js  free WhatsApp Web connection (QR login)
  connectors/console.js     terminal simulator
  payments/mock.js          simulated payments
  services/                 distances, maps links, QR codes, phone numbers, records
data/restrooms.json         restroom list (used when demo mode is off)
test/                       automated tests
```

## Hosting on your domain (official WhatsApp Cloud API)

Point the Cloud API webhook at your own server (startup file `app.js`, webhook path `/webhook`) and set the variables in `.env.example`. See Meta's Cloud API documentation for the app and the verify token.

## About the Cloud API connector

The conversation (`src/bot/`) doesn't know about WhatsApp. Moving to Meta's
official API means adding a `connectors/cloud-api.js` with the same `start()`
and `send()`, setting `CONNECTOR=cloud-api`, and hosting the bot online so Meta
can deliver messages to it. Real payments work the same way: add a provider
next to `payments/mock.js` (for example M-Pesa Daraja STK push) with the same
`charge()`.

## Known limits of version 1

- Chats are kept in memory: restarting the bot forgets where people were
  (paid passes too). A database comes later.
- WhatsApp Web can break when WhatsApp updates. Run `npm update whatsapp-web.js`
  if it stops working.
- WhatsApp Web can't send tappable buttons, so options are numbered.
- The access QR code isn't connected to any door lock yet.
