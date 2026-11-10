"""The operators' dashboard at /ops/: money owed to each restroom operator
(paid out with PayPal Payouts), every payment, and the problem reports with
the AI's review and refunds. Staff only (the admin's login)."""
import json
import logging
import re
import secrets
from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from functools import partial

from django.contrib.admin.views.decorators import staff_member_required as _staff_member_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from bot import outbox
from bot.formatting import normalize_phone
from bot.handler import default_engine
from bot.models import ProblemReport
from bot.replies import Text
from payments.models import AccessPass, Payment, Payout, Refund
from payments.refunds import staff_refund
from restrooms.models import Restroom

from .cleaning import decide as decide_cleaning
from .cleaning import paid_text, pay_bonus
from .models import Attendant, Cleaner, CleaningCheck
from .payouts import PayoutError, only_real_money, earnings, last_payout, pay_out, payment_value, refresh_pending

logger = logging.getLogger(__name__)

# Staff log in on TapFlash's own login page, not Django's admin one.
staff_member_required = partial(_staff_member_required, login_url='gate-login')


def _judge_limit(request):
    """The shared one-click judge login gets a daily cap on actions that move sandbox money."""
    if request.user.get_username() != 'judge':
        return None
    key = f"ops:judge-money:{timezone.localdate().isoformat()}"
    cache.add(key, 0, timeout=26 * 3600)
    try:
        used = cache.incr(key)
    except ValueError:
        used = 1
    if used > settings.JUDGE_MONEY_ACTIONS_PER_DAY:
        return JsonResponse({'error': 'The demo login has reached today\'s limit for payouts and refunds. '
                                      'It resets tomorrow.'}, status=429)
    return None

