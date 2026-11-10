from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from bot.handler import default_engine

from . import tfpass
from .models import VisitPass
from .paypal import PayPalError


def page(request, token):
    found = get_object_or_404(VisitPass, token=token)
    return render(request, 'payments/tfpass.html', {'p': found, 'bot_name': settings.TAPFLASH.bot_name,
                                                    'per_visit': tfpass.visit_value(found), 'error': request.GET.get('error', '')})


@require_POST
def pay(request, token):
    found = get_object_or_404(VisitPass, token=token, status=VisitPass.Status.OFFERED)
    try:
        return HttpResponseRedirect(tfpass.start_payment(found, default_engine().checkout, request.build_absolute_uri('/')))
    except PayPalError as error:
        return HttpResponseRedirect(f'/tfpass/{token}?error={str(error)[:120]}')


def paid(request, token):
    found = get_object_or_404(VisitPass, token=token)
    checkout = default_engine().checkout
    if checkout is None and not request.GET.get('simulated'):
        raise Http404
    try:
        tfpass.finish_payment(found, checkout)
    except PayPalError as error:
        return HttpResponseRedirect(f'/tfpass/{token}?error={str(error)[:120]}')
    return HttpResponseRedirect(f'/tfpass/{token}')
