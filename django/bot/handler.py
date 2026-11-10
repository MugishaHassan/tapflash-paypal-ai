"""The one way every channel answers a message.

Each user's turn runs with their Conversation row locked, from reading the
chat to sending the last reply, so two messages from the same user (on any
Passenger process) are answered one after the other, in order.
"""
import functools
import logging
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, transaction

from pictures.service import build_pictures

from .engine import Engine
from .replies import Text
from .sessions import ChatStore

logger = logging.getLogger(__name__)

APOLOGY = 'Sorry, something went wrong on our side. Please try again, or send *Hi* to start over.'


@functools.cache
def default_engine():
    config = settings.TAPFLASH
    return Engine(config, pictures=build_pictures(config, settings.MEDIA_ROOT, settings.CACHE_DIR))


def handle_message(incoming, send, engine=None):
    """Answer `incoming` (an engine.Incoming), calling send(reply) for each reply.

    A copy of the message just answered (a double send) gets no answer. If
    the engine fails, nothing it did is kept and the user gets an apology.
    """
    engine = engine or default_engine()
    timeout = timedelta(minutes=engine.config.session_timeout_minutes)
    try:
        ChatStore.create_row(incoming.user_id)
        with transaction.atomic():
            _answer(incoming, send, engine, timeout)
    except DatabaseError:
        # The lock or the final save failed (a busy database): the turn is lost.
        logger.exception('Could not answer %s', incoming.user_id)
        _apologise(send)


def _answer(incoming, send, engine, timeout):
    store = ChatStore.lock(incoming.user_id, timeout=timeout, clock=engine.clock)
    if store.is_repeat(incoming):
        return
    try:
        with transaction.atomic():
            replies = engine.handle(incoming, store)
            store.save()
        for reply in replies:
            send(reply)
    except Exception:
        logger.exception('Could not answer %s', incoming.user_id)
        _apologise(send)
    finally:
        store.mark_answered(incoming)


def handle_event(user_id, act, engine=None):
    """Run act(engine, store) on a user's chat with their row locked, like a
    message turn, for things that happen outside the chat (PayPal confirming
    a payment). Returns act's replies (None if it had none), with the chat
    saved, or None if anything failed: then nothing it did is kept."""
    engine = engine or default_engine()
    timeout = timedelta(minutes=engine.config.session_timeout_minutes)
    try:
        ChatStore.create_row(user_id)
        with transaction.atomic():
            store = ChatStore.lock(user_id, timeout=timeout, clock=engine.clock)
            replies = act(engine, store)
            store.save()
            return replies
    except Exception:
        logger.exception('Could not run an event for %s', user_id)
        return None


def _apologise(send):
    try:
        send(Text(APOLOGY))
    except Exception:
        pass  # The channel is down: nothing more can be done.
