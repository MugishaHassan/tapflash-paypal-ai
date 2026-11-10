"""Cleaning bonuses: paid to a cleaner the moment their work is approved.

A cleaner (added in the admin with their WhatsApp number and PayPal email)
sends a photo of the restroom they just cleaned. Gemini judges it; the rules
here decide, and the AI can't change them:

- the same photo is never paid twice: an exact copy (SHA-256), or a re-saved or
  re-sent copy of an earlier photo or of the restroom's own photo (perceptual hash);
- at most one bonus every CLEANING_BONUS_HOURS, and CLEANING_MAX_PER_DAY a day;
- a photo Gemini doesn't recognise as this restroom (compared with the
  restroom's own photo) waits for staff;
- only a confident "clean" (score 4 or 5) is paid at once; a 3, or no AI,
  waits for staff on /ops/; a 1 or 2, or a photo that isn't a restroom, is
  not approved and the cleaner is told what is left to do.

An approved check is paid straight away with PayPal Payouts (simulated without
PayPal), using the same safe sending as operator payouts.
"""
import hashlib
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from bot.formatting import format_money, normalize_phone
from payments.models import Payout

from .models import Cleaner, CleaningCheck
from .payouts import PayoutError, send

logger = logging.getLogger(__name__)

MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTO_TYPES = ('image/jpeg', 'image/png', 'image/webp')
PAY_AT = 4  # Gemini's score from which a cleaning is paid at once
REVIEW_AT = 3


def cleaner_for(phone, config):
    number = normalize_phone(phone or '', config.country_code) if phone else None
    if not number:
        return None
    return Cleaner.objects.select_related('restroom').filter(phone=number, is_active=True).first()


LOOK_DISTANCE = 4  # out of 64 bits: re-saved or recompressed copies, not a new photo of the same room


def photo_look(data):
    """A 64-bit difference hash of the picture as 16 hex digits ('' if it can't be read).
    Re-saving, recompressing or resizing a photo barely changes it."""
    try:
        from io import BytesIO

        from PIL import Image
        with Image.open(BytesIO(data)) as image:
            small = image.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(small.getdata())
    except Exception:
        return ''
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return f'{bits:016x}'


def looks_alike(a, b):
    return bool(a and b) and bin(int(a, 16) ^ int(b, 16)).count('1') <= LOOK_DISTANCE


def seen_before(look, restroom):
    """Whether the photo is a copy of the restroom's own photo or of an earlier cleaning photo."""
    if not look:
        return False
    reference = reference_photo(restroom)
    if reference and looks_alike(look, photo_look(reference[0])):
        return True
    earlier = (CleaningCheck.objects.filter(restroom=restroom).exclude(photo_look='')
               .order_by('-created_at').values_list('photo_look', flat=True)[:500])
    return any(looks_alike(look, other) for other in earlier)


def reference_photo(restroom):
    """The restroom's own photo as (bytes, mimetype), or None."""
    if not restroom.photo:
        return None
    try:
        with restroom.photo.open('rb') as image:
            data = image.read(MAX_PHOTO_BYTES + 1)
    except (OSError, ValueError):
        return None
    if len(data) > MAX_PHOTO_BYTES:
        return None
    name = restroom.photo.name.lower()
    return data, 'image/png' if name.endswith('.png') else 'image/webp' if name.endswith('.webp') else 'image/jpeg'


