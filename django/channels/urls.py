from django.urls import path

from . import link, paypal_views, views

# Exact paths with no trailing slash: Meta, PayPal and the bridge must never be redirected.
urlpatterns = [
    path('', views.home, name='home'),
    path('index.html', views.home),
    path('api/message', views.api_message, name='api-message'),
    path('api/outbox', views.api_outbox, name='api-outbox'),
    path('webhook', views.webhook, name='webhook'),
    path('paypal/return', paypal_views.paypal_return, name='paypal-return'),
    path('paypal/cancel', paypal_views.paypal_cancel, name='paypal-cancel'),
    path('paypal/webhook', paypal_views.paypal_webhook, name='paypal-webhook'),
    path('link', link.page, name='link'),
    path('link/status', link.status, name='link-status'),
]
