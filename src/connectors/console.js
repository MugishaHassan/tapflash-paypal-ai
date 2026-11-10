'use strict';

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { menuText } = require('../bot/replies');

// Chat with the bot in the terminal, no phone needed. Type messages as if you
// were on WhatsApp. Images (QR codes) are saved to the out/ folder.
//
//   /loc              share a sample location (Nairobi CBD)
//   /loc -1.29,36.82  share a specific location
//   /quit             leave

const SAMPLE_LOCATION = { lat: -1.285, lng: 36.82 }; // Kenyatta Avenue, Nairobi CBD

function parseLocation(arg) {
  if (!arg) return SAMPLE_LOCATION;
  const [lat, lng] = arg.split(',').map((s) => Number(s.trim()));
  return { lat, lng };
}

function createConsoleConnector(config) {
  const userId = 'console-user';
  const phone = `+${config.countryCode}712345678`;
  let imageCount = 0;

  async function start(onMessage) {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const interactive = process.stdin.isTTY;
    console.log(`\n💬 ${config.botName} simulator. You are ${phone}.`);
    console.log('   Type "hi" to begin, "/loc" to share a location, "/quit" to leave.\n');

    // Handle lines one at a time, in order, even when input is piped in.
    let queue = Promise.resolve();
    rl.on('line', (line) => {
      queue = queue.then(async () => {
        const input = line.trim();
        if (!interactive) console.log(`🧑 You: ${input}`);
        if (input === '/quit') return rl.close();
        const incoming = input.startsWith('/loc')
          ? { userId, chatId: userId, phone, type: 'location', location: parseLocation(input.slice(4).trim()) }
          : { userId, chatId: userId, phone, type: 'text', text: input };
        await onMessage(incoming);
        if (interactive) rl.prompt();
      });
    });
    rl.on('SIGINT', () => rl.close());
    rl.on('close', () => queue.then(() => process.exit(0)));
    rl.setPrompt('🧑 You: ');
    if (interactive) rl.prompt();
  }

  async function send(chatId, reply) {
    if (reply.kind === 'menu') {
      if (reply.image) await send(chatId, reply.image);
      console.log(`
🤖 ${config.botName}:
${menuText(reply)}
`);
      return;
    }
    if (reply.kind === 'text') {
      console.log(`\n🤖 ${config.botName}:\n${reply.text}\n`);
      return;
    }
    let where = reply.path;
    if (!where) {
      fs.mkdirSync(config.outDir, { recursive: true });
      imageCount += 1;
      where = path.join(config.outDir, reply.filename || `image-${imageCount}.png`);
      fs.writeFileSync(where, reply.buffer);
    }
    console.log(`\n🤖 ${config.botName}: [image: ${path.relative(config.root, where)}]`);
    if (reply.caption) console.log(reply.caption);
    console.log('');
  }

  return { name: 'console', supportsMenus: true, start, send, stop: async () => {} };
}

module.exports = { createConsoleConnector, parseLocation };