AG_GRID = 'ops/vendor/ag-grid-community.min.js'  # served from this project's static files (AG Grid Community 36.2.0)
CSP = '; '.join([
    "default-src 'none'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'unsafe-inline' https://fonts.googleapis.com",
    'font-src https://fonts.gstatic.com data:',  # AG Grid's icons are an inline font
    "img-src 'self' data:",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def _secure(response):
    response['Content-Security-Policy'] = CSP
    response['Referrer-Policy'] = 'same-origin'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'no-store'
    return response


@staff_member_required
@ensure_csrf_cookie
def dashboard(request):
    config = settings.TAPFLASH
    engine = default_engine()
    return _secure(render(request, 'ops/dashboard.html', {
        'bot_name': config.bot_name,
        'ag_grid': static(AG_GRID),
        'currency': config.paypal_currency,
        'sandbox': not config.paypal_live,
        'paypal_on': bool(engine.checkout),
        'ai_on': bool(engine.triage),
    }))


def _money(amount, currency):
    return f'{currency} {amount:.2f}'


def _when(moment):
    return timezone.localtime(moment).isoformat() if moment else None


@staff_member_required
def data(request):
    config = settings.TAPFLASH
    checkout = default_engine().checkout
    # PayPal is asked about unfinished payouts at most once a minute, however
    # many dashboards are open.
    if checkout and cache.add('ops:refresh-pending', 1, timeout=60):
        refresh_pending(checkout, config)
    currency = config.paypal_currency
    restrooms = []
    for restroom in Restroom.objects.order_by('name'):
        due = earnings(restroom, config, real_only=only_real_money(checkout, config))
        last = last_payout(restroom)
        restrooms.append({
            'code': restroom.code, 'name': restroom.name, 'area': restroom.area,
            'operator': restroom.operator_name, 'email': restroom.payout_email,
            'share': restroom.operator_share_percent, 'visits': due.visits,
            'gross': float(due.gross), 'refunded': float(due.refunded), 'owed': float(due.owed),
            'lastPayout': _when(last.created_at) if last else None, 'active': restroom.is_active,
        })

    payments = []
    rows = (Payment.objects.select_related('paypal_order', 'access_pass').prefetch_related('refunds')
            .order_by('-created_at')[:400])
    for payment in rows:
        taken, back = payment_value(payment, config) if payment.reference else (0, 0)
        order = getattr(payment, 'paypal_order', None) if payment.provider == 'paypal' else None
        payments.append({
            'id': payment.pk, 'time': _when(payment.created_at), 'restroom': payment.facility_name,
            'method': payment.get_method_display(), 'local': _money(payment.total, payment.currency),
            'amount': float(taken), 'refunded': float(back),
            'status': 'held' if (order and order.authorization_id and not order.capture_id and payment.status == 'succeeded') else payment.status,
            'reference': payment.reference or '', 'provider': payment.provider,
            'payer': order.payer_email if order else (payment.phone or ''),
            'reason': payment.failure_reason,
            'entered': _when(getattr(getattr(payment, 'access_pass', None), 'checked_in_at', None)),
        })

    reports = []
    rows = (ProblemReport.objects.select_related('payment').prefetch_related('refunds', 'payment__refunds')
            .order_by('-created_at')[:200])
    for report in rows:
        paid = report.payment
        refund = next((r for r in (paid.refunds.all() if paid else report.refunds.all())
                       if r.status != Refund.Status.FAILED), None)
        reports.append({
            'id': report.pk, 'time': _when(report.created_at),
            'restroom': report.facility_name or 'No restroom chosen',
            'message': report.message, 'status': report.get_status_display(), 'review': report.staff_notes,
            'refund': _money(refund.amount, refund.currency) if refund else '',
            'refundBy': refund.get_decided_by_display() if refund else '',
            'refundable': bool(paid and paid.status == Payment.Status.SUCCEEDED and refund is None),
        })

    payouts = [{
        'id': payout.pk, 'time': _when(payout.created_at), 'restroom': payout.restroom.name if payout.restroom else '',
        'receiver': payout.receiver, 'amount': float(payout.amount), 'currency': payout.currency,
        'visits': payout.visits, 'status': payout.status, 'batch': payout.batch_id,
        'kind': 'Cleaning bonus' if payout.kind == Payout.Kind.CLEANING else 'Operator share',
    } for payout in Payout.objects.select_related('restroom').order_by('-created_at')[:100]]

    cleaning = []
    checks = (CleaningCheck.objects.select_related('cleaner', 'restroom', 'payout').defer('photo')
              .order_by('-created_at')[:200])
    for check in checks:
        payout = check.payout
        cleaning.append({
            'id': check.pk, 'time': _when(check.created_at), 'restroom': check.restroom.name,
            'cleaner': check.cleaner.name, 'status': check.status, 'score': check.score,
            'note': check.ai_note, 'decidedBy': 'Gemini' if check.decided_by == 'ai' else check.decided_by,
            'photo': reverse('ops-photo', args=[check.pk]),
            'bonus': float(payout.amount) if payout else float(check.cleaner.bonus),
            'payout': payout.status if payout else '', 'batch': (payout.batch_id if payout else ''),
        })

    cleaners = [{
        'id': c.pk, 'name': c.name, 'phone': c.phone, 'email': c.payout_email, 'restroom': c.restroom.name,
        'restroomCode': c.restroom.code, 'bonus': float(c.bonus), 'active': c.is_active,
    } for c in Cleaner.objects.select_related('restroom').order_by('-is_active', 'name')]
    attendants = [{
        'id': a.pk, 'username': a.user.get_username(), 'restroom': a.restroom.name, 'restroomCode': a.restroom.code,
        'active': a.user.is_active,
        'lastLogin': _when(a.user.last_login),
    } for a in Attendant.objects.select_related('user', 'restroom').order_by('restroom__name', 'user__username')]

    since = timezone.now() - timedelta(hours=24)
    today = [row for row in payments if row['status'] in ('succeeded', 'refunded') and row['time']
             and row['time'] >= _when(since)]
    summary = {
        'visits': len(today),
        'taken': round(sum(row['amount'] for row in today), 2),
        'refunds': sum(1 for row in today if row['refunded']),
        'entered': sum(1 for row in today if row['entered']),
        'owed': round(sum(row['owed'] for row in restrooms), 2),
        'operators': sum(1 for row in restrooms if row['owed'] > 0),
        'currency': currency,
    }
    from payments.models import VisitPass
    passes = [{'id': v.pk, 'time': _when(v.paid_at or v.created_at), 'buyer': v.user_id[-6:], 'visits': v.visits_total,
               'left': v.visits_left, 'price': float(v.price), 'currency': v.currency,
               'fee': float(v.paypal_fee) if v.paypal_fee is not None else None,
               'feePerVisit': float(v.paypal_fee / v.visits_total) if v.paypal_fee is not None else None,
               'status': v.get_status_display(), 'capture': v.capture_id}
              for v in VisitPass.objects.filter(status=VisitPass.Status.PAID)[:200]]
    response = JsonResponse({'summary': summary, 'restrooms': restrooms, 'payments': payments, 'passes': passes,
                             'reports': reports, 'payouts': payouts, 'cleaning': cleaning,
                             'cleaners': cleaners, 'attendants': attendants})
    response['Cache-Control'] = 'no-store'
    return response


@staff_member_required
@require_POST
def payout(request):
    if (limited := _judge_limit(request)) is not None:
        return limited
    config = settings.TAPFLASH
    try:
        code = str(json.loads(request.body.decode('utf-8') or '{}').get('restroom', ''))[:64]
    except (ValueError, AttributeError):
        return JsonResponse({'error': 'Send JSON like {"restroom": "<code>"}.'}, status=400)
    restroom = Restroom.objects.filter(code=code).first()
    if restroom is None:
        return JsonResponse({'error': 'That restroom does not exist.'}, status=404)
    try:
        sent = pay_out(restroom, checkout=default_engine().checkout, config=config, now=timezone.now())
    except PayoutError as error:
        return JsonResponse({'error': str(error)}, status=409)
    simulated = sent.status == 'SIMULATED'
    return JsonResponse({'message': (
        f'Sent {sent.currency} {sent.amount:.2f} to {sent.receiver} for {sent.visits} '
        f'visit{"s" if sent.visits != 1 else ""}' + (' (simulated: PayPal is not set up).' if simulated
                                                     else f'. PayPal batch {sent.batch_id}.')
    )})


@staff_member_required
@require_POST
def refund(request):
    """Staff refund the visit a problem report is about, through PayPal for
    PayPal visits. The user is told in the chat."""
    if (limited := _judge_limit(request)) is not None:
        return limited
    config = settings.TAPFLASH
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
        report_id, share = int(body.get('report')), str(body.get('share', 'full'))
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'error': 'Send JSON like {"report": 12, "share": "full"}.'}, status=400)
    report = ProblemReport.objects.select_related('payment').filter(pk=report_id).first()
    if report is None or report.payment is None:
        return JsonResponse({'error': 'That report is not about a paid visit.'}, status=404)
    engine = default_engine()
    payment, now = report.payment, timezone.now()
    # PayPal is not called inside a database lock. Two staff clicking at once
    # send the same PayPal request id, so PayPal refunds once.
    if not cache.add(f'ops:refund:{payment.pk}', 1, timeout=60):
        return JsonResponse({'error': 'This visit is being refunded already.'}, status=409)
    try:
        given, why = staff_refund(payment=payment, report=report, share_name=share, checkout=engine.checkout,
                                  config=config, now=now, staff_name=request.user.get_username())
    finally:
        cache.delete(f'ops:refund:{payment.pk}')
    if given is None:
        return JsonResponse({'error': f'Not refunded: {why}.'}, status=409)
    if share == 'full':
        AccessPass.objects.filter(payment=payment, expires_at__gt=now).update(expires_at=now)
    report.status, report.resolved_at = ProblemReport.Status.RESOLVED, now
    report.staff_notes = (report.staff_notes + f'\nRefunded {given.amount} {given.currency} by staff '
                          f'({given.provider_refund_id}).').strip()
    report.save(update_fields=['status', 'resolved_at', 'staff_notes'])
    try:
        outbox.notify(payment.user_id, [Text(f'About your report on {payment.facility_name}: '
                                               + engine.msg.refunded(given, payment))])
    except Exception:
        logger.exception('Could not tell %s about the staff refund', payment.user_id)
    return JsonResponse({'message': f'Refunded {given.currency} {given.amount:.2f} for {payment.facility_name} '
                                    f'({given.provider_refund_id}). The user was told in the chat.'})


