'use strict';

const { loadEnvFile, buildConfig } = require('./config');
const { createBot } = require('./bot/engine');
const { createRemoteBot } = require('./bot/remote');
const { createRestroomService } = require('./services/restrooms');
const { createRecords } = require('./services/records');
const { createMockPayments } = require('./payments/mock');
const { createUserQueue } = require('./util/queue');
const { createRepeatFilter } = require('./util/repeats');
const { createStateFile } = require('./services/state-file');
const { flattenMenus } = require('./bot/replies');

// Connectors are loaded only when chosen, so the simulator doesn't start Chrome.
const CONNECTORS = {
  'whatsapp-web': () => require('./connectors/whatsapp-web').createWhatsAppWebConnector,
  console: () => require('./connectors/console').createConsoleConnector,
  web: () => require('./connectors/web').createWebConnector,
  'cloud-api': () => require('./connectors/cloud-api').createCloudApiConnector,
};

async function main() {
  loadEnvFile();
  const config = buildConfig(process.env);
  // Bridge: the conversation runs on a remote TapFlash server, and this PC
  // only connects WhatsApp Web to it (the free QR code login). Setting
  // REMOTE_BOT_URL is enough; --bridge also picks WhatsApp Web whatever
  // CONNECTOR says.
  const bridge = process.argv.includes('--bridge') || Boolean(config.remoteBotUrl);
  if (process.argv.includes('--bridge')) config.connector = 'whatsapp-web';
  if (process.argv.includes('--console')) config.connector = 'console';
  if (process.argv.includes('--web')) config.connector = 'web';

  const makeConnector = CONNECTORS[config.connector];
  if (!makeConnector) {
    throw new Error(`Unknown CONNECTOR "${config.connector}". Use one of: ${Object.keys(CONNECTORS).join(', ')}`);
  }
  if (bridge && !config.remoteBotUrl) {
    throw new Error('Set REMOTE_BOT_URL in .env to use the bridge (example: REMOTE_BOT_URL=https://bot.example.com)');
  }
  const connector = makeConnector()(config);

  let bot;
  let state = null;
  if (bridge) {
    // Pictures, payments, records and saved chats are the server's job.
    bot = createRemoteBot({
      url: config.remoteBotUrl,
      token: config.remoteBotToken,
      timeoutMs: config.remoteBotTimeoutMs,
    });
    console.log(`[bridge] the conversation runs on ${config.remoteBotUrl}`);
    if (!config.remoteBotToken) console.log('[bridge] no REMOTE_BOT_TOKEN set, so messages are sent without a token');
  } else {
    bot = createBot({
      config,
      restrooms: createRestroomService(config),
      payments: createMockPayments(),
      records: createRecords(config.recordsDir),
      images: loadImages(config),
    });
    state = config.saveState ? createStateFile(config.stateFile) : null;
    if (state) bot.loadState(state.read());
  }

  // Registered before start(), so Ctrl+C during startup also closes Chrome.
  let shuttingDown = false;
  const shutdown = async () => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log('\nShutting down…');
    try {
      await connector.stop();
    } catch (err) {
      console.error('[bot] error while shutting down:', err.message);
    } finally {
      process.exit(0);
    }
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  const handler = createMessageHandler({ bot, connector, state });
  await connector.start(handler);
  if (bridge) startOutbox({ bot, connector, handler, everyMs: 3000 });
}

// The bridge: replies the server sends on its own (the receipt after a PayPal
// payment) are collected every few seconds and sent in order with the
// user's other replies.
function startOutbox({ bot, connector, handler, everyMs }) {
  let busy = false;
  const timer = setInterval(async () => {
    if (busy || (connector.isReady && !connector.isReady())) return;
    busy = true;
    try {
      for (const { userId, replies } of await bot.collect()) handler.push(userId, replies);
    } catch (err) {
      console.error('[bridge] could not collect replies:', err.message);
    } finally {
      busy = false;
    }
  }, everyMs);
  timer.unref?.();
  return timer;
}

// Pictures need the sharp image library, which some servers can't load (for
// example an older CPU). Then the bot runs without pictures instead of
// failing to start.
function loadImages(config) {
  if (!config.images) return undefined;
  try {
    return require('./services/images').createImages(config);
  } catch (err) {
    console.warn(`[images] pictures are off, the image library could not load: ${err.message.split('\n')[0]}`);
    return undefined;
  }
}

// Answer each incoming message: one user's messages in order, replies sent
// the way the connector can show them.
function createMessageHandler({ bot, connector, state }) {
  const enqueue = createUserQueue();
  const repeats = createRepeatFilter();
  // Saves run one after another, each writing the latest state.
  let saving = Promise.resolve();
  const save = () => {
    if (!state) return;
    saving = saving
      .then(() => state.write(bot.saveState()))
      .catch((err) => console.error('[state] could not save:', err.message));
  };
  const handle = (incoming) =>
    enqueue(incoming.userId, async () => {
      if (repeats.isRepeat(incoming)) return;
      try {
        // Connectors that can't show tappable choices get pictures and
        // numbered text instead of menus.
        const replies = await bot.handle(incoming);
        save();
        const toSend = connector.supportsMenus ? replies : flattenMenus(replies);
        for (const reply of toSend) await connector.send(incoming.chatId, reply);
      } catch (err) {
        console.error(`[bot] error while replying to ${incoming.userId}:`, err);
        try {
          await connector.send(incoming.chatId, {
            kind: 'text',
            text: 'Sorry, something went wrong on our side. Please try again, or send *Hi* to start over.',
          });
        } catch {
          // Nothing more we can do.
        }
      } finally {
        repeats.answered(incoming);
      }
    });
  // Replies nobody asked for, queued behind the user's own messages.
  handle.push = (chatId, replies) =>
    enqueue(chatId, async () => {
      const toSend = connector.supportsMenus ? replies : flattenMenus(replies);
      for (const reply of toSend) {
        try {
          await connector.send(chatId, reply);
        } catch (err) {
          console.error(`[bot] could not send a reply to ${chatId}:`, err.message);
        }
      }
    });
  return handle;
}

module.exports = { main, createMessageHandler, startOutbox };

if (require.main === module) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
