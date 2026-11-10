'use strict';

const crypto = require('crypto');

// Simulated payments: nothing is charged. Every payment succeeds, except phone
// numbers ending in 0000, which are declined so the failure path can be tried.
//
// A real provider (M-Pesa Daraja STK push, Airtel Money, card/PayPal links)
// will implement the same charge() and return the same shape.
function createMockPayments() {
  return {
    name: 'mock',
    isTestMode: true,
    async charge({ phone }) {
      if (phone && phone.endsWith('0000')) {
        return { ok: false, reason: 'The payment was declined (test numbers ending in 0000 always fail).' };
      }
      const reference = `TF${crypto.randomBytes(5).toString('hex').toUpperCase()}`;
      return { ok: true, reference };
    },
  };
}

module.exports = { createMockPayments };
