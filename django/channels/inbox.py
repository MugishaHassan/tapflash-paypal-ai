"""WhatsApp messages waiting for an answer.

The webhook stores each new message as a pending IncomingMessage (its unique
id drops Meta's retries) and then answers that user's pending rows in order.
The user's Conversation row is locked while a row is answered, so two
Passenger processes never answer the same message or mix up the order.
drain_inbox (a cron job) answers anything a killed process left behind.
"""
import logging
import time
from datetime import timedelta

from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from bot.handler import handle_message
from bot.models import Conversation, IncomingMessage
from bot.sessions import ChatStore

from .cloud_api import fetch_photo, to_incoming

logger = logging.getLogger(__name__)

# Meta retries a webhook it thinks failed; anything older is not answered.
MAX_MESSAGE_AGE = timedelta(minutes=5)
# A pending row older than this is given up (the user has moved on).
MAX_PENDING_AGE = timedelta(minutes=10)


def is_recent(message, now=None):
    try:
        sent = float(message.get('timestamp') or 0)
    except (TypeError, ValueError):
        return False
    return (now or time.time()) - sent <= MAX_MESSAGE_AGE.total_seconds()


def claim(messages):
    """Store each new, recent message as pending. Returns the users who got
    new rows, in the order they first appear."""
    users = []
    for message in messages:
        message_id = str(message.get('id') or '')[:128]
        incoming = to_incoming(message)
        if not message_id or not incoming or not is_recent(message):
            continue
        try:
            with transaction.atomic():
                IncomingMessage.objects.create(wa_message_id=message_id, user_id=incoming.user_id, payload=message)
        except IntegrityError:
            continue  # already stored: a retry from Meta
        if incoming.user_id not in users:
            users.append(incoming.user_id)
    return users


def answer_user(user_id, client):
    """Answer the user's pending messages, oldest first."""
    while True:
        try:
            ChatStore.create_row(user_id)
            with transaction.atomic():
                Conversation.objects.select_for_update().get(user_id=user_id)
                row = IncomingMessage.objects.filter(
                    user_id=user_id, status=IncomingMessage.Status.PENDING
                ).order_by('id').first()
                if row is None:
                    return
                if row.received_at < timezone.now() - MAX_PENDING_AGE:
                    _finish(row, IncomingMessage.Status.FAILED, 'Too old to answer')
                    continue
                incoming = fetch_photo(to_incoming(row.payload), client)
                handle_message(incoming, lambda reply: client.send(user_id, reply))
                _finish(row, IncomingMessage.Status.DONE)
        except DatabaseError:
            logger.exception('Could not answer the messages of %s', user_id)
            return


def _finish(row, status, error=''):
    row.status = status
    row.error = error
    row.handled_at = timezone.now()
    row.save(update_fields=['status', 'error', 'handled_at'])


def pending_users():
    return list(
        IncomingMessage.objects.filter(status=IncomingMessage.Status.PENDING)
        .order_by('user_id').values_list('user_id', flat=True).distinct()
    )
