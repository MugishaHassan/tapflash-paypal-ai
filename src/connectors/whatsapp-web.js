'use strict';

const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const fs = require('fs');
const path = require('path');
const qrcodeTerminal = require('qrcode-terminal');
const { createUserQueue } = require('../util/queue');

// Free connector: drives WhatsApp Web in a hidden Chrome window, logged in by
// scanning a QR code with the bot's phone (WhatsApp → Linked devices).
// Unofficial: WhatsApp may ban numbers that behave like spam bots, so this bot
// only ever replies to people who message it first.
//
// The official Meta Cloud API connector will expose the same start()/send().

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Messages sent while the bot was off are delivered when it starts. Answering
// hours-old messages in a burst looks like spam, so skip anything this old.
const MAX_MESSAGE_AGE_SECONDS = 5 * 60;

// Only one-to-one chats: skip groups, status updates, channels and broadcasts.
function isPrivateChat(id) {
  return typeof id === 'string' && (id.endsWith('@c.us') || id.endsWith('@lid'));
}

// The login state for the password-protected link page on the server
// (/link): "starting", "qr" with the current code, or "ready". Written to a
// file so the web app, a separate process, can show it.
function createLinkStatus(file) {
  return (state, qr = '') => {
    if (!file) return;
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      const tmp = `${file}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify({ state, qr, at: Date.now() }), { mode: 0o600 });
      fs.renameSync(tmp, file);
    } catch (err) {
      console.warn('[whatsapp] could not save the login state:', err.message);
    }
  };
}

function createWhatsAppWebConnector(config) {
  const linkStatus = createLinkStatus(config.linkStatusFile);
  const client = new Client({
    authStrategy: new LocalAuth({ dataPath: config.authDir }),
    puppeteer: {
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
      // Let our own Ctrl+C handler close Chrome cleanly. Puppeteer's default
      // handler force-kills it, which can corrupt the saved WhatsApp login.
      handleSIGINT: false,
      handleSIGTERM: false,
      handleSIGHUP: false,
    },
  });
  const enqueue = createUserQueue();
  const phones = new Map();
  let stopping = false;

  // Newer WhatsApp accounts identify people by a private "LID" instead of their
  // phone number, so ask WhatsApp for the number (once per chat) when needed.
  async function phoneOf(chatId) {
    if (chatId.endsWith('@c.us')) return `+${chatId.split('@')[0]}`;
    if (phones.has(chatId)) return phones.get(chatId);
    let phone;
    try {
      const [ids] = await client.getContactLidAndPhone([chatId]);
      if (ids?.pn) phone = `+${ids.pn.split('@')[0]}`;
    } catch (err) {
      console.warn('[whatsapp] could not look up phone number:', err.message);
      return undefined;
    }
    phones.set(chatId, phone);
    return phone;
  }

  // A photo of up to 5 MB, as WhatsApp gives it (base64).
  const MAX_PHOTO_BASE64 = Math.ceil((5 * 1024 * 1024) / 3) * 4;

  async function toIncoming(msg, receivedAt) {
    const base = { userId: msg.from, chatId: msg.from, phone: await phoneOf(msg.from), receivedAt };
    if (msg.type === 'location' && msg.location) {
      return {
        ...base,
        type: 'location',
        location: { lat: Number(msg.location.latitude), lng: Number(msg.location.longitude) },
      };
    }
    if (msg.type === 'chat') return { ...base, type: 'text', text: msg.body };
    // A photo goes to the server: a cleaner's photo earns a cleaning bonus.
    if (msg.type === 'image' && msg.hasMedia) {
      try {
        const media = await msg.downloadMedia();
        if (media?.data && media.data.length <= MAX_PHOTO_BASE64) {
          return { ...base, type: 'image', image: media.data, mimetype: media.mimetype };
        }
      } catch (err) {
        console.warn('[whatsapp] could not download a photo:', err.message);
      }
    }
    return { ...base, type: 'other' };
  }

  let ready = false;

  async function start(onMessage) {
    client.on('qr', (qr) => {
      console.log('\nScan this QR code with the bot\'s phone:');
      console.log('WhatsApp → Settings (or ⋮) → Linked devices → Link a device\n');
      qrcodeTerminal.generate(qr, { small: true });
      linkStatus('qr', qr);
    });
    client.on('authenticated', () => {
      console.log('[whatsapp] logged in, loading chats…');
      linkStatus('linking');
    });
    client.on('auth_failure', (reason) => {
      console.error('[whatsapp] login failed:', reason);
      console.error('Delete the .wwebjs_auth folder and start again to get a new QR code.');
      // On the server, systemd starts the bot again, which shows a new QR code.
      linkStatus('starting');
      process.exit(1);
    });
    client.on('ready', () => {
      ready = true;
      console.log('[whatsapp] ✅ bot is ready. Send it "Hi" from another phone.');
      linkStatus('ready');
    });
    client.on('disconnected', (reason) => {
      linkStatus('starting');
      console.error('[whatsapp] disconnected:', reason);
      console.error('Run "npm start" again to reconnect.');
      process.exit(1);
    });

    client.on('message', (msg) => {
      if (msg.fromMe || msg.isStatus || msg.broadcast || !isPrivateChat(msg.from)) return;
      if (msg.timestamp && Date.now() / 1000 - msg.timestamp > MAX_MESSAGE_AGE_SECONDS) return;
      // Queue before any await (the phone lookup), so one user's messages
      // are always handled in the order they arrived.
      const receivedAt = Date.now();
      enqueue(msg.from, async () => onMessage(await toIncoming(msg, receivedAt))).catch((err) =>
        console.error('[whatsapp] failed to handle a message:', err),
      );
    });

    linkStatus('starting');
    console.log('[whatsapp] starting WhatsApp Web (first start can take a minute)…');
    await client.initialize();

    // The library doesn't notice if Chrome crashes or is closed. Exit, so the
    // problem is visible and a process manager can restart the bot.
    client.pupBrowser?.on('disconnected', () => {
      if (stopping) return;
      console.error('[whatsapp] Chrome closed unexpectedly. Run "npm start" again.');
      process.exit(1);
    });
  }

  async function send(chatId, reply) {
    // Show "typing…" and pause briefly so replies feel human and aren't sent
    // in a machine-gun burst, which also lowers the risk of being flagged.
    try {
      const chat = await client.getChatById(chatId);
      await chat.sendStateTyping();
    } catch {
      // Typing indicator is cosmetic.
    }
    await sleep(config.replyDelayMs);

    if (reply.kind === 'text') {
      await client.sendMessage(chatId, reply.text);
    } else if (reply.kind === 'image') {
      const media = reply.path
        ? MessageMedia.fromFilePath(reply.path)
        : new MessageMedia(reply.mimetype || 'image/png', reply.buffer.toString('base64'), reply.filename);
      await client.sendMessage(chatId, media, { caption: reply.caption });
    } else {
      throw new Error(`Unknown reply kind: ${reply.kind}`);
    }
  }

  async function stop() {
    stopping = true;
    if (client.pupBrowser) await client.destroy();
  }

  return { name: 'whatsapp-web', start, send, stop, isReady: () => ready };
}

module.exports = { createWhatsAppWebConnector, isPrivateChat };
