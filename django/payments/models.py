from django.db import models
from django.utils import timezone

from restrooms.models import Restroom

from .methods import Method


class Payment(models.Model):
    """Every payment attempt, paid or failed."""

    class Status(models.TextChoices):
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'
        REFUNDED = 'refunded', 'Refunded'

    reference = models.CharField(
        max_length=40, unique=True, null=True, blank=True,
        help_text="The provider's reference. Failed payments have none.",
    )
    user_id = models.CharField('user ID', max_length=100)
    phone = models.CharField(
        'paid from', max_length=20, blank=True, help_text='The mobile money number. Empty for card and PayPal.'
    )
    method = models.CharField(max_length=20, choices=Method.choices)
    provider = models.CharField(max_length=20, help_text='"mock" while payments are simulated.')
    # The restroom's code and name are copied, so the record stays complete
    # for demo restrooms (which have no row) and after a restroom is renamed or deleted.
    restroom = models.ForeignKey(Restroom, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')
    facility_code = models.CharField('restroom code', max_length=64)
    facility_name = models.CharField('restroom name', max_length=120)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    fee = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8)
    status = models.CharField(max_length=10, choices=Status.choices)
    failure_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    # The operator payout that paid the operator's share of this visit (empty: not paid out yet).
    payout = models.ForeignKey('Payout', on_delete=models.SET_NULL, null=True, blank=True, related_name='visits_paid')

    class Meta:
        indexes = [models.Index(fields=['user_id', 'created_at'])]

    def __str__(self):
        return self.reference or f'Failed payment {self.pk}'


class AccessPassQuerySet(models.QuerySet):
    def active(self, at=None):
        """Passes that still work at `at` (default: now)."""
        return self.filter(expires_at__gt=at or timezone.now())


class AccessPass(models.Model):
    """What a successful payment buys: an access code that works until expires_at.
    The restroom, method and amounts are on the payment."""

    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name='access_pass')
    # Copied from the payment, so a user's passes are found with one index.
    user_id = models.CharField('user ID', max_length=100)
    otp = models.CharField('access code', max_length=6)
    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    # Set when the attendant scans the QR code or types the code at the door (ops.gate).
    # A pass lets one person in once.
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.CharField(max_length=150, blank=True)

    objects = AccessPassQuerySet.as_manager()

    class Meta:
        verbose_name_plural = 'access passes'
        indexes = [models.Index(fields=['user_id', 'expires_at'])]

    def __str__(self):
        return f'Access pass for {self.payment}'

    def is_active(self, at=None):
        return self.expires_at > (at or timezone.now())


class PayPalOrder(models.Model):
    """A PayPal checkout sent to a user: created, approved by the buyer on
    PayPal's page, then captured (the money is taken). The Payment and its
    access pass exist only once it is captured."""

    class Status(models.TextChoices):
        CREATED = 'created', 'Waiting for the buyer'
        COMPLETED = 'completed', 'Paid'
        CANCELLED = 'cancelled', 'Cancelled'
        FAILED = 'failed', 'Failed'

    order_id = models.CharField('PayPal order ID', max_length=40, unique=True)
    user_id = models.CharField('user ID', max_length=100)
    channel = models.CharField(max_length=10, default='web', help_text='Where the chat is: web, bridge or cloud.')
    facility_code = models.CharField('restroom code', max_length=64)
    facility_name = models.CharField('restroom name', max_length=120)
    local_total = models.DecimalField(max_digits=10, decimal_places=2, help_text='The total in the local currency.')
    local_currency = models.CharField(max_length=8)
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text='What PayPal charges.')
    currency = models.CharField(max_length=3, default='USD')
    approve_url = models.URLField('PayPal link', max_length=500)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CREATED)
    capture_id = models.CharField('PayPal capture ID', max_length=40, blank=True)
    authorization_id = models.CharField(
        'PayPal authorization ID', max_length=40, blank=True,
        help_text='Held (authorized) payment: taken when the attendant scans the pass, released if the visit fails.')
    payer_email = models.CharField(max_length=254, blank=True)
    payer_name = models.CharField(max_length=140, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    payment = models.OneToOneField(
        Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name='paypal_order'
    )
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'PayPal order'
        indexes = [models.Index(fields=['user_id', 'created_at'])]

    def __str__(self):
        return self.order_id


class Refund(models.Model):
    """Money given back for a visit, decided by the AI triage or by staff."""

    class Status(models.TextChoices):
        COMPLETED = 'completed', 'Refunded'
        PENDING = 'pending', 'Pending at PayPal'
        FAILED = 'failed', 'Failed'

    class DecidedBy(models.TextChoices):
        AI = 'ai', 'AI triage'
        STAFF = 'staff', 'Staff'
        PAYPAL = 'paypal', 'PayPal (denied or reversed)'

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name='refunds')
    report = models.ForeignKey(
        'bot.ProblemReport', on_delete=models.SET_NULL, null=True, blank=True, related_name='refunds'
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8)
    status = models.CharField(max_length=10, choices=Status.choices)
    provider_refund_id = models.CharField('PayPal refund ID', max_length=40, blank=True)
    decided_by = models.CharField(max_length=10, choices=DecidedBy.choices, default=DecidedBy.AI)
    category = models.CharField(max_length=30, blank=True)
    reason = models.CharField(max_length=255, help_text='What the user was told.')
    notes = models.TextField(blank=True, help_text="The AI's reasoning, for staff.")
    created_at = models.DateTimeField(default=timezone.now)
    # The operator payout this refund was taken off (empty: not taken off yet).
    payout = models.ForeignKey('Payout', on_delete=models.SET_NULL, null=True, blank=True, related_name='refunds_taken')

    def __str__(self):
        return f'Refund of {self.amount} {self.currency} for {self.payment}'


class Payout(models.Model):
    """Money sent to a restroom operator or cleaner through PayPal Payouts."""

    class Kind(models.TextChoices):
        EARNINGS = 'earnings', "Operator's share of visits"
        CLEANING = 'cleaning', 'Cleaning bonus'

    sender_batch_id = models.CharField(max_length=60, unique=True)
    batch_id = models.CharField('PayPal batch ID', max_length=40, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    restroom = models.ForeignKey(Restroom, on_delete=models.SET_NULL, null=True, blank=True, related_name='payouts')
    receiver = models.CharField('PayPal email', max_length=254)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='USD')
    visits = models.PositiveIntegerField(default=0, help_text='Paid visits this payout covers.')
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, default='PENDING', help_text="PayPal's batch status.")
    error = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=['restroom', 'created_at'])]

    def __str__(self):
        return f'Payout {self.sender_batch_id}'


class VisitPass(models.Model):
    """A prepaid TapFlash Pass: one PayPal payment buys several visits; each visit uses one. One PayPal fee is
    spread over all the visits instead of being paid on every small visit."""
    class Status(models.TextChoices):
        OFFERED = 'offered', 'Offered'
        PAID = 'paid', 'Paid'

    user_id = models.CharField('chat user ID', max_length=100, db_index=True)
    channel = models.CharField(max_length=10, default='web')
    visits_total = models.PositiveSmallIntegerField()
    visits_left = models.PositiveSmallIntegerField(default=0)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='USD')
    paypal_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                     help_text="PayPal's fee on the purchase, from the capture.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OFFERED)
    token = models.CharField(max_length=40, unique=True)
    paypal_order_id = models.CharField(max_length=40, blank=True)
    capture_id = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
