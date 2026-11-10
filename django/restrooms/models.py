from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .facility import Facility
from .hours import parse_hours

MAX_PHOTO_BYTES = 8 * 1024 * 1024


class Restroom(models.Model):
    code = models.SlugField(
        max_length=64,
        unique=True,
        help_text='A short unique name such as nbi-city-market. It is used in access QR codes and payment records.',
    )
    name = models.CharField(max_length=120)
    area = models.CharField(
        max_length=200, blank=True, help_text='Where to find it, for example "Muindi Mbingu Street, Nairobi CBD".'
    )
    latitude = models.FloatField(db_index=True, validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.FloatField(validators=[MinValueValidator(-180), MaxValueValidator(180)])
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal(0))],
        help_text='Leave empty to use the default price (DEFAULT_PRICE in the settings).',
    )
    opening_hours = models.CharField(
        max_length=60,
        blank=True,
        help_text='For example "6:00 AM – 9:00 PM", "06:00-21:00", "6 pm to 2 am" or "24 hours". '
        'Leave empty if it never closes.',
    )
    photo = models.ImageField(upload_to='restrooms/', blank=True)
    cleanliness = models.PositiveSmallIntegerField(
        'cleanliness (1-5)', default=0, validators=[MaxValueValidator(5)],
        help_text='1 to 5 stars, 0 if not rated yet. "The most clean" lists the best rated first.',
    )
    accessible = models.BooleanField(default=False, help_text='Step-free entrance, easy to reach.')
    disabled_toilet = models.BooleanField('toilet for the disabled', default=False)
    baby_changing = models.BooleanField('baby changing station', default=False)
    is_active = models.BooleanField('active', default=True, help_text='Only active restrooms are offered in the chat.')
    operator_name = models.CharField(max_length=120, blank=True, help_text='Who runs and cleans this restroom.')
    payout_email = models.EmailField(
        'operator PayPal email', blank=True,
        help_text="The operator's share of each paid visit is sent here with PayPal Payouts.",
    )
    operator_share_percent = models.PositiveSmallIntegerField(
        default=80, validators=[MaxValueValidator(100)],
        help_text='The part of each visit (after refunds) paid out to the operator.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def clean(self):
        if self.opening_hours and parse_hours(self.opening_hours) is None:
            raise ValidationError({
                'opening_hours': "These opening hours can't be read. "
                'Write them like 6:00 AM – 9:00 PM, 06:00-21:00 or 24 hours.'
            })

    def to_facility(self, default_price, cleaned_at=None, cleaned_score=None):
        return Facility(
            code=self.code,
            name=self.name,
            area=self.area,
            lat=self.latitude,
            lng=self.longitude,
            price=default_price if self.price is None else self.price,
            hours=self.opening_hours,
            photo=self.photo.name or '',
            cleanliness=self.cleanliness,
            accessible=self.accessible,
            disabled_toilet=self.disabled_toilet,
            baby_changing=self.baby_changing,
            cleaned_at=cleaned_at.isoformat() if cleaned_at else '',
            cleaned_checked_by_ai=cleaned_score is not None,
        )
