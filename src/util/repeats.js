'use strict';

// Spots a message sent again before the user could have seen the bot's answer
// to it (a double tap, or an impatient double send), so it is answered only
// once. Otherwise the copy answers the next question: "2" twice picks
// restroom 2, then Exit. Connectors stamp `receivedAt` (ms) when a message
// arrives; messages without it are never treated as repeats.

// Nobody reads a new question and answers it this fast, so a copy that
// arrives this soon after the answer was sent is still a double send.
const QUICKEST_ANSWER_MS = 300;

function createRepeatFilter({ now = () => Date.now() } = {}) {
  const last = new Map();
  const keyOf = (incoming) =>
    incoming.type === 'location'
      ? `location:${incoming.location?.lat},${incoming.location?.lng}`
      : `${incoming.type}:${String(incoming.text ?? '').trim()}`;
  return {
    isRepeat(incoming) {
      const previous = last.get(incoming.userId);
      return Boolean(
        previous &&
          incoming.receivedAt &&
          keyOf(incoming) === previous.key &&
          incoming.receivedAt < previous.answeredAt + QUICKEST_ANSWER_MS,
      );
    },
    // Call once the replies to this message have been sent.
    answered(incoming) {
      last.set(incoming.userId, { key: keyOf(incoming), answeredAt: now() });
    },
  };
}

module.exports = { createRepeatFilter, QUICKEST_ANSWER_MS };
