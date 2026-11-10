from django.db import models
from django.utils import timezone

from restrooms.models import Restroom


class Conversation(models.Model):
    """One user's chat: the step they are on and what they have chosen so far."""

    class Status(models.TextChoices):
        IDLE = 'idle', 'No chat going on'
        ACTIVE = 'active', 'Active'
        TIMED_OUT = 'timed_out', 'Timed out'

    # A WhatsApp number, or "web:" and up to 64 characters from the test chat.
    user_id = models.CharField('user ID', max_length=100, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.IDLE)
    step = models.CharField(max_length=30, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    keep_until = models.DateTimeField(
        null=True, blank=True, help_text='A paid journey stays open until its pass ends.'
    )
    last_key = models.CharField(
        max_length=40, blank=True, help_text='SHA-1 of the last message answered, to spot double sends.'
    )
    last_answered_at = models.DateTimeField(null=True, blank=True)
    state = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.user_id


class IncomingMessage(models.Model):
    """A message from WhatsApp. Its unique id drops Meta's retries, the pending
    rows are each user's queue, and the rest is a log."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        DONE = 'done', 'Answered'
        FAILED = 'failed', 'Failed'

    wa_message_id = models.CharField('WhatsApp message ID', max_length=128, unique=True)
    user_id = models.CharField('user ID', max_length=100)
    payload = models.JSONField()
    received_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True)
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'received_at']),
            models.Index(fields=['user_id', 'id']),
        ]

    def __str__(self):
        return self.wa_message_id


class ProblemReport(models.Model):
    class Status(models.TextChoices):
        NEW = 'new', 'New'
        LOOKING = 'looking', 'Looking into it'
        RESOLVED = 'resolved', 'Resolved'

    user_id = models.CharField('user ID', max_length=100)
    phone = models.CharField('WhatsApp number', max_length=20, blank=True)
    # Copied like on payments: demo restrooms have no row.
    restroom = models.ForeignKey(Restroom, on_delete=models.SET_NULL, null=True, blank=True, related_name='reports')
    facility_code = models.CharField('restroom code', max_length=64, blank=True)
    facility_name = models.CharField('restroom name', max_length=120, blank=True)
    step = models.CharField(
        'reported while', max_length=30, blank=True, help_text='The step of the chat the user was on.'
    )
    message = models.TextField()
    # The paid visit the report is about, when the user had a pass.
    payment = models.ForeignKey(
        'payments.Payment', on_delete=models.SET_NULL, null=True, blank=True, related_name='reports'
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW)
    staff_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'Report {self.pk}: {self.facility_name or "no restroom"}'

    def save(self, *args, **kwargs):
        # resolved_at follows the status, however the status was changed.
        if self.status == self.Status.RESOLVED:
            self.resolved_at = self.resolved_at or timezone.now()
        else:
            self.resolved_at = None
        super().save(*args, **kwargs)


class OutgoingReply(models.Model):
    """Replies the bot sends without being asked, such as the receipt after
    the user pays on PayPal's page. The web chat and the WhatsApp Web bridge
    collect them from /api/outbox; Cloud API chats get them at once."""

    user_id = models.CharField('user ID', max_length=100)
    channel = models.CharField(max_length=10, default='web')
    # The replies in the web chat's JSON shape (bot.web_replies).
    replies = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = 'outgoing replies'
        indexes = [models.Index(fields=['channel', 'sent_at', 'id']), models.Index(fields=['user_id', 'sent_at'])]

    def __str__(self):
        return f'Replies for {self.user_id}'
