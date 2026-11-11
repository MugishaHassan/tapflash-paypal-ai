"""The operators' analytics page, built with AG Studio. It reads the same JSON as the dashboard."""
from django.conf import settings
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

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
@ensure_csrf_cookie
def studio(request):
    config = settings.TAPFLASH
    response = render(request, 'ops/studio.html', {
        'bot_name': config.bot_name,
        'currency': config.paypal_currency,
        'sandbox': not config.paypal_live,
        'ai_on': config.ai_ready,
    })
    response['Content-Security-Policy'] = CSP
    response['Referrer-Policy'] = 'same-origin'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'no-store'
    return response


# ---- The Studio assistant: this site forwards one turn to Gemini, so the key never reaches the browser.
import json
import logging

import requests

from bot.ai.gemini import FALLBACK_MODEL
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

AI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
AI_TIMEOUT = 12            # seconds for each model, so two tries fit inside the web server's limit
AI_MAX_BODY = 600_000          # bytes; a turn carries the conversation and the tool schemas
AI_TURNS_PER_HOUR = 120        # for each signed-in user
AI_FIELDS = ('contents', 'systemInstruction', 'tools', 'toolConfig', 'generationConfig')


@staff_member_required
@require_POST
def assistant(request):
    config = settings.TAPFLASH
    if not config.ai_ready:
        return JsonResponse({'error': 'The assistant is off: no Gemini key is set on this server.'}, status=503)
    if len(request.body) > AI_MAX_BODY:
        return JsonResponse({'error': 'That conversation is too long. Start a new one.'}, status=413)
    try:
        sent = json.loads(request.body.decode('utf-8'))
        if not isinstance(sent, dict) or not isinstance(sent.get('contents'), list):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Send a Gemini request as JSON.'}, status=400)

    key = f'studio-ai:{request.user.pk}'
    cache.add(key, 0, timeout=3600)
    try:
        used = cache.incr(key)
    except ValueError:
        used = 1
    if used > AI_TURNS_PER_HOUR:
        return JsonResponse({'error': 'The assistant has had many questions this hour. Try again later.'}, status=429)

    body = {name: sent[name] for name in AI_FIELDS if name in sent}
    # A busy or slow model gives way to the fallback, as in the chat bot.
    res = None
    for model in dict.fromkeys([config.gemini_model, FALLBACK_MODEL]):
        try:
            res = requests.post(
                AI_URL.format(model=model), json=body, timeout=AI_TIMEOUT,
                headers={'x-goog-api-key': config.gemini_api_key, 'Content-Type': 'application/json'},
            )
        except requests.RequestException as error:
            logger.warning('Studio assistant: Gemini %s could not be reached: %s', model, type(error).__name__)
            res = None
            continue
        if res.status_code == 200:
            break
        logger.warning('Studio assistant: Gemini %s answered %s: %s', model, res.status_code, res.text[:300])
    if res is None:
        return JsonResponse({'error': 'The assistant could not be reached. Try again.'}, status=502)
    if res.status_code != 200:
        busy = res.status_code in (429, 503)
        try:
            detail = str(res.json().get('error', {}).get('message', ''))[:400]
        except ValueError:
            detail = ''
        return JsonResponse({'error': 'The assistant is busy right now. Try again in a minute.' if busy
                             else 'The assistant could not answer that. Try asking in another way.',
                             'detail': detail}, status=429 if busy else 502)
    try:
        answer = res.json()
    except ValueError:
        return JsonResponse({'error': 'The assistant gave an answer that could not be read.'}, status=502)
    return JsonResponse({name: answer[name] for name in ('candidates', 'usageMetadata', 'modelVersion') if name in answer})
