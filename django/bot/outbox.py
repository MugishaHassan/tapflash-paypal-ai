"""Replies the bot sends on its own, outside a turn the user started.

When PayPal confirms a payment, the user is on PayPal's page, not in the
chat. The receipt, route and access code are kept here until the chat
picks them up: the web chat polls /api/outbox for its user, and the WhatsApp
Web bridge polls it for all of its users. Cloud API chats are sent at once.
"""
import logging

from django.db import transaction
from django.utils import timezone

from .models import OutgoingReply

logger = logging.getLogger(__name__)

WEB_PREFIX = 'web:'
MAX_BATCH = 20


def push(user_id, channel, replies, *, cloud_client=None):
    """Send `replies` (engine replies) to a chat that didn't ask for them."""
    if not replies:
        return
    if channel == 'cloud' and cloud_client is not None:
        for reply in replies:
            try:
                cloud_client.send(user_id, reply)
            except Exception:
                logger.exception('Could not push a reply to %s', user_id)
        return
    from channels.web_chat import to_page_reply  # the page's JSON shape

    page_replies = [page for page in map(to_page_reply, replies) if page]
    OutgoingReply.objects.create(user_id=user_id, channel=channel, replies=page_replies)


def notify(user_id, replies):
    """Send `replies` to a user on the channel their chat is on (staff
    refunds, cleaning bonuses). The WhatsApp bridge also uses "web:" ids, so the
    channel comes from the saved chat, not from the id."""
    from .models import Conversation

    state = Conversation.objects.filter(user_id=user_id).values_list('state', flat=True).first() or {}
    channel = state.get('channel') or 'web'
    client = None
    if channel == 'cloud':
        from django.conf import settings

        from channels.cloud_api import GraphClient
        client = GraphClient(settings.TAPFLASH)
    push(user_id, channel, replies, cloud_client=client)


def collect(*, channel, user_id=None):
    """The replies waiting for `channel` (and for one user, if given), oldest
    first, marked as sent. Returns [(user_id, page_replies)]."""
    with transaction.atomic():
        rows = OutgoingReply.objects.select_for_update().filter(channel=channel, sent_at__isnull=True)
        if user_id is not None:
            rows = rows.filter(user_id=user_id)
        rows = list(rows.order_by('id')[:MAX_BATCH])
        OutgoingReply.objects.filter(pk__in=[row.pk for row in rows]).update(sent_at=timezone.now())
    return [(row.user_id, row.replies) for row in rows]


def channel_user_id(user_id):
    """The id the channel knows: 'web:2547...@c.us' is '2547...@c.us' to the bridge."""
    return user_id.removeprefix(WEB_PREFIX)
