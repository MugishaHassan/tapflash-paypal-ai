import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class ChargeResult:
    ok: bool
    reference: str | None = None
    reason: str = ''
    # Not paid yet, but not failed either: the buyer is still on PayPal's page.
    waiting: bool = False


class MockPayments:
    """Simulated payments: nothing is charged.

    Every payment succeeds, except phone numbers ending in 0000, which are
    declined so the failure path can be tried. A real provider (M-Pesa Daraja
    STK push, Airtel Money, card or PayPal links) will offer the same charge()
    and return the same ChargeResult.
    """

    name = 'mock'
    is_test_mode = True

    def charge(self, *, user_id, method, phone, amount, currency, facility_code):
        if phone and phone.endswith('0000'):
            return ChargeResult(
                ok=False, reason='The payment was declined (test numbers ending in 0000 always fail).'
            )
        return ChargeResult(ok=True, reference=f'TF{secrets.token_hex(5).upper()}')
