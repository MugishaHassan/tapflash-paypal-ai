"""PayPal's side of a payment: the pages buyers come back to, and the webhook.

/paypal/return  PayPal sends the buyer here after they approve. The order is
                captured (if the chat is still waiting for it) and the
                receipt, route and access code are pushed to the chat.
/paypal/cancel  The buyer pressed cancel on PayPal's page.
/paypal/webhook PayPal's own notice that an order was approved, for buyers
                who close the page before coming back. The signature is
                checked with PayPal before anything is done.
"""
import html
import json
import logging

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from bot import outbox
from bot.handler import default_engine, handle_event
from payments.models import PayPalOrder
from payments.webhooks import LOST, handle_capture_lost, handle_payout_batch, handle_payout_item, handle_refunded

from .cloud_api import GraphClient
from .web_chat import SECURITY_HEADERS

logger = logging.getLogger(__name__)

MAX_WEBHOOK_BYTES = 256 * 1024


def _order(request):
    order_id = request.GET.get('token', '')[:40]
    return PayPalOrder.objects.filter(order_id=order_id).first() if order_id else None


def _push(order, replies):
    if not replies:
        return
    client = GraphClient(settings.TAPFLASH) if order.channel == 'cloud' else None
    outbox.push(order.user_id, order.channel, replies, cloud_client=client)


def paypal_return(request):
    order = _order(request)
    if order is None:
        return _page(404, 'Payment not found', 'This PayPal payment is not one of ours. Nothing was charged.', None)
    replies = handle_event(order.user_id, lambda engine, store: engine.on_checkout_return(store, order.order_id))
    _push(order, replies)
    order.refresh_from_db()
    if order.status == PayPalOrder.Status.COMPLETED:
        return _page(200, 'Payment received', (
            f'You paid {order.currency} {order.amount} ({order.local_currency} {order.local_total:.0f}) '
            f'for {order.facility_name}. Your receipt, the way there and your access code are in the chat.'
        ), order)
    if replies is None:
        return _page(200, 'Payment not taken', (
            'The chat stopped waiting for this payment (it timed out, or you chose another way to pay), '
            'so PayPal did not take any money. Go back to the chat to start again.'
        ), order)
    return _page(200, 'Not paid yet', (
        "PayPal hasn't confirmed this payment yet. Go back to the chat and tap I have paid, "
        'or pay again with the link there.'
    ), order)


def paypal_cancel(request):
    order = _order(request)
    if order is None:
        return _page(404, 'Payment not found', 'This PayPal payment is not one of ours. Nothing was charged.', None)
    replies = handle_event(order.user_id, lambda engine, store: engine.on_checkout_cancel(store, order.order_id))
    _push(order, replies)
    return _page(200, 'Payment cancelled', 'Nothing was charged. Go back to the chat to choose another way to pay.',
                 order)


@csrf_exempt
def paypal_webhook(request):
    if request.method != 'POST':
        return HttpResponse(status=405)
    raw = request.body
    if len(raw) > MAX_WEBHOOK_BYTES:
        return HttpResponse(status=413)
    engine = default_engine()
    config = settings.TAPFLASH
    if not engine.checkout or not engine.checkout.client.verify_webhook(
        headers=request.headers, raw_body=raw, webhook_id=config.paypal_webhook_id
    ):
        return HttpResponse(status=400)
    try:
        event = json.loads(raw.decode('utf-8'))
    except ValueError:
        return HttpResponse(status=400)
    kind = event.get('event_type', '')
    resource = event.get('resource') or {}
    if kind == 'CHECKOUT.ORDER.APPROVED':
        order = PayPalOrder.objects.filter(order_id=str(resource.get('id', ''))[:40]).first()
        if order and order.status == PayPalOrder.Status.CREATED:
            replies = handle_event(
                order.user_id, lambda engine, store: engine.on_checkout_return(store, order.order_id), engine
            )
            _push(order, replies)
    elif kind in LOST:
        handle_capture_lost(kind, resource, timezone.now())
    elif kind == 'PAYMENT.CAPTURE.REFUNDED':
        handle_refunded(resource, timezone.now())
    elif kind.startswith('PAYMENT.PAYOUTS-ITEM.'):
        handle_payout_item(resource)
    elif kind.startswith('PAYMENT.PAYOUTSBATCH.'):
        handle_payout_batch(resource)
    else:
        logger.info('PayPal webhook %s received', kind)
    return HttpResponse(status=200)


def _back_link(order):
    """Back to WhatsApp for WhatsApp chats, back to the test chat for the web."""
    number = settings.TAPFLASH.bot_whatsapp_number
    if order is not None and order.channel != 'web' and number:
        return f'https://wa.me/{number}', 'Back to WhatsApp'
    if order is not None and order.channel == 'web':
        return '/', 'Back to the chat'
    return '', ''


def _page(status, title, text, order):
    bot = html.escape(settings.TAPFLASH.bot_name)
    href, label = _back_link(order)
    button = f'<a class="btn" href="{html.escape(href)}">{html.escape(label)}</a>' if href else ''
    ok = title == 'Payment received'
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} | {bot}</title>
<style>
:root {{ --ink:#1C1E49; --sun:#79D3E7; --ok:#1B7F4B; --bad:#B4372C; --paper:#F6F4EE; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; min-height:100vh; display:grid; place-items:center; padding:16px;
  background:var(--paper); color:var(--ink); font:16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ width:100%; max-width:420px; background:#fff; border-radius:18px; padding:28px 24px;
  box-shadow:0 10px 30px rgba(10,47,68,.12); text-align:center; }}
.mark {{ width:64px; height:64px; border-radius:50%; margin:0 auto 14px; display:grid; place-items:center;
  font-size:32px; font-weight:700; color:#fff; background:{'var(--ok)' if ok else 'var(--ink)'}; }}
h1 {{ font-size:24px; margin:0 0 8px; }}
p {{ margin:0 0 20px; color:#33505f; }}
.btn {{ display:inline-block; padding:12px 22px; border-radius:999px; background:var(--sun); color:var(--ink);
  font-weight:700; text-decoration:none; }}
small {{ display:block; margin-top:18px; color:#6b7f89; }}
</style></head>
<body><main>
<div class="mark" aria-hidden="true">{'&#10003;' if ok else 'i'}</div>
<h1>{html.escape(title)}</h1>
<p>{html.escape(text)}</p>
{button}
<small>{bot} uses PayPal to take card and PayPal payments.</small>
</main></body></html>"""
    response = HttpResponse(body, status=status, content_type='text/html; charset=utf-8')
    for name, value in SECURITY_HEADERS.items():
        response[name] = value
    response['Cache-Control'] = 'no-store'
    return response
