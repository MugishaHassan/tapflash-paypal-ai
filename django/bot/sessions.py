"""One user's chat, kept in their Conversation row.

A chat that has been idle longer than SESSION_TIMEOUT_MINUTES is over, so the
next message starts fresh. A paid journey stays open until its pass ends,
however long the walk takes. The row also remembers the last message answered,
to drop double sends.
"""
import hashlib
import logging
from datetime import timedelta

from .formatting import js_trim
from .models import Conversation
from .state import Session

logger = logging.getLogger(__name__)

# Nobody reads a new question and answers it this fast, so a copy that
# arrives this soon after the answer was sent is still a double send.
QUICKEST_ANSWER = timedelta(milliseconds=300)

Status = Conversation.Status


def message_key(incoming):
    if incoming.type == 'location':
        what = f'location:{incoming.location}'
    elif incoming.type == 'image':
        what = f'image:{hashlib.sha256(incoming.image).hexdigest()}'
    else:
        what = f'{incoming.type}:{js_trim(incoming.text)}'
    return hashlib.sha1(what.encode()).hexdigest()


class ChatStore:
    """The chat of the user whose Conversation row is given, which the caller
    has locked for the length of the turn."""

    def __init__(self, conversation, *, timeout, clock):
        self.conversation = conversation
        self.user_id = conversation.user_id
        self.timeout = timeout
        self.clock = clock
        # The chat going on, or the one that timed out (until take_timed_out).
        self._session = self._read_session()

    @staticmethod
    def create_row(user_id):
        """Make sure the user has a Conversation row. Call it outside the turn's
        transaction: on MySQL, new users' inserts inside it deadlock with the lock."""
        Conversation.objects.bulk_create([Conversation(user_id=user_id)], ignore_conflicts=True)

    @classmethod
    def lock(cls, user_id, *, timeout, clock):
        """The user's chat, with its row locked until the transaction ends."""
        conversation = Conversation.objects.select_for_update().get(user_id=user_id)
        return cls(conversation, timeout=timeout, clock=clock)

    def _read_session(self):
        if self.conversation.status == Status.IDLE:
            return None
        try:
            return Session.from_json(self.conversation.state)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            # Saved by a version of the bot that kept other things: start again.
            logger.exception('Could not read the chat of %s, so it starts again', self.user_id)
            self.conversation.status = Status.IDLE
            return None

    def get(self):
        """The chat going on, or None."""
        conversation = self.conversation
        if conversation.status != Status.ACTIVE:
            return None
        last_active = max(conversation.last_seen, conversation.keep_until or conversation.last_seen)
        if self.clock() - last_active > self.timeout:
            conversation.status = Status.TIMED_OUT
            return None
        return self._session

    def take_timed_out(self):
        """The chat that timed out, once, so the bot can say so. Starting or
        ending a chat forgets it too: it no longer needs explaining."""
        if self.conversation.status != Status.TIMED_OUT:
            return None
        old = self._session
        self.end()
        return old

    def start(self):
        self.conversation.status = Status.ACTIVE
        self.conversation.keep_until = None
        self._session = Session()
        self.touch()
        return self._session

    def touch(self):
        self.conversation.last_seen = self.clock()

    def keep_open_until(self, when):
        self.conversation.keep_until = when

    def end(self):
        self.conversation.status = Status.IDLE
        self.conversation.keep_until = None
        self._session = None

    def save(self):
        session = self._session
        self.conversation.state = session.to_json() if session else {}
        self.conversation.step = (session.step or '') if session else ''
        self.conversation.save()

    def is_repeat(self, incoming):
        """Was this message sent again before the user could have seen the answer
        to it (a double tap, or an impatient double send)? Otherwise the copy
        would answer the next question: "2" twice picks restroom 2, then Exit.
        Messages without received_at are never repeats."""
        conversation = self.conversation
        return bool(
            incoming.received_at
            and conversation.last_answered_at
            and message_key(incoming) == conversation.last_key
            and incoming.received_at < conversation.last_answered_at + QUICKEST_ANSWER
        )

    def mark_answered(self, incoming):
        """Call once the replies to this message have been sent."""
        self.conversation.last_key = message_key(incoming)
        self.conversation.last_answered_at = self.clock()
        self.conversation.save(update_fields=['last_key', 'last_answered_at'])
