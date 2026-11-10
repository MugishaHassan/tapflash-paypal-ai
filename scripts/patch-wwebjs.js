'use strict';

// whatsapp-web.js 1.34.7 can't send pictures on WhatsApp Web builds since
// 17 Sep 2026: the media model carries a private "__x_id" that overwrites the
// new message's id ("Data passed to getter must include an id property").
// This drops it, as the upstream fix does (wwebjs/whatsapp-web.js#201923).
// Runs after every npm install; safe to run again. Remove once a fixed
// whatsapp-web.js is released.
const fs = require('fs');
const path = require('path');

const file = path.join(__dirname, '..', 'node_modules', 'whatsapp-web.js', 'src', 'util', 'Injected', 'Utils.js');
const before = `            ...mediaOptions,
            ...(mediaOptions.toJSON ? mediaOptions.toJSON() : {}),`;
const after = `            ...withoutXId(mediaOptions),
            ...withoutXId(mediaOptions.toJSON ? mediaOptions.toJSON() : {}),`;
const helper = `        // Patched by TapFlash (scripts/patch-wwebjs.js).
        const withoutXId = (media) => {
            const { __x_id, ...rest } = media;
            return rest;
        };
        const message = {
            ...options,
            id: newMsgKey,`;

if (!fs.existsSync(file)) process.exit(0);
let source = fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
if (source.includes('withoutXId')) process.exit(0);
const anchor = `        const message = {
            ...options,
            id: newMsgKey,`;
if (!source.includes(before) || !source.includes(anchor)) {
  console.warn('[patch-wwebjs] whatsapp-web.js changed; picture fix not applied');
  process.exit(0);
}
source = source.replace(anchor, helper).replace(before, after);
fs.writeFileSync(file, source);
console.log('[patch-wwebjs] picture sending fix applied');
