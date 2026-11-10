"""Demo data, made the honest way.

Adds the sample Nairobi CBD restrooms with their operators, features and
photos, then runs scripted visits through the real bot (simulated M-Pesa
and Airtel Money, no AI calls), so every payment, pass and report on the
operators' dashboard comes from the same code a real visitor goes through.

    python manage.py seed_demo            # today's visits
    python manage.py seed_demo --reset    # start again from an empty demo
"""
from dataclasses import replace
from datetime import datetime, time, timedelta
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.files import File
from django.core.management import BaseCommand, call_command
from django.utils import timezone

from bot.ai.cleaning import CleaningJudge
from bot.ai.gemini import build_gemini
from bot.engine import Engine, Incoming
from bot.handler import handle_message
from bot.models import Conversation, OutgoingReply, ProblemReport
from ops.cleaning import check_photo
from ops.models import Cleaner, CleaningCheck
from payments.models import AccessPass, Payment, PayPalOrder, Payout, Refund
from restrooms.geo import Location
from restrooms.models import Restroom

SAMPLE = Path(settings.BASE_DIR) / 'restrooms' / 'sample_restrooms.json'
PHOTOS = Path(settings.BASE_DIR) / 'restrooms' / 'demo_photos'

# Who runs each sample restroom (groups, not people), and what it offers.
# The emails are placeholders: put your PayPal sandbox personal accounts'
# emails here (or in the admin) to see real sandbox payouts arrive.
OPERATORS = {
    'nbi-city-market': ('City Market Traders Sacco', 'citymarket.ops@example.com', 4, True, False, True, '2.jpg'),
    'nbi-kenyatta-ave': ('Kenyatta Avenue Attendants', 'kenyatta.ops@example.com', 5, True, True, True, '1.jpg'),
    'nbi-jeevanjee': ('Jeevanjee Gardens Youth Group', 'jeevanjee.ops@example.com', 3, False, False, False, '4.jpg'),
    'nbi-railways': ('Railways Terminus Cleaners', 'railways.ops@example.com', 4, True, True, False, '5.jpg'),
    'nbi-uhuru-park': ('Uhuru Park Friends', 'uhurupark.ops@example.com', 2, False, False, True, '6.jpg'),
}

# (restroom, need number, method number, report or '')
# Needs: 1 clean, 2 convenient, 3 accessible, 4 disabled, 5 baby.
# Methods: 1 M-Pesa, 2 Airtel Money.
VISITS = [
    ('nbi-kenyatta-ave', 1, 1, ''), ('nbi-city-market', 2, 1, ''), ('nbi-railways', 4, 2, ''),
    ('nbi-kenyatta-ave', 5, 1, ''), ('nbi-jeevanjee', 2, 1, 'There was no water in the sinks.'),
    ('nbi-city-market', 3, 2, ''), ('nbi-uhuru-park', 2, 1, ''), ('nbi-kenyatta-ave', 2, 1, ''),
    ('nbi-railways', 2, 1, 'The door code worked but the attendant asked me to pay again.'),
    ('nbi-city-market', 1, 1, ''), ('nbi-kenyatta-ave', 3, 2, ''), ('nbi-uhuru-park', 5, 1, ''),
    ('nbi-railways', 1, 1, ''), ('nbi-jeevanjee', 2, 2, ''),
]


# Sample cleaners (inactive, so no real WhatsApp number can match them) and the
# photo each sends. Gemini reviews them when a key is set; without one they wait for staff on /ops/.
CLEANERS = [
    ('nbi-kenyatta-ave', 'Wanjiru', '+254700000901', 'wanjiru.cleaner@example.com', '3.jpg'),
    ('nbi-city-market', 'Otieno', '+254700000902', 'otieno.cleaner@example.com', '1.jpg'),
    ('nbi-railways', 'Achieng', '+254700000903', 'achieng.cleaner@example.com', '5.jpg'),
]


