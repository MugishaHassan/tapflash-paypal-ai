"""Paying on PayPal's page, for the chat.

The chat can't take card or PayPal details itself, so it sends a PayPal link.
start() creates the PayPal order and keeps a PayPalOrder row; finish() takes
the money once the buyer has approved it, which can come from three places:
PayPal's return page, PayPal's webhook, or the user tapping "I have paid".
Each of them is safe to run twice.

PayPal does not take KES: the total is charged in PAYPAL_CURRENCY at
PAYPAL_RATE (129 KES to the dollar by default) and both amounts are shown.
"""
import logging
import secrets
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from .mock import ChargeResult
from .models import PayPalOrder
from .paypal import first_authorization, PayPalClient, PayPalError, first_capture

logger = logging.getLogger(__name__)

CENT = Decimal('0.01')
WAITING = 'You have not finished paying on PayPal yet.'
DECLINED = 'PayPal declined the payment. Try again, or choose another way to pay.'
NOT_REACHED = 'PayPal could not be reached right now.'


@dataclass(frozen=True)
class CheckoutLink:
    order_id: str
    url: str
    amount: Decimal
    currency: str


def to_paypal_amount(local_total, rate):
    """KES 101 at 129 to the dollar gives Decimal('0.78'); never below one cent."""
    return max((Decimal(local_total) / rate).quantize(CENT, ROUND_HALF_UP), CENT)


