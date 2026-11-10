"""PayPal REST API: Orders v2 (create, look up, capture), refunds, payouts and
webhook signature checks.

Sandbox unless PAYPAL_ENV=live. One access token is kept until a minute
before it runs out. Every call that moves money carries a PayPal-Request-Id,
so retrying it (a timeout, a double tap) never charges, refunds or pays twice.
Secrets are never logged; PayPal's debug id is, so a failed call can be
traced in the PayPal dashboard.
"""
import json
import logging
import time
from urllib.parse import urlencode
from decimal import Decimal

import requests

logger = logging.getLogger(__name__)

SANDBOX_URL = 'https://api-m.sandbox.paypal.com'
LIVE_URL = 'https://api-m.paypal.com'
TIMEOUT = (5, 25)  # seconds to connect, seconds to answer
WEBHOOK_HEADERS = {
    'auth_algo': 'PAYPAL-AUTH-ALGO',
    'cert_url': 'PAYPAL-CERT-URL',
    'transmission_id': 'PAYPAL-TRANSMISSION-ID',
    'transmission_sig': 'PAYPAL-TRANSMISSION-SIG',
    'transmission_time': 'PAYPAL-TRANSMISSION-TIME',
}


class PayPalError(Exception):
    """A call PayPal refused or could not answer.

    `issue` is PayPal's reason code when it gave one (INSTRUMENT_DECLINED,
    ORDER_ALREADY_CAPTURED, ...)."""

    def __init__(self, message, *, status=None, issue='', debug_id=''):
        super().__init__(message)
        self.status = status
        self.issue = issue
        self.debug_id = debug_id


def money(value, currency):
    """PayPal's amount object: {'currency_code': 'USD', 'value': '0.78'}."""
    return {'currency_code': currency, 'value': f'{Decimal(value):.2f}'}


def link(resource, *rels):
    """The first of the links named `rels` in a PayPal answer, or ''."""
    for rel in rels:
        for item in resource.get('links', []):
            if item.get('rel') == rel:
                return item.get('href', '')
    return ''


def first_authorization(order):
    for unit in order.get('purchase_units', []):
        for item in unit.get('payments', {}).get('authorizations', []):
            return item
    return {}


def first_capture(order):
    """The capture of a completed order, or {}."""
    for unit in order.get('purchase_units', []):
        captures = unit.get('payments', {}).get('captures', [])
        if captures:
            return captures[0]
    return {}