class Command(BaseCommand):
    help = 'Add the sample Nairobi restrooms with operators, and run scripted visits through the bot.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Delete the demo visits and payouts first.')
        parser.add_argument('--visits', type=int, default=8,
                            help='How many simulated mobile money visits to add (real PayPal visits stand out more with fewer).')
        parser.add_argument('--no-ai', action='store_true',
                            help="Don't ask Gemini to review the sample reports and photos, even with a key.")

    def handle(self, *args, reset, visits, no_ai, **options):
        if reset:
            self.reset()
        call_command('import_restrooms', SAMPLE, stdout=StringIO())
        for code, (operator, email, stars, accessible, disabled, baby, photo) in OPERATORS.items():
            row = Restroom.objects.get(code=code)
            # Open all day on the demo, so judges in any time zone see several restrooms;
            # Uhuru Park keeps daytime hours to show that closed restrooms are left out.
            if code != 'nbi-uhuru-park':
                row.opening_hours = '24 hours'
            # DEMO_PAYOUT_EMAIL (a sandbox personal account) lets you see every demo payout arrive.
            row.operator_name = operator
            row.payout_email = settings.TAPFLASH.demo_payout_email or row.payout_email or email
            row.cleanliness, row.accessible, row.disabled_toilet, row.baby_changing = stars, accessible, disabled, baby
            # The README says these are sample restrooms; on screen the names read cleaner without the suffix.
            row.name = row.name.replace(' (sample)', '')
            row.save()
            if not row.photo and (PHOTOS / photo).exists():
                with (PHOTOS / photo).open('rb') as image:
                    row.photo.save(f'{code}.jpg', File(image))

        # With a Gemini key, the sample problem reports and cleaning photos are reviewed by the real
        # Gemini, through the same code as live ones; the visits themselves need no AI.
        use_ai = bool(settings.TAPFLASH.gemini_api_key) and not no_ai
        # The sample visits pay with simulated mobile money, even where the chat offers PayPal only.
        config = replace(settings.TAPFLASH, demo_restrooms_near_user=False, demo_restrooms_fallback=False,
                         images=False, paypal_only=False, gemini_api_key=settings.TAPFLASH.gemini_api_key if use_ai else '')
        chosen = [v for v in VISITS if v[3]] + [v for v in VISITS if not v[3]][:max(visits - 2, 0)]
        moments = self.visit_times(config, len(chosen))
        for number, ((code, need, method, report), moment) in enumerate(zip(chosen, moments), start=1):
            self.visit(config, number, Restroom.objects.get(code=code), need, method, report, moment)
        self.cleaning(config, moments[-1] if moments else timezone.now(), use_ai)
        self.stdout.write(self.style.SUCCESS(
            f'{len(OPERATORS)} restrooms with operators, {len(chosen)} demo visits and '
            f'{len(CLEANERS)} cleaning photos added. '
            'Open /ops/ to see them.'
        ))

    @staticmethod
    def visit_times(config, count):
        """Daytime moments today in Nairobi (yesterday if it's still early)."""
        zone = ZoneInfo(config.timezone)
        now = timezone.now().astimezone(zone)
        day = now.date() if now.hour >= 10 else now.date() - timedelta(days=1)
        start = datetime.combine(day, time(8, 5), zone)
        end = min(datetime.combine(day, time(17, 30), zone), now - timedelta(minutes=20))
        step = (end - start) / max(count, 1)
        return [start + step * index for index in range(count)]

    def visit(self, config, number, restroom, need, method, report, moment):
        clock = {'now': moment}
        engine = Engine(config, clock=lambda: clock['now'])
        user, phone = f'demo:visitor-{number}', f'+2547000{number:05d}'

        def say(text=None, location=None):
            clock['now'] += timedelta(seconds=40)
            incoming = (Incoming(user, 'location', location=location, phone=phone) if location
                        else Incoming(user, 'text', text=text, phone=phone))
            handle_message(incoming, lambda reply: None, engine)

        # Standing a few steps from the restroom, so it is the nearest one.
        near = Location(restroom.latitude + 0.0004, restroom.longitude - 0.0003)
        say('hi')
        say(str(need))
        say(location=near)
        say('1')  # the nearest restroom (with this need)
        say('1')  # pay
        say(str(method))
        say('1')  # use my WhatsApp number
        say('1')  # authorize
        say('1')  # I have arrived
        if report:
            say('2')  # report a problem
            say(report)
        else:
            say('1')  # unlock
            say('3')  # done

    def cleaning(self, config, moment, use_ai=False):
        # The web test chat always sends this number, so anyone can try a cleaning check there.
        # Change the email in the admin to a sandbox personal account to see the payout arrive.
        demo_email = settings.TAPFLASH.demo_payout_email
        tester, _ = Cleaner.objects.get_or_create(phone='+254712345678', defaults={
            'name': 'Web chat tester', 'payout_email': demo_email or 'webchat.cleaner@example.com',
            'restroom': Restroom.objects.get(code='nbi-kenyatta-ave'),
        })
        if demo_email and tester.payout_email != demo_email:
            tester.payout_email = demo_email
            tester.save(update_fields=['payout_email'])
        judge = CleaningJudge(build_gemini(config)) if use_ai and build_gemini(config) else None
        for number, (code, name, phone, email, photo) in enumerate(CLEANERS):
            cleaner, _ = Cleaner.objects.update_or_create(phone=phone, defaults={
                'name': name, 'payout_email': settings.TAPFLASH.demo_payout_email or email, 'restroom': Restroom.objects.get(code=code), 'is_active': False,
            })
            if cleaner.checks.exists() or not (PHOTOS / photo).exists():
                continue
            check_photo(cleaner=cleaner, user_id=f'demo:cleaner-{number}', channel='web',
                        photo=(PHOTOS / photo).read_bytes(), mimetype='image/jpeg', judge=judge, checkout=None,
                        config=config, now=moment - timedelta(minutes=17 * number))

    def reset(self):
        """Deletes only demo data: visits by demo users, the sample cleaners' checks, and payouts that
        covered nothing but demo visits. Real (sandbox) visits, refunds and payouts are kept."""
        demo = Payment.objects.filter(user_id__startswith='demo:')
        sample_checks = CleaningCheck.objects.filter(cleaner__phone__in=[row[2] for row in CLEANERS])
        real_visits = Payment.objects.exclude(user_id__startswith='demo:')
        demo_payouts = list(
            Payout.objects.filter(restroom__code__in=OPERATORS, kind=Payout.Kind.EARNINGS)
            .exclude(visits_paid__in=real_visits).exclude(refunds_taken__payment__in=real_visits)
            .values_list('pk', flat=True)
        ) + list(sample_checks.exclude(payout=None).values_list('payout_id', flat=True))
        AccessPass.objects.filter(payment__in=demo).delete()
        Refund.objects.filter(payment__in=demo).delete()
        PayPalOrder.objects.filter(payment__in=demo).delete()
        demo.delete()
        ProblemReport.objects.filter(user_id__startswith='demo:').delete()
        Conversation.objects.filter(user_id__startswith='demo:').delete()
        OutgoingReply.objects.filter(user_id__startswith='demo:').delete()
        sample_checks.delete()
        Payout.objects.filter(pk__in=demo_payouts).delete()
