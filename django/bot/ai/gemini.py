"""Google Gemini over plain REST (no SDK): one call, one decision.

The bot never lets the model act on its own. Each call offers a few
functions (pick a need, search near a place, refund...) and the model must
call exactly one; the engine then does it, inside the bot's own rules.
The free tier allows a limited number of calls a minute, so a busy or
missing model falls back to FALLBACK_MODEL, and any failure to None: the
chat then carries on with its menus.
"""
import base64
import logging

import requests

logger = logging.getLogger(__name__)

API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
FALLBACK_MODEL = 'gemini-3.5-flash-lite'
# Per model: a slow model gives way to the fallback quickly, so a user never waits long.
TIMEOUT = (5, 15)
RETRY_STATUSES = {404, 429, 500, 503}


class GeminiClient:
    def __init__(self, api_key, model, *, http=None):
        self.api_key = api_key
        self.model = model
        self.http = http or requests.Session()

    def decide(self, *, system, prompt, functions, images=()):
        """Ask the model to call one of `functions` about `prompt`.

        functions: [{'name', 'description', 'parameters'}] in Gemini's schema
        (types in capitals: OBJECT, STRING, INTEGER, ...). images: (bytes,
        mimetype) pairs shown with the prompt. Returns (name, args) or None.
        """
        parts = [{'text': prompt}]
        for data, mimetype in images:
            parts.append({'inline_data': {'mime_type': mimetype, 'data': base64.b64encode(data).decode()}})
        body = {
            'systemInstruction': {'parts': [{'text': system}]},
            'contents': [{'role': 'user', 'parts': parts}],
            'tools': [{'functionDeclarations': functions}],
            'toolConfig': {'functionCallingConfig': {
                'mode': 'ANY', 'allowedFunctionNames': [function['name'] for function in functions],
            }},
            'generationConfig': {'temperature': 0.2, 'maxOutputTokens': 2048},
        }
        for model in dict.fromkeys([self.model, FALLBACK_MODEL]):
            answer = self._post(model, body)
            if answer is None:
                continue
            call = _function_call(answer)
            if call:
                return call
            logger.warning('Gemini %s answered without calling a function', model)
            return None
        return None

    def _post(self, model, body):
        try:
            res = self.http.post(
                API_URL.format(model=model), json=body, timeout=TIMEOUT,
                headers={'x-goog-api-key': self.api_key, 'Content-Type': 'application/json'},
            )
        except requests.RequestException as error:
            logger.warning('Gemini %s could not be reached: %s', model, type(error).__name__)
            return None
        if res.status_code != 200:
            # The key is in a header, never in the URL, so this log is safe.
            logger.warning('Gemini %s answered %s: %s', model, res.status_code, res.text[:300])
            return None if res.status_code in RETRY_STATUSES else {}
        try:
            return res.json()
        except ValueError:
            return None


def _function_call(answer):
    for candidate in answer.get('candidates', []):
        for part in candidate.get('content', {}).get('parts', []):
            call = part.get('functionCall')
            if call and call.get('name'):
                return call['name'], call.get('args') or {}
    return None


def build_gemini(config):
    return GeminiClient(config.gemini_api_key, config.gemini_model) if config.ai_ready else None
