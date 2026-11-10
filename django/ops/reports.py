"""Income reports for operators: from TapFlash's ledger of PayPal payments, checked against PayPal's own
Transaction Search, with a short Gemini summary. The statement can support a loan application."""
import logging
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from payments.models import Payment, Payout
from payments.paypal import PayPalError

from .payouts import payment_value

logger = logging.getLogger(__name__)
DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
SUMMARY = [{'name': 'write_summary', 'description': "Write the operator's income summary.",
            'parameters': {'type': 'OBJECT', 'properties': {'summary': {'type': 'STRING', 'description':
                           'Two or three short plain sentences for a restroom operator: the trend, the busiest '
                           'times, and one practical tip. Use only the numbers given.'}}, 'required': ['summary']}}]


def build(restroom, config, *, now=None, checkout=None, ai=None):
    now = timezone.localtime(now or timezone.now())
    share = Decimal(restroom.operator_share_percent) / 100
    rows = []
    for p in Payment.objects.filter(restroom=restroom, status__in=['succeeded', 'refunded']).select_related('paypal_order').prefetch_related('refunds'):
        taken, refunded = payment_value(p, config)
        rows.append((timezone.localtime(p.created_at), taken - refunded, p.provider == 'paypal'))
    def bucket(key):
        out = defaultdict(lambda: [0, Decimal(0)])
        for at, net, _ in rows:
            k = key(at)
            if k is not None:
                out[k][0] += 1; out[k][1] += net
        return out
    day0 = now.date()
    days = bucket(lambda at: at.date() if (day0 - at.date()).days < 14 else None)
    months = bucket(lambda at: at.strftime('%Y-%m'))
    years = bucket(lambda at: at.year)
    weekdays, hours = Counter(at.weekday() for at, _, _ in rows), Counter(at.hour for at, _, _ in rows)
    money = lambda v: f'{v.quantize(Decimal("0.01"))}'
    payouts = list(Payout.objects.filter(restroom=restroom, kind=Payout.Kind.EARNINGS).exclude(status__in=['FAILED', 'DENIED', 'RETURNED', 'BLOCKED']).order_by('-created_at')
                   .values('created_at', 'amount', 'currency', 'batch_id', 'status'))
    report = {
        'restroom': restroom.name, 'operator': restroom.operator_name, 'currency': config.paypal_currency, 'share': restroom.operator_share_percent,
        'visits': len(rows), 'income': money(sum((n for _, n, _ in rows), Decimal(0))),
        'days': [{'day': (day0 - timedelta(days=i)).strftime('%a %d %b'), 'visits': days[day0 - timedelta(days=i)][0],
                  'income': money(days[day0 - timedelta(days=i)][1])} for i in range(13, -1, -1)],
        'months': [{'month': m, 'visits': v[0], 'income': money(v[1]), 'share': money(v[1] * share)} for m, v in sorted(months.items())],
        'years': [{'year': y, 'visits': v[0], 'income': money(v[1])} for y, v in sorted(years.items())],
        'busiestDay': DAYS[weekdays.most_common(1)[0][0]] if weekdays else '', 'busiestHour': hours.most_common(1)[0][0] if hours else None,
        'hours': [hours.get(h, 0) for h in range(24)],
        'payouts': [{'date': timezone.localtime(p['created_at']).strftime('%d %b %Y'), 'amount': money(p['amount']), 'currency': p['currency'],
                     'batch': p['batch_id'], 'status': p['status']} for p in payouts],
        'paidOut': money(sum((p['amount'] for p in payouts), Decimal(0))),
    }
    report['paypal'] = confirm(checkout, now)
    report['summary'] = summarize(report, ai)
    return report


def confirm(checkout, now):
    """The last 31 days of the PayPal account, from PayPal's Transaction Search."""
    if checkout is None:
        return {'ok': False, 'note': 'PayPal is not set up: payments are simulated.'}
    try:
        found = checkout.client.search_transactions(start=now - timedelta(days=30), end=now)
    except PayPalError as error:
        return {'ok': False, 'note': 'Cross-check with PayPal Transaction Search: turn on the "Transaction search" feature of the PayPal app to add it here.'}
    infos = [t.get('transaction_info', {}) for t in found.get('transaction_details', [])]
    money_in = [Decimal(i['transaction_amount']['value']) for i in infos
                if i.get('transaction_amount') and Decimal(i['transaction_amount']['value']) > 0]
    return {'ok': True, 'count': len(infos), 'in': f'{sum(money_in, Decimal(0)):.2f}', 'source': 'PayPal Transaction Search, last 31 days'}


def summarize(report, ai):
    plain = (f"{report['visits']} paid visits so far, {report['currency']} {report['income']} after refunds. "
             + (f"Busiest on {report['busiestDay']}s around {report['busiestHour']}:00." if report['busiestDay'] else ''))
    if ai is None or not report['visits']:
        return {'text': plain, 'by': 'TapFlash'}
    facts = {k: report[k] for k in ('visits', 'income', 'currency', 'busiestDay', 'busiestHour', 'months', 'days')}
    try:
        call = ai.decide(system='You help small restroom operators in Kenya understand their income. Be concrete and brief.',
                         prompt=f'Income facts (JSON): {facts}', functions=SUMMARY)
    except Exception:
        logger.exception('Gemini summary failed')
        call = None
    text = str((call or (None, {}))[1].get('summary', '')).strip()[:600]
    return {'text': text, 'by': 'Gemini'} if text else {'text': plain, 'by': 'TapFlash'}
