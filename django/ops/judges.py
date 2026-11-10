"""/judges/: one page that tells a hackathon judge what to try, in order, with
one-click entry to the dashboard and the door check (JUDGE_ONE_CLICK, for a
sandbox demo only) and the PayPal sandbox buyer login."""
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from bot.handler import default_engine

JUDGE = 'judge'
PLACES = {'ops': '/ops/', 'gate': '/gate/'}


def judges(request):
    engine = default_engine()
    response = render(request, 'ops/judges.html', {
        'bot_name': settings.TAPFLASH.bot_name,
        'one_click': settings.JUDGE_ONE_CLICK,
        'buyer_email': settings.DEMO_BUYER_EMAIL,
        'buyer_password': settings.DEMO_BUYER_PASSWORD,
        'paypal_on': bool(engine.checkout),
        'ai_on': bool(engine.triage),
        'whatsapp': settings.TAPFLASH.bot_whatsapp_number,
    })
    response['Cache-Control'] = 'no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Referrer-Policy'] = 'same-origin'
    return response


@require_POST
def judge_login(request, place):
    """Log in as the limited judge user (staff for /ops/ and /gate/, no admin rights)."""
    if not settings.JUDGE_ONE_CLICK or place not in PLACES:
        raise Http404
    user, created = get_user_model().objects.get_or_create(username=JUDGE, defaults={'is_staff': True})
    if created:
        user.set_unusable_password()
    user.is_staff, user.is_superuser, user.is_active = True, False, True
    user.save()
    user.user_permissions.clear()
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return redirect(PLACES[place])
