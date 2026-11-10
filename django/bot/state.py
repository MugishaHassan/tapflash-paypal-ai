"""What the chat remembers between two messages from a user.

A Session is saved as JSON in Conversation.state after every message, so a
restart, or another Passenger process answering the next message, loses
nothing. Money is written as text and times in ISO format, so both come back
exactly.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from payments.methods import PaymentMethod
from restrooms.facility import Facility
from restrooms.geo import Location


class Step(StrEnum):
    CHOOSE_NEED = 'choose_need'
    AWAIT_LOCATION = 'await_location'
    CHOOSE_RESTROOM = 'choose_restroom'
    CONFIRM_PAY = 'confirm_pay'
    CHOOSE_METHOD = 'choose_method'
    AWAIT_PHONE = 'await_phone'
    AUTHORIZE = 'authorize'
    AWAIT_CHECKOUT = 'await_checkout'  # the user is paying on PayPal's page
    PAYMENT_FAILED = 'payment_failed'
    NAVIGATING = 'navigating'
    ARRIVED = 'arrived'
    ACCESS = 'access'
    REPORT = 'report'
    PASS_EXPIRED = 'pass_expired'


@dataclass
class PaymentDraft:
    """The payment in the summary, waiting for the user to authorize it."""
    facility: Facility
    method: PaymentMethod
    phone: str | None
    price: Decimal
    fee: Decimal
    total: Decimal
    # Set when closing time cuts the pass short, so the summary says so.
    closes_at: datetime | None = None
    # The PayPal order sent to the user, while they pay on PayPal's page.
    order_id: str = ''
    checkout_url: str = ''
    checkout_amount: str = ''
    checkout_currency: str = ''


@dataclass
class Session:
    step: Step | None = None
    location: Location | None = None
    options: list[Facility] = field(default_factory=list)  # the restrooms on the list
    restroom: Facility | None = None  # the one chosen
    method: PaymentMethod | None = None
    payment: PaymentDraft | None = None
    pass_reference: str = ''  # the access pass of the journey under way
    fail_reason: str = ''
    return_step: Step | None = None  # where a problem report goes back to
    # The numbered choices on screen by name, so typing "back" works like 0.
    choices: dict[str, str] = field(default_factory=dict)
    whatsapp_phone: str | None = None
    need: str = ''  # what the user looks for: most clean, accessible, ...
    channel: str = 'web'  # web, bridge or cloud: where replies sent later go
    base_url: str = ''  # this server's address as the user's browser sees it

    def to_json(self):
        return {
            'step': self.step,
            'location': list(self.location) if self.location else None,
            'options': [_facility_to_json(facility) for facility in self.options],
            'restroom': _facility_to_json(self.restroom) if self.restroom else None,
            'method': _method_to_json(self.method) if self.method else None,
            'payment': _draft_to_json(self.payment) if self.payment else None,
            'pass_reference': self.pass_reference,
            'fail_reason': self.fail_reason,
            'return_step': self.return_step,
            'choices': self.choices,
            'whatsapp_phone': self.whatsapp_phone,
            'need': self.need,
            'channel': self.channel,
            'base_url': self.base_url,
        }

    @classmethod
    def from_json(cls, data):
        location = data.get('location')
        restroom = data.get('restroom')
        method = data.get('method')
        payment = data.get('payment')
        return cls(
            step=_step(data.get('step')),
            location=Location(*location) if location else None,
            options=[_facility_from_json(facility) for facility in data.get('options', [])],
            restroom=_facility_from_json(restroom) if restroom else None,
            method=_method_from_json(method) if method else None,
            payment=_draft_from_json(payment) if payment else None,
            pass_reference=data.get('pass_reference', ''),
            fail_reason=data.get('fail_reason', ''),
            return_step=_step(data.get('return_step')),
            choices=data.get('choices', {}),
            whatsapp_phone=data.get('whatsapp_phone'),
            need=data.get('need', ''),
            channel=data.get('channel', 'web'),
            base_url=data.get('base_url', ''),
        )


def _step(value):
    return Step(value) if value else None


def _facility_to_json(facility):
    return {**asdict(facility), 'price': str(facility.price)}


def _facility_from_json(data):
    return Facility(**{**data, 'price': Decimal(data['price'])})


def _method_to_json(method):
    return {**asdict(method), 'fee_percent': str(method.fee_percent)}


def _method_from_json(data):
    return PaymentMethod(**{**data, 'fee_percent': Decimal(data['fee_percent'])})


def _draft_to_json(draft):
    return {
        'facility': _facility_to_json(draft.facility),
        'method': _method_to_json(draft.method),
        'phone': draft.phone,
        'price': str(draft.price),
        'fee': str(draft.fee),
        'total': str(draft.total),
        'closes_at': draft.closes_at.isoformat() if draft.closes_at else None,
        'order_id': draft.order_id,
        'checkout_url': draft.checkout_url,
        'checkout_amount': draft.checkout_amount,
        'checkout_currency': draft.checkout_currency,
    }


def _draft_from_json(data):
    closes_at = data['closes_at']
    return PaymentDraft(
        facility=_facility_from_json(data['facility']),
        method=_method_from_json(data['method']),
        phone=data['phone'],
        price=Decimal(data['price']),
        fee=Decimal(data['fee']),
        total=Decimal(data['total']),
        closes_at=datetime.fromisoformat(closes_at) if closes_at else None,
        order_id=data.get('order_id', ''),
        checkout_url=data.get('checkout_url', ''),
        checkout_amount=data.get('checkout_amount', ''),
        checkout_currency=data.get('checkout_currency', ''),
    )
