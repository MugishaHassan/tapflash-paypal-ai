'use strict';

// Run tasks for the same key one after another, so quick double replies from
// one user are handled in order. Different keys still run in parallel.
function createUserQueue() {
  const tails = new Map();
  return function enqueue(key, task) {
    const previous = tails.get(key) || Promise.resolve();
    const current = previous.then(task);
    const tail = current.catch(() => {});
    tails.set(key, tail);
    tail.then(() => {
      if (tails.get(key) === tail) tails.delete(key);
    });
    return current;
  };
}

module.exports = { createUserQueue };
