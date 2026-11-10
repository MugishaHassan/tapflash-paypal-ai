'use strict';

// In-memory conversation state per user. A session that has been idle longer
// than the timeout is forgotten, so the next message starts fresh. A paid
// journey sets `keepUntil` (when its pass ends), and counts as active until
// then, however long the walk takes.
// (Restarting the bot also forgets everything; a database comes later.)
function createSessionStore({ timeoutMs, now }) {
  const sessions = new Map();
  // Sessions get() has just forgotten, so the bot can say the chat timed out.
  const timedOut = new Map();
  return {
    get(userId) {
      const s = sessions.get(userId);
      if (!s) return undefined;
      if (now().getTime() - Math.max(s.lastSeen, s.keepUntil || 0) > timeoutMs) {
        sessions.delete(userId);
        timedOut.set(userId, s);
        return undefined;
      }
      return s;
    },
    // The session that timed out for this user, once, or undefined. Starting
    // or ending a chat forgets it too: it no longer needs explaining.
    takeTimedOut(userId) {
      const s = timedOut.get(userId);
      timedOut.delete(userId);
      return s;
    },
    start(userId) {
      const s = { step: null, lastSeen: now().getTime() };
      sessions.set(userId, s);
      timedOut.delete(userId);
      return s;
    },
    touch(session) {
      session.lastSeen = now().getTime();
    },
    end(userId) {
      sessions.delete(userId);
      timedOut.delete(userId);
    },
    // Everything, as plain data to save, and back.
    dump() {
      return { sessions: [...sessions], timedOut: [...timedOut] };
    },
    load(data) {
      for (const [id, s] of data?.sessions || []) sessions.set(id, s);
      for (const [id, s] of data?.timedOut || []) timedOut.set(id, s);
    },
  };
}

// Paid access passes, kept separately so a pass survives "exit" or "hi". A
// user can hold several (one per restroom paid for). The newest pass is kept
// after it expires, so the bot can say when it ran out.
function createPassStore({ now }) {
  const passes = new Map();
  const isActive = (pass) => Boolean(pass) && pass.expiresAt.getTime() > now().getTime();
  return {
    isActive,
    save(userId, pass) {
      passes.set(userId, [...(passes.get(userId) || []).filter(isActive), pass]);
    },
    // Passes that still work, oldest first.
    active(userId) {
      return (passes.get(userId) || []).filter(isActive);
    },
    // The newest pass, even if it has expired.
    latest(userId) {
      return (passes.get(userId) || []).at(-1);
    },
    dump() {
      return [...passes];
    },
    load(data) {
      for (const [id, list] of data || []) passes.set(id, list);
    },
  };
}

module.exports = { createSessionStore, createPassStore };
