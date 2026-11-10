'use strict';

const fs = require('fs');
const path = require('path');

// Conversations and passes saved to one JSON file, so a restart (shared
// hosting restarts idle apps) doesn't forget them. Fine for a small bot; a
// database is the next step when traffic grows.

const ISO_DATE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;

// JSON has no Map or Set, so save them tagged...
function replace(key, value) {
  if (value instanceof Map) return { $map: [...value] };
  if (value instanceof Set) return { $set: [...value] };
  return value;
}

// ...and turn them, and dates (saved as ISO strings), back on reading.
function revive(key, value) {
  if (typeof value === 'string' && ISO_DATE.test(value)) return new Date(value);
  if (value && typeof value === 'object' && Array.isArray(value.$map)) return new Map(value.$map);
  if (value && typeof value === 'object' && Array.isArray(value.$set)) return new Set(value.$set);
  return value;
}

function createStateFile(file) {
  return {
    read() {
      if (!fs.existsSync(file)) return null;
      try {
        return JSON.parse(fs.readFileSync(file, 'utf8'), revive);
      } catch (err) {
        console.error(`[state] could not read ${file}, starting empty:`, err.message);
        return null;
      }
    },
    // Write to a temporary file first, so a crash mid-write can't leave a
    // half-written file behind.
    async write(data) {
      await fs.promises.mkdir(path.dirname(file), { recursive: true });
      const tmp = `${file}.tmp`;
      await fs.promises.writeFile(tmp, JSON.stringify(data, replace), 'utf8');
      await fs.promises.rename(tmp, file);
    },
  };
}

module.exports = { createStateFile, replace, revive };
