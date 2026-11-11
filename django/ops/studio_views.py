"""The operators' analytics page, built with AG Studio. It reads the same JSON as the dashboard."""
from django.conf import settings
from django.shortcuts import render

from .views import staff_member_required

# AG Studio adds its own styles at run time and draws icons as data: images.
CSP = '; '.join([
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    'font-src data:',
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    'worker-src blob:',
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


@staff_member_required
def studio(request):
    config = settings.TAPFLASH
    response = render(request, 'ops/studio.html', {
        'bot_name': config.bot_name,
        'currency': config.paypal_currency,
        'sandbox': not config.paypal_live,
    })
    response['Content-Security-Policy'] = CSP
    response['Referrer-Policy'] = 'same-origin'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'no-store'
    return response
