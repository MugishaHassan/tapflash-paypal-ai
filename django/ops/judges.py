"""/judges/: one page that tells a hackathon judge what to try, in order, with
one-click entry to the dashboard and the door check (JUDGE_ONE_CLICK, for a
sandbox demo only) and the PayPal sandbox buyer login."""
from django.conf import settings
from django.contrib.auth import get_user_model, login
from pathlib import Path

from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from bot.handler import default_engine

JUDGE = 'judge'
PHOTOS = Path(settings.BASE_DIR) / 'restrooms' / 'demo_photos'
# The photo a judge can send as the demo cleaner (Kenyatta Avenue): that restroom's own demo photo.
SAMPLES = {'clean': '1.jpg'}
TEST_CLEANER_PHONE = '+254712345678'
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
        'refund_per_day': settings.TAPFLASH.max_refunds_per_day,
        'refund_window_minutes': settings.TAPFLASH.refund_window_minutes,
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


@require_POST
def sample_photo(request, kind):
    """A judge sends a sample cleaning photo as the demo cleaner. "Sent before" counts only this judge's own
    photos, so one judge's test never blocks the next."""
    from ops.cleaning import check_photo, cleaner_for
    if kind not in SAMPLES:
        raise Http404
    config = settings.TAPFLASH
    cleaner = cleaner_for(TEST_CLEANER_PHONE, config)
    if cleaner is None:
        return JsonResponse({'error': 'The demo cleaner is not set up on this server.'}, status=404)
    if not request.session.session_key:
        request.session.save()
    engine = default_engine()
    text = check_photo(
        cleaner=cleaner, user_id=f'web:judge-{request.session.session_key[:16]}', channel='web',
        photo=(PHOTOS / SAMPLES[kind]).read_bytes(), mimetype='image/jpeg', judge=engine.cleaning_judge,
        checkout=engine.checkout, config=config, allow_reference_copy=True,
    )
    return JsonResponse({'text': text})
