'use strict';

const fs = require('fs/promises');
const path = require('path');

// Append-only JSON Lines files under records/, one line per event. Enough to
// see what happened until we add a real database.
function createRecords(dir) {
  async function append(file, entry) {
    await fs.mkdir(dir, { recursive: true });
    await fs.appendFile(path.join(dir, file), `${JSON.stringify(entry)}\n`, 'utf8');
  }
  return {
    savePayment: (entry) => append('payments.jsonl', entry),
    saveReport: (entry) => append('reports.jsonl', entry),
  };
}

module.exports = { createRecords };