@staff_member_required
@require_POST
def visit(request):
    """Staff act on one visit from the payments list: "refund" a paid (captured) visit, or
    "release" a held one, so the visitor is never charged. The user is told in the chat."""
    if (limited := _judge_limit(request)) is not None:
        return limited
    config = settings.TAPFLASH
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
        payment_id, action = int(body.get('payment')), str(body.get('action', ''))
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'error': 'Send JSON like {"payment": 12, "action": "refund"}.'}, status=400)
    payment = Payment.objects.select_related('paypal_order').filter(pk=payment_id).first()
    if payment is None or action not in ('refund', 'release'):
        return JsonResponse({'error': 'Unknown visit or action.'}, status=404)
    order = getattr(payment, 'paypal_order', None) if payment.provider == 'paypal' else None
    held = bool(order and order.authorization_id and not order.capture_id)
    if held != (action == 'release'):
        return JsonResponse({'error': 'This visit is held: release it.' if held
                             else 'Only a held visit can be released. Use Refund for a paid one.'}, status=409)
    engine, now = default_engine(), timezone.now()
    if not cache.add(f'ops:refund:{payment.pk}', 1, timeout=60):
        return JsonResponse({'error': 'This visit is being refunded already.'}, status=409)
    try:
        given, why = staff_refund(payment=payment, report=None, share_name='full', checkout=engine.checkout,
                                  config=config, now=now, staff_name=request.user.get_username())
    finally:
        cache.delete(f'ops:refund:{payment.pk}')
    if given is None:
        return JsonResponse({'error': f'Not done: {why}.'}, status=409)
    AccessPass.objects.filter(payment=payment, expires_at__gt=now).update(expires_at=now)
    word = 'released' if action == 'release' else 'refunded'
    try:
        outbox.notify(payment.user_id, [Text(
            f'About your visit to {payment.facility_name}: staff {word} it. '
            + ('You were not charged.' if action == 'release' else engine.msg.refunded(given, payment)))])
    except Exception:
        logger.exception('Could not tell %s about the staff action', payment.user_id)
    return JsonResponse({'message': f'{word.capitalize()} {given.currency} {given.amount:.2f} for '
                                    f'{payment.facility_name}. The user was told in the chat.'})


