"""Simple limits kept in the shared cache (both gunicorn workers see them):
messages per minute from one address, and wrong passwords before a lockout."""
from django.conf import settings
from django.core.cache import cache


def client_ip(request):
    """The visitor's address; behind nginx (BEHIND_PROXY) it comes from X-Real-IP."""
    forwarded = request.headers.get('X-Real-IP') if getattr(settings, 'SECURE_PROXY_SSL_HEADER', None) else None
    return forwarded or request.META.get('REMOTE_ADDR', '')


def hit(key, seconds):
    """Count one more for `key` in a window of `seconds` (from the first hit). Returns the count."""
    cache.add(key, 0, seconds)
    try:
        return cache.incr(key)
    except ValueError:  # the window ended between add and incr
        cache.set(key, 1, seconds)
        return 1


def count(key):
    return cache.get(key, 0)


def clear(key):
    cache.delete(key)
