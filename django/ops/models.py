"""Cleaners and their cleaning checks: a cleaner sends a photo of the restroom
they cleaned, Gemini checks it, and an approved check is paid a bonus at once
with PayPal Payouts. Checks the AI is unsure about wait for staff on /ops/."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from payments.models import Payout
from restrooms.models import Restroom


class Cleaner(models.Model):
    name = models.CharField(max_length=80)
    phone = models.CharField(
        'WhatsApp number', max_length=20, unique=True,
        help_text='With the country code, e.g. +254712345678. Photos from this number are cleaning checks.',
    )
    payout_email = models.EmailField('PayPal email', help_text='Where cleaning bonuses are sent.')
    restroom = models.ForeignKey(Restroom, on_delete=models.CASCADE, related_name='cleaners')
    bonus = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('1.00'), validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Paid for each approved cleaning, in PAYPAL_CURRENCY.',
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.name} ({self.restroom.name})'


class CleaningCheck(models.Model):
    class Status(models.TextChoices):
        APPROVED = 'approved', 'Approved'
        REVIEW = 'review', 'Waiting for staff'
        REJECTED = 'rejected', 'Not approved'

    cleaner = models.ForeignKey(Cleaner, on_delete=models.CASCADE, related_name='checks')
    restroom = models.ForeignKey(Restroom, on_delete=models.CASCADE, related_name='cleaning_checks')
    user_id = models.CharField('chat user ID', max_length=100)
    channel = models.CharField(max_length=10, default='bridge')
    photo = models.BinaryField()
    photo_type = models.CharField(max_length=30, default='image/jpeg')
    photo_hash = models.CharField(max_length=64, db_index=True, help_text='SHA-256: the same photo twice is refused.')
    photo_look = models.CharField(
        max_length=16, blank=True, db_index=True,
        help_text='Perceptual hash: a re-saved or re-sent copy of an earlier photo is refused too.')
    status = models.CharField(max_length=10, choices=Status.choices)
    score = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Gemini's cleanliness score, 1 to 5.")
    ai_note = models.CharField(max_length=300, blank=True)
    decided_by = models.CharField(max_length=80, blank=True, help_text='"ai", or the staff member.')
    payout = models.OneToOneField(Payout, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='cleaning_check')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=['cleaner', 'created_at'])]

    def __str__(self):
        return f'Cleaning check {self.pk} at {self.restroom.name}'


class Attendant(models.Model):
    """The person at a restroom's door who checks visitors in on /gate/ with
    their own login. Staff can check in at any restroom."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attendant')
    restroom = models.ForeignKey(Restroom, on_delete=models.CASCADE, related_name='attendants')

    def __str__(self):
        return f'{self.user.get_username()} at {self.restroom.name}'


class OperatorChat(models.Model):
    """The chat (WhatsApp or the web demo chat) where a restroom's operator gets alerts."""
    restroom = models.OneToOneField(Restroom, on_delete=models.CASCADE, related_name='operator_chat')
    user_id = models.CharField('chat user ID', max_length=100)
    linked_at = models.DateTimeField(default=timezone.now)


class SensorReading(models.Model):
    """A reading sent by any sensor through the sensor API, or by the sensor sandbox (test readings)."""
    class Kind(models.TextChoices):
        SEPTIC = 'septic_level', 'Septic tank level (%)'

    restroom = models.ForeignKey(Restroom, on_delete=models.CASCADE, related_name='sensor_readings')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    value = models.FloatField()
    sandbox = models.BooleanField(default=False, help_text='A test reading from the sensor sandbox.')
    at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-at']


class EmptyingOrder(models.Model):
    """A septic tank emptying the operator orders from the alert and pays with PayPal; TapFlash then pays
    the emptying company with PayPal Payouts."""
    class Status(models.TextChoices):
        ALERTED = 'alerted', 'Alert sent'
        PAYING = 'paying', 'Waiting for PayPal'
        PAID = 'paid', 'Paid, emptying booked'
        CANCELLED = 'cancelled', 'Cancelled'

    restroom = models.ForeignKey(Restroom, on_delete=models.CASCADE, related_name='emptying_orders')
    level = models.FloatField()
    days_left = models.FloatField(null=True, blank=True)
    provider = models.CharField(max_length=120)
    provider_email = models.EmailField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='USD')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ALERTED)
    token = models.CharField(max_length=40, unique=True)
    paypal_order_id = models.CharField(max_length=40, blank=True)
    capture_id = models.CharField(max_length=40, blank=True)
    provider_batch_id = models.CharField(max_length=40, blank=True)
    simulated = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