@staff_member_required
def photo(request, check_id):
    """A cleaning photo, for staff only."""
    check = get_object_or_404(CleaningCheck.objects.only('photo', 'photo_type'), pk=check_id)
    response = HttpResponse(bytes(check.photo), content_type=check.photo_type)
    response['Cache-Control'] = 'private, max-age=3600'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'"
    return response


@staff_member_required
@require_POST
def cleaning(request):
    """Staff approve (and pay), reject, or pay again a cleaning check. The
    cleaner is told in the chat."""
    if (limited := _judge_limit(request)) is not None:
        return limited
    config = settings.TAPFLASH
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
        check_id, action = int(body.get('check')), str(body.get('action', ''))
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'error': 'Send JSON like {"check": 3, "action": "approve"}.'}, status=400)
    check = CleaningCheck.objects.select_related('cleaner', 'restroom').filter(pk=check_id).first()
    if check is None or action not in ('approve', 'reject', 'pay'):
        return JsonResponse({'error': 'Unknown cleaning check or action.'}, status=404)
    engine = default_engine()
    if action == 'pay':
        if check.status != CleaningCheck.Status.APPROVED or check.payout_id:
            return JsonResponse({'error': 'Only an approved check whose bonus was not sent can be paid.'}, status=409)
        result = pay_bonus(check, checkout=engine.checkout, config=config, now=timezone.now())
        for_staff, for_cleaner = (f'Not sent: {result}' if isinstance(result, PayoutError) else
                                  f'Sent {result.currency} {result.amount:.2f} to {result.receiver}.'), paid_text(result)
    else:
        try:
            for_staff, for_cleaner = decide_cleaning(check, approve=action == 'approve',
                                                     staff_name=request.user.get_username(),
                                                     checkout=engine.checkout, config=config)
        except ValueError as error:
            return JsonResponse({'error': str(error)}, status=409)
    try:
        outbox.push(check.user_id, check.channel, [Text(f'{config.bot_name} cleaning check, '
                                                        f'{check.restroom.name}: {for_cleaner}')])
    except Exception:
        logger.exception('Could not tell cleaner %s', check.cleaner_id)
    return JsonResponse({'message': for_staff})


@staff_member_required
@require_POST
def edit_restroom(request):
    """Change an operator's PayPal email or share from the grid."""
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
        code, field, value = str(body['restroom'])[:64], str(body['field']), body['value']
    except (ValueError, KeyError, TypeError, AttributeError):
        return JsonResponse({'error': 'Send JSON like {"restroom": "<code>", "field": "email", "value": "..."}.'},
                            status=400)
    restroom = Restroom.objects.filter(code=code).first()
    if restroom is None:
        return JsonResponse({'error': 'That restroom does not exist.'}, status=404)
    if field == 'email':
        email = str(value or '').strip()[:254]
        try:
            if email:
                validate_email(email)
        except ValidationError:
            return JsonResponse({'error': f'"{email}" is not an email address.'}, status=400)
        restroom.payout_email = email
        restroom.save(update_fields=['payout_email'])
        return JsonResponse({'message': f"{restroom.name}: payouts now go to {email or 'nobody'}."})
    if field == 'share':
        try:
            share = int(value)
        except (TypeError, ValueError):
            share = -1
        if not 0 <= share <= 100:
            return JsonResponse({'error': 'The share is a whole number from 0 to 100.'}, status=400)
        restroom.operator_share_percent = share
        restroom.save(update_fields=['operator_share_percent'])
        return JsonResponse({'message': f"{restroom.name}: the operator now gets {share}% of each visit."})
    return JsonResponse({'error': 'Only the email and the share can be changed here.'}, status=400)