def check_photo(*, cleaner, user_id, channel, photo, mimetype, judge, checkout, config, now=None):
    """Handle one cleaning photo. Returns the text to send the cleaner."""
    now = now or timezone.now()
    if mimetype not in PHOTO_TYPES or not photo or len(photo) > MAX_PHOTO_BYTES:
        return 'Please send the cleaning photo as a normal photo (JPEG or PNG, under 5 MB).'
    digest = hashlib.sha256(photo).hexdigest()
    look = photo_look(photo)
    if CleaningCheck.objects.filter(photo_hash=digest).exists() or seen_before(look, cleaner.restroom):
        return 'This photo was sent before. Please take a new photo of today\'s cleaning.'
    counted = cleaner.checks.exclude(status=CleaningCheck.Status.REJECTED)
    last = counted.filter(created_at__gt=now - timedelta(hours=config.cleaning_bonus_hours)).order_by('-created_at').first()
    if last:
        ready = timezone.localtime(last.created_at + timedelta(hours=config.cleaning_bonus_hours))
        return (f'Thank you! Your last cleaning was checked at {timezone.localtime(last.created_at):%H:%M}. '
                f'The next bonus can be earned from {ready:%H:%M}.')
    if counted.filter(created_at__gt=now - timedelta(days=1)).count() >= config.cleaning_max_per_day:
        return 'Thank you! You have earned the most bonuses for today. Keep up the good work.'

    verdict = None
    if judge:
        try:
            verdict = judge.judge(photo=photo, mimetype=mimetype, restroom_name=cleaner.restroom.name,
                                  reference=reference_photo(cleaner.restroom))
        except Exception:
            logger.exception('The AI cleaning check failed')
    if verdict is None:
        status, score, note = CleaningCheck.Status.REVIEW, None, 'Not checked by the AI.'
    elif not verdict['is_restroom']:
        status, score, note = CleaningCheck.Status.REJECTED, verdict['score'], 'Not a photo of a restroom.'
    elif not verdict.get('same_restroom', True):
        # Clean, maybe, but not clearly this restroom: a supervisor decides.
        status, score, note = (CleaningCheck.Status.REVIEW, verdict['score'],
                               'May not be this restroom (compared with its reference photo).')
    elif verdict['score'] >= PAY_AT:
        status, score, note = CleaningCheck.Status.APPROVED, verdict['score'], verdict['issues']
    elif verdict['score'] >= REVIEW_AT:
        status, score, note = CleaningCheck.Status.REVIEW, verdict['score'], verdict['issues']
    else:
        status, score, note = CleaningCheck.Status.REJECTED, verdict['score'], verdict['issues']

    check = CleaningCheck.objects.create(
        cleaner=cleaner, restroom=cleaner.restroom, user_id=user_id, channel=channel, photo=photo,
        photo_type=mimetype, photo_hash=digest, photo_look=look, status=status, score=score, ai_note=note[:300],
        decided_by='ai' if verdict else '', created_at=now,
    )
    said = f"{verdict['reply']} " if verdict and verdict['reply'] else ''
    if status == CleaningCheck.Status.APPROVED:
        return said + paid_text(pay_bonus(check, checkout=checkout, config=config, now=now))
    if status == CleaningCheck.Status.REVIEW:
        return said + 'A supervisor will check your photo today and send your bonus if it is approved.'
    if verdict and not verdict['is_restroom']:
        return 'This doesn\'t look like a photo of the restroom. Please send a photo of the cleaned restroom.'
    left = f" Still to do: {verdict['issues']}." if verdict and verdict['issues'] else ''
    return said + f'Not approved yet.{left} Send a new photo when it is done.'


def pay_bonus(check, *, checkout, config, now):
    """Pay an approved check once. Returns the Payout, or a PayoutError."""
    with transaction.atomic():
        check = CleaningCheck.objects.select_for_update().select_related('cleaner', 'restroom').get(pk=check.pk)
        if check.payout_id:
            return check.payout
        cleaner = check.cleaner
        payout = Payout.objects.create(
            sender_batch_id=f'tf-clean-{check.pk}', kind=Payout.Kind.CLEANING, restroom=check.restroom,
            receiver=cleaner.payout_email, amount=cleaner.bonus, currency=config.paypal_currency,
            note=f'{config.bot_name}: cleaning bonus for {check.restroom.name}'[:255],
            status='PENDING' if checkout else 'SIMULATED', created_at=now,
        )
        check.payout = payout
        check.save(update_fields=['payout'])
    try:
        return send(payout, checkout=checkout, config=config)
    except PayoutError as error:
        logger.warning('Cleaning bonus %s not sent: %s', check.pk, error)
        return error


def paid_text(result):
    if isinstance(result, PayoutError):
        return 'Approved! Your bonus could not be sent right now; a supervisor will send it today.'
    test = ' (test mode: no real money moves)' if result.status == 'SIMULATED' else ''
    return (f'Approved! {format_money(result.amount, result.currency)} is on its way to your PayPal '
            f'({result.receiver}){test}. Reference *{result.batch_id or result.sender_batch_id}*.')


def decide(check, *, approve, staff_name, checkout, config, now=None):
    """Staff approve (and pay) or reject a check that was waiting. Returns
    (text for staff, text for the cleaner) or raises ValueError."""
    now = now or timezone.now()
    with transaction.atomic():
        check = CleaningCheck.objects.select_for_update().get(pk=check.pk)
        if check.status != CleaningCheck.Status.REVIEW:
            raise ValueError('This check was already decided.')
        check.status = CleaningCheck.Status.APPROVED if approve else CleaningCheck.Status.REJECTED
        check.decided_by = staff_name[:80]
        check.save(update_fields=['status', 'decided_by'])
    if not approve:
        return 'Marked as not approved.', 'A supervisor checked your photo: not approved this time. Send a new photo after cleaning.'
    result = pay_bonus(check, checkout=checkout, config=config, now=now)
    if isinstance(result, PayoutError):
        return f'Approved, but the bonus was not sent: {result}', paid_text(result)
    return (f'Approved and sent {result.currency} {result.amount:.2f} to {result.receiver}.',
            'A supervisor approved your cleaning. ' + paid_text(result))