class PayPalClient:
    def __init__(self, client_id, secret, *, live=False, http=None, clock=time.monotonic):
        if not client_id or not secret:
            raise ValueError('PayPal needs PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET')
        self.client_id = client_id
        self.secret = secret
        self.base_url = LIVE_URL if live else SANDBOX_URL
        self.live = live
        self.http = http or requests.Session()
        self.clock = clock
        self._token = ''
        self._token_until = 0.0

    # ---- plumbing ----

    def access_token(self):
        if self._token and self.clock() < self._token_until:
            return self._token
        try:
            res = self.http.post(
                f'{self.base_url}/v1/oauth2/token',
                auth=(self.client_id, self.secret),
                data={'grant_type': 'client_credentials'},
                headers={'Accept': 'application/json'},
                timeout=TIMEOUT,
            )
        except requests.RequestException as error:
            raise PayPalError(f'Could not reach PayPal: {type(error).__name__}') from None
        if res.status_code != 200:
            raise PayPalError(
                'PayPal refused the client id or secret', status=res.status_code,
                debug_id=res.headers.get('PayPal-Debug-Id', ''),
            )
        data = res.json()
        self._token = data['access_token']
        # Renew a minute early, so a token never runs out mid-call.
        self._token_until = self.clock() + max(int(data.get('expires_in', 0)) - 60, 0)
        return self._token

    def call(self, method, path, *, body=None, raw_body=None, request_id=None, ok=(200, 201)):
        headers = {
            'Authorization': f'Bearer {self.access_token()}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Prefer': 'return=representation',
        }
        if request_id:
            headers['PayPal-Request-Id'] = request_id
        data = raw_body if raw_body is not None else (json.dumps(body) if body is not None else None)
        try:
            res = self.http.request(method, f'{self.base_url}{path}', data=data, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as error:
            raise PayPalError(f'Could not reach PayPal: {type(error).__name__}') from None
        debug_id = res.headers.get('PayPal-Debug-Id', '')
        try:
            payload = res.json() if res.content else {}
        except ValueError:
            payload = {}
        if res.status_code not in ok:
            details = payload.get('details') or [{}]
            issue = details[0].get('issue', '') or payload.get('name', '')
            message = details[0].get('description') or payload.get('message') or f'HTTP {res.status_code}'
            logger.warning('PayPal %s %s failed: %s %s (debug id %s)', method, path, res.status_code, issue, debug_id)
            raise PayPalError(message, status=res.status_code, issue=issue, debug_id=debug_id)
        return payload

    # ---- orders ----

    def create_order(self, *, amount, currency, description, reference, custom_id, return_url, cancel_url,
                     brand_name, request_id, landing_page='NO_PREFERENCE', intent='CAPTURE'):
        """A one-off order the buyer approves on PayPal's page: with their
        PayPal account, or with a card as a guest (landing_page GUEST_CHECKOUT
        opens the card form first). Nothing to ship."""
        order = self.call('POST', '/v2/checkout/orders', request_id=request_id, body={
            'intent': intent,
            'purchase_units': [{
                'reference_id': reference[:256],
                'custom_id': custom_id[:127],
                'description': description[:127],
                'soft_descriptor': brand_name[:22],
                'amount': money(amount, currency),
            }],
            'payment_source': {'paypal': {'experience_context': {
                'brand_name': brand_name[:127],
                'user_action': 'PAY_NOW',
                'shipping_preference': 'NO_SHIPPING',
                'landing_page': landing_page,
                'return_url': return_url,
                'cancel_url': cancel_url,
            }}},
        })
        return {
            'id': order['id'],
            'status': order.get('status', ''),
            # "payer-action" with payment_source; "approve" in older answers.
            'approve_url': link(order, 'payer-action', 'approve'),
        }

    def get_order(self, order_id):
        return self.call('GET', f'/v2/checkout/orders/{order_id}', ok=(200,))

    def capture_order(self, order_id):
        """Take the money for an approved order. Capturing twice is safe: the
        same request id gives back the first capture."""
        return self.call('POST', f'/v2/checkout/orders/{order_id}/capture', body={},
                         request_id=f'capture-{order_id}')

    # ---- refunds ----

    def authorize_order(self, order_id):
        """Hold the buyer's money for an approved order (intent AUTHORIZE); nothing is taken yet."""
        return self.call('POST', f'/v2/checkout/orders/{order_id}/authorize', body={}, request_id=f'auth-{order_id}')

    def capture_authorization(self, authorization_id, *, amount, currency, request_id):
        """Take held money (all of it, or less: the rest is released). Final: the hold ends."""
        return self.call('POST', f'/v2/payments/authorizations/{authorization_id}/capture', request_id=request_id,
                         body={'amount': money(amount, currency), 'final_capture': True})

    def void_authorization(self, authorization_id):
        """Release held money: nothing is taken."""
        return self.call('POST', f'/v2/payments/authorizations/{authorization_id}/void', ok=(200, 201, 204))

    def refund_capture(self, capture_id, *, amount, currency, note, request_id):
        refund = self.call('POST', f'/v2/payments/captures/{capture_id}/refund', request_id=request_id, body={
            'amount': money(amount, currency),
            'note_to_payer': note[:255],
        })
        return {'id': refund.get('id', ''), 'status': refund.get('status', '')}

    # ---- payouts ----

    def create_payout(self, *, sender_batch_id, items, subject, message):
        """Send money to PayPal accounts by email. items: dicts with receiver,
        amount, currency, note and sender_item_id."""
        batch = self.call('POST', '/v1/payments/payouts', request_id=sender_batch_id, body={
            'sender_batch_header': {
                'sender_batch_id': sender_batch_id,
                'email_subject': subject[:255],
                'email_message': message[:1000],
            },
            'items': [{
                'recipient_type': 'EMAIL',
                'receiver': item['receiver'],
                'amount': {'value': f"{Decimal(item['amount']):.2f}", 'currency': item['currency']},
                'note': item['note'][:4000],
                'sender_item_id': item['sender_item_id'][:63],
            } for item in items],
        })
        header = batch.get('batch_header', {})
        return {'batch_id': header.get('payout_batch_id', ''), 'status': header.get('batch_status', '')}

    def get_payout(self, batch_id):
        return self.call('GET', f'/v1/payments/payouts/{batch_id}', ok=(200,))

    # ---- webhooks ----

    def search_transactions(self, *, start, end, page=1):
        """PayPal's Transaction Search (Reporting API): the account's transactions between two datetimes
        (at most 31 days apart). Needs the app's "Transaction search" feature."""
        query = urlencode({'start_date': start.strftime('%Y-%m-%dT%H:%M:%S%z'), 'end_date': end.strftime('%Y-%m-%dT%H:%M:%S%z'),
                           'fields': 'transaction_info', 'page_size': 500, 'page': page})
        return self.call('GET', f'/v1/reporting/transactions?{query}')

    def verify_webhook(self, *, headers, raw_body, webhook_id):
        """Ask PayPal whether a webhook call really came from PayPal. The event
        is passed on byte for byte: re-encoding it can break the signature."""
        if not webhook_id:
            return False
        fields = {name: headers.get(header, '') for name, header in WEBHOOK_HEADERS.items()}
        if not all(fields.values()):
            return False
        fields['webhook_id'] = webhook_id
        body = json.dumps(fields)[:-1] + ', "webhook_event": ' + raw_body.decode('utf-8') + '}'
        try:
            answer = self.call('POST', '/v1/notifications/verify-webhook-signature', raw_body=body, ok=(200,))
        except PayPalError:
            return False
        return answer.get('verification_status') == 'SUCCESS'
