from dataclasses import dataclass
from decimal import Decimal

from django.db import models


class Method(models.TextChoices):
    MPESA = 'mpesa', 'M-Pesa'
    AIRTEL = 'airtel', 'Airtel Money'
    CARD = 'card', 'Card (Visa / Mastercard)'
    PAYPAL = 'paypal', 'PayPal'
    TFPASS = 'tfpass', 'TapFlash Pass'


@dataclass(frozen=True)
class PaymentMethod:
    id: str
    label: str
    needs_phone: bool
    fee_percent: Decimal


def payment_methods(config):
    """The ways to pay, in the order the chat offers them. Mobile money is free;
    card and PayPal add CARD_FEE_PERCENT."""
    no_fee = Decimal(0)
    card_fee = config.card_fee_percent
    if config.paypal_only:
        # PAYPAL_ONLY: PayPal first, then a card as a PayPal guest checkout; no simulated mobile money.
        methods = [
            PaymentMethod(Method.PAYPAL.value, Method.PAYPAL.label, needs_phone=False, fee_percent=card_fee),
            PaymentMethod(Method.CARD.value, 'Card (via PayPal)', needs_phone=False, fee_percent=card_fee),
        ]
        if config.tfpass:
            # A prepaid pass: one PayPal payment for many visits, so the fee is not paid on every visit.
            methods.append(PaymentMethod(Method.TFPASS.value, f'TapFlash Pass ({config.tfpass_visits} visits)',
                                         needs_phone=False, fee_percent=no_fee))
        return methods
    return [
        PaymentMethod(Method.MPESA.value, Method.MPESA.label, needs_phone=True, fee_percent=no_fee),
        PaymentMethod(Method.AIRTEL.value, Method.AIRTEL.label, needs_phone=True, fee_percent=no_fee),
        PaymentMethod(Method.CARD.value, Method.CARD.label, needs_phone=False, fee_percent=card_fee),
        PaymentMethod(Method.PAYPAL.value, Method.PAYPAL.label, needs_phone=False, fee_percent=card_fee),
    ]
