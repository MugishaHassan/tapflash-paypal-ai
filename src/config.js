'use strict';

const fs = require('fs');
const path = require('path');
const { parseEnv } = require('util');

const ROOT = path.resolve(__dirname, '..');

// Windows editors sometimes save files with an invisible BOM at the start.
function readTextFile(file) {
  return fs.readFileSync(file, 'utf8').replace(/^﻿/, '');
}

// Load .env (if present) into process.env. Values already set in the real
// environment win, so you can override anything from the command line.
function loadEnvFile(file = path.join(ROOT, '.env')) {
  if (!fs.existsSync(file)) return;
  for (const [key, value] of Object.entries(parseEnv(readTextFile(file)))) {
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

function readTimezone(env, name, fallback) {
  const timezone = readString(env, name, fallback);
  try {
    new Intl.DateTimeFormat('en-GB', { timeZone: timezone });
  } catch {
    throw new Error(`${name} "${timezone}" is not a valid time zone (example: Africa/Nairobi)`);
  }
  return timezone;
}

function readString(env, name, fallback) {
  const raw = env[name];
  return raw === undefined || raw.trim() === '' ? fallback : raw.trim();
}

function readNumber(env, name, fallback) {
  const raw = env[name];
  if (raw === undefined || raw.trim() === '') return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new Error(`${name} must be a number, got "${raw}"`);
  return value;
}

// The remote TapFlash server for the bridge, without a trailing slash. Its
// token travels with every message, so plain http is only allowed on this PC.
function readServerUrl(env, name) {
  const raw = readString(env, name, '');
  if (!raw) return '';
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`${name} is not a web address (example: https://bot.example.com)`);
  }
  // The address is never repeated in these messages, since it may hold a password.
  if (url.username || url.password || url.search || url.hash) {
    throw new Error(`${name} must be just the server address, without a password, ? or # (example: https://bot.example.com)`);
  }
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local)) {
    throw new Error(`${name} must start with https:// (plain http only works for localhost)`);
  }
  return `${url.origin}${url.pathname.replace(/\/+$/, '')}`;
}

function readPositive(env, name, fallback) {
  const value = readNumber(env, name, fallback);
  if (value <= 0) throw new Error(`${name} must be more than 0, got "${env[name]}"`);
  return value;
}

// Milliseconds for a timer: a whole number Node accepts (up to about 24 days).
function readTimeoutMs(env, name, fallback) {
  const value = readPositive(env, name, fallback);
  if (!Number.isInteger(value) || value > 2_147_483_647) {
    throw new Error(`${name} must be a whole number of milliseconds up to 2147483647, got "${env[name]}"`);
  }
  return value;
}

function readBool(env, name, fallback) {
  const raw = env[name];
  if (raw === undefined || raw.trim() === '') return fallback;
  return ['1', 'true', 'yes', 'on'].includes(raw.trim().toLowerCase());
}

// Build the config from an env object. Pure, so tests can pass their own values.
function buildConfig(env = {}) {
  const countryCode = readString(env, 'COUNTRY_CODE', '254').replace(/^\+/, '');
  if (!/^\d{1,3}$/.test(countryCode)) throw new Error(`COUNTRY_CODE must be 1-3 digits, got "${countryCode}"`);

  return {
    root: ROOT,
    botName: readString(env, 'BOT_NAME', 'TapFlash'),
    connector: readString(env, 'CONNECTOR', 'whatsapp-web'),
    currency: readString(env, 'CURRENCY', 'KES'),
    countryCode,
    timezone: readTimezone(env, 'TIMEZONE', 'Africa/Nairobi'),

    defaultPrice: readNumber(env, 'DEFAULT_PRICE', 100),
    cardFeePercent: readNumber(env, 'CARD_FEE_PERCENT', 1),

    maxResults: readNumber(env, 'MAX_RESULTS', 4),
    maxDistanceKm: readNumber(env, 'MAX_DISTANCE_KM', 20),
    // While we have no real restroom data, invent sample restrooms around
    // whoever shares their location, so the bot can be tried from anywhere.
    demoRestroomsNearUser: readBool(env, 'DEMO_RESTROOMS_NEAR_USER', true),

    accessMinutes: readNumber(env, 'ACCESS_MINUTES', 30),
    sessionTimeoutMinutes: readNumber(env, 'SESSION_TIMEOUT_MINUTES', 30),
    replyDelayMs: readNumber(env, 'REPLY_DELAY_MS', 800),
    webPort: readNumber(env, 'WEB_PORT', 3000),
    // On Meta's API the server's port comes from the host (cPanel sets PORT).
    port: readString(env, 'PORT', '3000'),

    // Meta WhatsApp Cloud API (CONNECTOR=cloud-api). Secrets come only from
    // .env or the host's environment settings, never from the code.
    whatsappToken: readString(env, 'WHATSAPP_TOKEN', ''),
    whatsappPhoneNumberId: readString(env, 'WHATSAPP_PHONE_NUMBER_ID', ''),
    whatsappVerifyToken: readString(env, 'WHATSAPP_VERIFY_TOKEN', ''),
    whatsappAppSecret: readString(env, 'WHATSAPP_APP_SECRET', ''),
    graphApiVersion: readString(env, 'GRAPH_API_VERSION', 'v23.0'),
    webhookPath: readString(env, 'WEBHOOK_PATH', '/webhook'),

    // Bridge (npm run bridge): WhatsApp Web runs on this PC, the conversation
    // on a remote TapFlash server. Setting REMOTE_BOT_URL turns it on.
    remoteBotUrl: readServerUrl(env, 'REMOTE_BOT_URL'),
    remoteBotToken: readString(env, 'REMOTE_BOT_TOKEN', ''),
    remoteBotTimeoutMs: readTimeoutMs(env, 'REMOTE_BOT_TIMEOUT_MS', 30000),

    // Keep chats and passes in a file so restarts don't lose them.
    saveState: readBool(env, 'SAVE_STATE', true),

    // Pictures like in the demo video. Maps need internet (OpenStreetMap).
    images: readBool(env, 'IMAGES', true),
    mapImages: readBool(env, 'MAP_IMAGES', true),

    restroomsFile: path.join(ROOT, 'data', 'restrooms.json'),
    imagesDir: path.join(ROOT, 'data', 'images'),
    recordsDir: path.join(ROOT, 'records'),
    stateFile: path.join(ROOT, 'records', 'state.json'),
    // WhatsApp login state for the server's /link page.
    linkStatusFile: path.join(ROOT, 'records', 'link.json'),
    outDir: path.join(ROOT, 'out'),
    authDir: path.join(ROOT, '.wwebjs_auth'),
  };
}

module.exports = { ROOT, readTextFile, loadEnvFile, buildConfig };