def _body(request):
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


@staff_member_required
@require_POST
def save_cleaner(request):
    """Add a cleaner, change one, or switch one on or off."""
    config = settings.TAPFLASH
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'Send a JSON object.'}, status=400)
    cleaner = Cleaner.objects.filter(pk=body.get('id')).first() if body.get('id') else Cleaner()
    if body.get('id') and cleaner is None:
        return JsonResponse({'error': 'That cleaner does not exist.'}, status=404)
    if 'active' in body and len(body) == 2 and cleaner.pk:  # {"id", "active"}: on or off only
        cleaner.is_active = bool(body['active'])
        cleaner.save(update_fields=['is_active'])
        return JsonResponse({'message': f'{cleaner.name} is {"on" if cleaner.is_active else "off"}.'})
    name = str(body.get('name', cleaner.name or '')).strip()[:80]
    phone = normalize_phone(str(body.get('phone', cleaner.phone or '')), config.country_code)
    email = str(body.get('email', cleaner.payout_email or '')).strip()[:254]
    code = body.get('restroom') or (cleaner.restroom.code if cleaner.pk else '')
    restroom = Restroom.objects.filter(code=code).first() if code else None
    try:
        bonus = Decimal(str(body.get('bonus', cleaner.bonus or '1.00')))
    except (InvalidOperation, ValueError):
        bonus = Decimal(-1)
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({'error': 'Add the PayPal email the bonus goes to.'}, status=400)
    if not name:
        return JsonResponse({'error': "Add the cleaner's name."}, status=400)
    if not phone:
        return JsonResponse({'error': 'Add the WhatsApp number with the country code, e.g. +254712345678.'}, status=400)
    if restroom is None:
        return JsonResponse({'error': 'Choose the restroom they clean.'}, status=400)
    if not Decimal('0.01') <= bonus <= Decimal('100'):
        return JsonResponse({'error': 'The bonus is between 0.01 and 100.'}, status=400)
    if Cleaner.objects.filter(phone=phone).exclude(pk=cleaner.pk).exists():
        return JsonResponse({'error': f'{phone} is already a cleaner.'}, status=409)
    cleaner.name, cleaner.phone, cleaner.payout_email = name, phone, email
    cleaner.restroom, cleaner.bonus = restroom, bonus.quantize(Decimal('0.01'))
    adding = cleaner.pk is None
    cleaner.save()
    return JsonResponse({'message': f'{"Added" if adding else "Saved"} {name}: photos from {phone} '
                                    f'earn {config.paypal_currency} {cleaner.bonus} at {restroom.name}.'})


@staff_member_required
@require_POST
def save_attendant(request):
    """Add an attendant with a login for /gate/, give them a new password, or remove them."""
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'Send a JSON object.'}, status=400)
    action = body.get('action', 'add')
    if action in ('password', 'remove'):
        attendant = Attendant.objects.select_related('user').filter(pk=body.get('id')).first()
        if attendant is None:
            return JsonResponse({'error': 'That attendant does not exist.'}, status=404)
        user = attendant.user
        if user.is_staff:
            return JsonResponse({'error': 'Change staff logins in the admin.'}, status=400)
        if action == 'remove':
            user.is_active = False
            user.save(update_fields=['is_active'])
            attendant.delete()
            return JsonResponse({'message': f'{user.get_username()} can no longer log in.'})
        password = secrets.token_urlsafe(6)
        user.set_password(password)
        user.is_active = True
        user.save()
        return JsonResponse({'message': f'New password for {user.get_username()}: {password}', 'password': password})
    username = str(body.get('username', '')).strip().lower()[:30]
    restroom = Restroom.objects.filter(code=body.get('restroom')).first()
    if not re.fullmatch(r'[a-z0-9_.-]{3,30}', username):
        return JsonResponse({'error': 'A username of 3 to 30 letters, numbers, dots or dashes.'}, status=400)
    if restroom is None:
        return JsonResponse({'error': 'Choose the restroom they work at.'}, status=400)
    User = get_user_model()
    if User.objects.filter(username=username).exists():
        return JsonResponse({'error': f'The username {username} is taken.'}, status=409)
    password = secrets.token_urlsafe(6)
    user = User.objects.create_user(username=username, password=password)
    Attendant.objects.create(user=user, restroom=restroom)
    return JsonResponse({'message': f'Added {username} at {restroom.name}. Password: {password} '
                                    f'(they log in at /gate/).', 'password': password})