class PayPalCheckout:
    name = 'paypal'

    def __init__(self, client, config, clock=timezone.now):
        self.client = client
        self.config = config
        self.clock = clock

    @property
    def is_test_mode(self):
        return not self.client.live

    def start(self, *, user_id, channel, draft, local_currency, base_url):
        """Create the PayPal order for a payment draft. Raises PayPalError."""
        facility = draft.facility
        amount = to_paypal_amount(draft.total, self.config.paypal_rate)
        currency = self.config.paypal_currency
        custom_id = f'tf-{secrets.token_hex(8)}'
        order = self.client.create_order(
            amount=amount,
            currency=currency,
            description=f'{self.config.bot_name} pass: {facility.name}',
            reference=facility.code,
            custom_id=custom_id,
            return_url=f'{base_url}/paypal/return',
            cancel_url=f'{base_url}/paypal/cancel',
            brand_name=self.config.bot_name,
            request_id=custom_id,
            landing_page='GUEST_CHECKOUT' if draft.method.id == 'card' else 'NO_PREFERENCE',
            intent='AUTHORIZE' if self.config.paypal_hold else 'CAPTURE',
        )
        if not order['approve_url']:
            raise PayPalError('PayPal gave no link to pay with')
        PayPalOrder.objects.create(
            order_id=order['id'],
            user_id=user_id,
            channel=channel,
            facility_code=facility.code,
            facility_name=facility.name,
            local_total=draft.total,
            local_currency=local_currency,
            amount=amount,
            currency=currency,
            approve_url=order['approve_url'],
        )
        return CheckoutLink(order['id'], order['approve_url'], amount, currency)

    def finish(self, order_id):
        """Take the money for an order the buyer approved.

        Returns a ChargeResult: ok with the capture id as the reference,
        waiting=True if the buyer hasn't approved it yet, or failed."""
        record = PayPalOrder.objects.select_for_update().filter(order_id=order_id).first()
        if record is None:
            return ChargeResult(ok=False, reason=NOT_REACHED)
        if record.status == PayPalOrder.Status.COMPLETED:
            return ChargeResult(ok=True, reference=record.capture_id)
        try:
            order = self.client.get_order(order_id)
            status = order.get('status', '')
            if status in ('CREATED', 'SAVED', 'PAYER_ACTION_REQUIRED'):
                return ChargeResult(ok=False, waiting=True, reason=WAITING)
            if status == 'APPROVED':
                order = self.client.authorize_order(order_id) if self.config.paypal_hold else self.client.capture_order(order_id)
        except PayPalError as error:
            if error.issue in ('ORDER_ALREADY_CAPTURED', 'ORDER_ALREADY_AUTHORIZED'):
                order = self.client.get_order(order_id)
            elif error.issue in ('INSTRUMENT_DECLINED', 'PAYER_ACTION_REQUIRED', 'ORDER_NOT_APPROVED'):
                # The buyer must pick another card or account on PayPal's page.
                return ChargeResult(ok=False, waiting=True, reason=WAITING)
            else:
                logger.warning('PayPal order %s could not be finished: %s', order_id, error)
                return ChargeResult(ok=False, reason=NOT_REACHED)

        held = first_authorization(order)
        if held.get('id') and not first_capture(order):
            # Held, not taken: the attendant's scan takes it (take_held), a failed visit releases it (refund).
            if order.get('status') != 'COMPLETED' or held.get('status') not in ('CREATED', 'PENDING'):
                record.status = PayPalOrder.Status.FAILED
                record.failure_reason = f"PayPal status {order.get('status', '?')}/{held.get('status', '?')}"[:255]
                record.save(update_fields=['status', 'failure_reason'])
                return ChargeResult(ok=False, reason=DECLINED)
            capture = {'id': ''}
        else:
            capture = first_capture(order)
        if not held.get('id') and (order.get('status') != 'COMPLETED' or capture.get('status') not in ('COMPLETED', 'PENDING')):
            record.status = PayPalOrder.Status.FAILED
            record.failure_reason = f"PayPal status {order.get('status', '?')}/{capture.get('status', '?')}"[:255]
            record.save(update_fields=['status', 'failure_reason'])
            return ChargeResult(ok=False, reason=DECLINED)

        payer = order.get('payer', {})
        name = payer.get('name', {})
        record.status = PayPalOrder.Status.COMPLETED
        record.capture_id = capture['id']
        record.authorization_id = held.get('id', '')[:40]
        record.payer_email = payer.get('email_address', '')[:254]
        record.payer_name = ' '.join(filter(None, [name.get('given_name'), name.get('surname')]))[:140]
        record.completed_at = self.clock()
        record.save(update_fields=['status', 'capture_id', 'authorization_id', 'payer_email', 'payer_name', 'completed_at'])
        return ChargeResult(ok=True, reference=capture['id'] or record.authorization_id)

    def cancel(self, order_id):
        """The user chose another way to pay. An order nobody captures takes
        no money, so PayPal needs no call."""
        PayPalOrder.objects.filter(order_id=order_id, status=PayPalOrder.Status.CREATED).update(
            status=PayPalOrder.Status.CANCELLED
        )

    def attach_payment(self, order_id, payment):
        PayPalOrder.objects.filter(order_id=order_id).update(payment=payment)

    def take_held(self, payment):
        """The attendant scanned the pass: take the held money. Safe to repeat. Raises PayPalError."""
        order = PayPalOrder.objects.select_for_update().get(payment=payment)
        if order.capture_id or not order.authorization_id:
            return order.capture_id
        result = self.client.capture_authorization(order.authorization_id, amount=order.amount, currency=order.currency,
                                                   request_id=f'take-{order.order_id}')
        order.capture_id = result.get('id', '')[:40]
        order.save(update_fields=['capture_id'])
        return order.capture_id

    def refund(self, payment, *, amount, note, request_id):
        """Give money back on PayPal. A held payment is released instead (or, for part of it, the rest is
        taken), so the visitor never pays and is refunded. Raises PayPalError."""
        order = PayPalOrder.objects.get(payment=payment)
        if order.authorization_id and not order.capture_id:
            if amount >= order.amount:
                self.client.void_authorization(order.authorization_id)
                return {'id': f'VOID-{order.authorization_id}'[:40], 'status': 'COMPLETED'}
            kept = self.client.capture_authorization(order.authorization_id, amount=order.amount - amount,
                                                     currency=order.currency, request_id=f'take-{order.order_id}')
            order.capture_id = kept.get('id', '')[:40]
            order.save(update_fields=['capture_id'])
            return {'id': f'HOLD-{order.authorization_id}'[:40], 'status': 'COMPLETED'}
        return self.client.refund_capture(
            order.capture_id, amount=amount, currency=order.currency, note=note, request_id=request_id
        )


def build_checkout(config):
    """PayPal checkout when PayPal is set up, else None (cards and PayPal are simulated)."""
    if not config.paypal_ready:
        return None
    client = PayPalClient(config.paypal_client_id, config.paypal_client_secret, live=config.paypal_live)
    return PayPalCheckout(client, config)
