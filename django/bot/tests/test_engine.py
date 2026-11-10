"""The conversation, ported test by test from the Node version's engine.test.js."""
import math
import time
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from bot.engine import NEEDS, Incoming, parse_choice
from bot.models import Conversation, ProblemReport
from bot.replies import Text
from bot.state import Step
from payments.models import AccessPass, Payment
from restrooms.facility import Facility
from restrooms.geo import Location

from .helpers import NAIROBI, PHONE, START, USER, ChatTester, FakePictures, images, last_menu, texts

SAMPLE_RESTROOMS = Path(settings.BASE_DIR, 'restrooms', 'sample_restrooms.json')
REAL_DATA = {'DEMO_RESTROOMS_NEAR_USER': 'false'}
UHURU_PARK = Location(-1.2885, 36.8131)
FAR_AWAY = Location(-1.33, 36.88)  # about 8 km from Nairobi CBD


def paid():
    return Payment.objects.filter(status=Payment.Status.SUCCEEDED).order_by('pk')


def reports():
    return list(ProblemReport.objects.order_by('pk'))


class EngineTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # The five sample restrooms, used when demo restrooms are turned off.
        call_command('import_restrooms', SAMPLE_RESTROOMS, stdout=StringIO())


class StartTests(EngineTestCase):
    def test_greets_and_asks_for_location_on_the_first_message(self):
        out = texts(ChatTester().say('Hi!'))
        self.assertIn("I'm *TapFlash AI*", out)
        self.assertIn('What are you looking for?', out)

    def test_the_first_message_shows_the_five_needs_menu(self):
        out = texts(ChatTester().say('Hi!'))
        for n, need in enumerate(NEEDS, start=1):
            self.assertIn(f'*{n}.* {need}', out)
        self.assertIn('Reply *exit* to leave.', out)
        self.assertEqual(Conversation.objects.get().step, Step.CHOOSE_NEED)

    def test_picking_a_need_saves_it_and_asks_for_the_location(self):
        t = ChatTester()
        t.say('hi')
        out = texts(t.say('3'))
        self.assertIn('Got it: *The most accessible*.', out)
        self.assertIn('Share your location', out)
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.step, Step.AWAIT_LOCATION)
        self.assertEqual(conversation.state.get('need'), 'The most accessible')

    def test_back_on_the_location_question_returns_to_the_needs(self):
        for typed in ('0', 'back'):
            t = ChatTester()
            t.say('hi')
            self.assertIn('*0.* Back', texts(t.say('2')))
            self.assertIn('What are you looking for?', texts(t.say(typed)))
            self.assertEqual(Conversation.objects.get().step, Step.CHOOSE_NEED)
            self.assertIn('Got it: *The most clean*.', texts(t.say('1')))
            t.say('exit')

    def test_any_first_message_starts_the_conversation(self):
        self.assertIn('What are you looking for?', texts(ChatTester().say('where is the toilet')))

    def test_pressing_continue_without_a_location_explains_how_to_share_it(self):
        t = ChatTester()
        t.say('hi')
        t.say('1')
        self.assertIn("don't have your location yet", texts(t.say('1')))

    def test_sharing_a_location_lists_the_nearest_restrooms_closest_first(self):
        t = ChatTester()
        t.say('hi')
        out = texts(t.share_location())
        self.assertIn('*1.* City Mall Restroom (demo)', out)
        self.assertIn('*4.*', out)
        self.assertNotIn('*5.*', out, 'only MAX_RESULTS restrooms are shown')
        self.assertIn('Which one would you like?', out)

    def test_a_location_as_the_very_first_message_greets_and_lists_restrooms(self):
        out = texts(ChatTester().share_location())
        self.assertIn("I'm *TapFlash AI*", out)
        self.assertIn('City Mall Restroom', out)

    def test_restrooms_from_the_database_are_used_when_demo_mode_is_off(self):
        t = ChatTester(REAL_DATA)
        t.say('hi')
        self.assertIn('Kenyatta Avenue Restroom (sample)', texts(t.share_location()))

    def test_no_restrooms_nearby_is_explained(self):
        t = ChatTester(REAL_DATA)
        t.say('hi')
        out = texts(t.share_location(Location(51.5, -0.12)))  # London
        self.assertIn("couldn't find a restroom within 20 km", out)

    def test_stickers_and_voice_notes_get_a_polite_note_and_the_current_question(self):
        t = ChatTester()
        t.say('hi')
        out = texts(t.send(Incoming(USER, 'other')))
        self.assertIn('only read text and shared locations', out)
        self.assertIn('What are you looking for?', out)

    def test_users_do_not_share_state(self):
        t = ChatTester()
        t.say('hi')
        t.share_location()
        self.assertIn("I'm *TapFlash AI*", texts(t.send(Incoming('user-2', 'text', text='1'))))

    def test_a_huge_message_is_handled_quickly_and_does_not_freeze_the_bot(self):
        t = ChatTester()
        t.say('hi')
        t.say('1')
        started = time.perf_counter()
        out = t.say('!' * 60_000 + 'x')
        took = time.perf_counter() - started
        self.assertLess(took, 0.2, f'took {took * 1000:.0f} ms')
        self.assertIn("don't have your location yet", texts(out))


class PaymentTests(EngineTestCase):
    def test_full_happy_path_choose_pay_with_mpesa_navigate_arrive_unlock(self):
        t = ChatTester()
        t.say('hi')
        t.share_location()

        out = texts(t.say('1'))
        self.assertRegex(out, r'(?m)^\*City Mall Restroom \(demo\)\*$')
        self.assertIn('Price: KES 100', out)
        self.assertIn('*1.* Pay\n*2.* Exit', out)

        out = texts(t.say('1'))
        self.assertIn('*1.* M-Pesa\n_No fees_', out)
        self.assertIn('*4.* PayPal\n_1% fee_', out)

        out = texts(t.say('1'))
        self.assertIn('Enter your M-Pesa number', out)
        self.assertIn('*1.* Use +254712345678', out)

        out = texts(t.say('0712 345 678'))
        self.assertIn('Method: M-Pesa (+254712345678)', out)
        self.assertIn('*Total: KES 100*', out)
        self.assertIn('Test mode', out)

        out = t.say('1')
        self.assertRegex(texts(out), r'Payment received\. Reference \*TF[0-9A-F]{10}\*')
        [receipt] = images(out)
        self.assertEqual(receipt.mimetype, 'image/png')
        self.assertGreater(len(receipt.data), 100)
        self.assertIn('Finding the shortest route', texts(out))
        self.assertIn('https://www.google.com/maps/dir/?api=1&destination=', texts(out))
        self.assertIn('travelmode=walking', texts(out))
        [payment] = paid()
        self.assertEqual(payment.phone, '+254712345678')
        self.assertEqual(payment.total, Decimal(100))
        self.assertEqual(payment.method, 'mpesa')
        self.assertEqual(payment.facility_name, 'City Mall Restroom (demo)')

        self.assertIn('You have arrived at *City Mall Restroom (demo)*', texts(t.say('1')))

        out = t.say('1')
        self.assertIn('Your access details', texts(out))
        self.assertIn('Access code (OTP): *123456*', texts(out))
        # 07:00 UTC is 10:00 in Nairobi, plus a 6 minute walk and 30 minutes.
        self.assertIn('Valid until: *10:36*', texts(out))
        self.assertEqual(len(images(out)), 1, 'access QR code is sent')

        self.assertIn('Your access code (OTP) is *123456*', texts(t.say('2')))
        self.assertEqual(len(images(t.say('1'))), 1)
        self.assertIn('Enjoy!', texts(t.say('3')))
        self.assertIn('Your access details', texts(t.say('pass')), 'pass still available after finishing')

    def test_use_this_whatsapp_number_shortcut(self):
        out = ChatTester().reach_payment_summary('1')
        self.assertIn('Method: M-Pesa (+254712345678)', texts(out))

    def test_card_payment_adds_the_1_percent_fee_and_skips_the_phone_number(self):
        t = ChatTester()
        t.say('hi')
        t.share_location()
        t.say('1')
        t.say('1')
        out = texts(t.say('3'))  # card
        self.assertIn('Method: Card (Visa / Mastercard)\n', out)
        self.assertIn('Fee: KES 1\n', out)
        self.assertIn('*Total: KES 101*', out)

    def test_invalid_phone_number_is_rejected_and_can_be_retried(self):
        t = ChatTester()
        self.assertIn("doesn't look like an M-Pesa number", texts(t.reach_payment_summary('12345')))
        self.assertIn('(+254798765432)', texts(t.say('+254 798 765 432')))

    def test_declined_payment_offers_retry_another_method_or_exit(self):
        t = ChatTester()
        t.reach_payment_summary('0712340000')
        out = texts(t.say('1'))
        self.assertIn('declined', out)
        self.assertIn('*1.* Try again\n*2.* Choose another payment method\n*3.* Exit', out)
        self.assertFalse(paid().exists())
        [failed] = Payment.objects.filter(status=Payment.Status.FAILED)
        self.assertIsNone(failed.reference)
        self.assertEqual(failed.phone, '+254712340000')
        self.assertIn('declined', failed.failure_reason)

        self.assertIn('How would you like to pay?', texts(t.say('2')))
        t.say('3')  # card
        self.assertIn('Payment received', texts(t.say('1')))

    def test_a_payment_provider_that_fails_is_handled_as_a_failed_payment(self):
        def charge(**payment):
            raise ConnectionError('network down')

        t = ChatTester(payments=SimpleNamespace(name='broken', is_test_mode=False, charge=charge))
        t.reach_payment_summary()
        with self.assertLogs('bot.engine', 'ERROR'):
            out = texts(t.say('1'))
        self.assertIn('could not be completed', out)
        self.assertEqual(Payment.objects.get().provider, 'broken')

    def test_cancel_at_the_payment_summary_goes_back_to_pay_or_exit(self):
        t = ChatTester()
        t.reach_payment_summary()
        self.assertIn('Pay to get the route', texts(t.say('2')))

    def test_choose_a_different_restroom_from_the_pay_prompt(self):
        t = ChatTester()
        t.say('hi')
        t.share_location()
        t.say('2')
        self.assertIn('closest restrooms', texts(t.say('3')))
        self.assertRegex(texts(t.say('4')), r'(?m)^\*Riverside Park Restroom \(demo\)\*$')

    def test_back_from_payment_methods_and_from_phone_entry(self):
        t = ChatTester()
        t.say('hi')
        t.share_location()
        t.say('1')
        t.say('1')
        self.assertIn('Pay to get the route', texts(t.say('0')))
        t.say('1')
        t.say('2')  # Airtel
        self.assertIn('How would you like to pay?', texts(t.say('0')))

    def test_unknown_replies_repeat_the_current_question(self):
        t = ChatTester()
        t.say('hi')
        t.share_location()
        out = texts(t.say('9'))
        self.assertIn("didn't get that", out)
        self.assertIn('Which one would you like?', out)

    def test_exit_works_at_any_step_and_says_goodbye(self):
        t = ChatTester()
        t.say('hi')
        t.share_location()
        t.say('1')
        self.assertIn('Thanks for using TapFlash', texts(t.say('EXIT')))
        self.assertIn("I'm *TapFlash AI*", texts(t.say('1')), 'next message starts a new conversation')

    def test_typing_the_word_on_a_choice_works_like_its_number(self):
        t = ChatTester()
        t.say('hi')
        t.share_location()
        t.say('1')
        t.say('1')  # payment methods, with 0: Back
        self.assertIn('Pay to get the route', texts(t.say('back')))
        t.say('pay')
        t.say('m-pesa')
        self.assertIn('How would you like to pay?', texts(t.say('Back')), 'back on the phone question')
        t.say('1')
        t.say('0712345678')
        out = texts(t.say('cancel'))  # the summary's Cancel option, not "exit"
        self.assertIn('Pay to get the route', out)
        self.assertNotIn('Thanks for using', out)
        self.assertIn('How to use TapFlash', texts(t.say('help?')))


class JourneyTests(EngineTestCase):
    def test_a_new_location_after_paying_does_not_reset_the_journey(self):
        t = ChatTester()
        t.reach_payment_summary()
        t.say('1')
        out = texts(t.share_location())
        self.assertIn('Got your location', out)
        self.assertIn('I have arrived', out)

    def test_hi_after_paying_reminds_the_user_of_their_pass(self):
        t = ChatTester()
        t.reach_payment_summary()
        t.say('1')
        self.assertIn(
            'You still have an access pass for *City Mall Restroom (demo)* until 10:36', texts(t.say('hi'))
        )

    def test_pass_expires_access_minutes_after_the_walk_there(self):
        t = ChatTester()
        t.reach_payment_summary()
        t.say('1')
        t.advance(35)  # 6 minute walk and 30 minutes
        self.assertIn('Your access details', texts(t.say('pass')))
        t.advance(2)
        t.say('exit')
        self.assertIn(
            'Your access pass for *City Mall Restroom (demo)* expired at 10:36. Send *hi*', texts(t.say('pass'))
        )

    def test_an_unreadable_location_keeps_the_current_question(self):
        # So its choices still mean what they say.
        t = ChatTester()
        t.reach_payment_summary()
        out = t.share_location(Location(math.nan, math.nan))
        self.assertIn("couldn't read that location", texts(out))
        self.assertNotIn("don't have your location yet", texts(out))
        self.assertEqual([option.title for option in last_menu(out).options], ['Authorize payment', 'Cancel'])
        self.assertIn('Payment received', texts(t.say('1')), 'option 1 is Authorize payment, as shown')
        self.assertEqual(paid().count(), 1)

        # On the restroom list, 2 is still the second restroom (City Mall,
        # paid for above, is left out of it).
        t.say('hi')
        self.assertIn('*2.* Market Square Restroom (demo)', texts(t.share_location()))
        t.share_location(Location(math.nan, math.nan))
        self.assertRegex(texts(t.say('2')), r'(?m)^\*Market Square Restroom \(demo\)\*$')

    def test_an_expired_pass_is_named_with_its_expiry_time_and_unlock_is_no_longer_offered(self):
        t = ChatTester()
        t.pay_by_card()
        t.advance(37)  # the 6 minute walk and 30 minutes have passed
        out = t.say('1')  # I have arrived
        self.assertIn('Your access pass for *City Mall Restroom (demo)* expired at 10:36', texts(out))
        self.assertNotRegex(texts(out), "Unlock|don't have an active access pass")
        self.assertEqual(
            [option.title for option in last_menu(out).options], ['Report a problem', 'Find a restroom', 'Exit']
        )
        self.assertIn('expired at 10:36', texts(t.say('pass')))
        t.say('1')
        self.assertIn('Our team has your report', texts(t.say('I was late')))
        self.assertEqual(reports()[0].step, Step.PASS_EXPIRED)
        self.assertIn('Here are the closest restrooms', texts(t.say('2')))

    def test_the_access_screen_says_when_the_pass_has_expired(self):
        t = ChatTester()
        t.pay_by_card()
        t.say('1')
        t.say('1')  # unlock: the access screen
        t.advance(40)
        out = texts(t.say('2'))  # View OTP
        self.assertIn('expired at 10:36', out)
        self.assertNotIn('123456', out)

    def test_a_far_restroom_shows_the_walk_in_hours_and_the_pass_and_chat_last_for_the_walk(self):
        t = ChatTester(REAL_DATA)
        t.say('hi')
        self.assertIn('12.7 km, about 3 h 18 min walk', texts(t.share_location(Location(-1.2, 36.9))))
        t.say('1')
        t.say('1')
        t.say('3')
        t.say('1')  # paid at 10:00
        t.advance(200)  # still walking
        self.assertIn('You have arrived', texts(t.say('1')))
        # 10:00, a 198 minute walk and 30 minutes.
        self.assertIn('Valid until: *13:48*', texts(t.say('1')))

    def test_restrooms_closed_when_the_user_would_arrive_are_not_listed_or_charged_for(self):
        # 20:30 in Nairobi: Uhuru Park (6 AM to 6 PM) and Jeevanjee (to 7 PM) are closed.
        t = ChatTester(REAL_DATA)
        t.advance(10 * 60 + 30)
        t.say('hi')
        out = texts(t.share_location(UHURU_PARK))
        self.assertNotRegex(out, 'Uhuru Park|Jeevanjee')
        self.assertIn('*1.* Kenyatta Avenue Restroom (sample)', out)

    def test_a_restroom_that_closes_before_the_payment_goes_through_is_not_charged_for(self):
        # 17:50: Uhuru Park is listed, but it closes before the payment goes through.
        t = ChatTester(REAL_DATA)
        t.advance(7 * 60 + 50)
        t.say('hi')
        self.assertIn('*1.* Uhuru Park Restroom (sample)', texts(t.share_location(UHURU_PARK)))
        t.say('1')
        t.say('1')
        t.say('3')
        t.advance(11)
        out = texts(t.say('1'))
        self.assertIn('*Uhuru Park Restroom (sample)* will be closed by the time you get there', out)
        self.assertIn('Here are the closest restrooms', out)
        self.assertFalse(Payment.objects.exists())

    def test_when_every_restroom_nearby_is_closed_the_bot_says_so(self):
        closed = Facility(
            code='x', name='X', area='', lat=NAIROBI.lat, lng=NAIROBI.lng, price=Decimal(100),
            hours='6:00 AM – 7:00 AM', distance_km=0,
        )
        restrooms = SimpleNamespace(nearest=lambda location, at=None, leave_out=frozenset(), need='': [] if at else [closed])
        t = ChatTester(restrooms=restrooms)
        t.say('hi')
        out = texts(t.share_location())
        self.assertIn('restrooms near you are closed now', out)
        self.assertNotIn("couldn't find a restroom within", out)

    def test_when_the_restrooms_with_the_need_are_closed_the_bot_says_so_and_shows_open_ones(self):
        def place(code, hours):
            return Facility(
                code=code, name=code, area='', lat=NAIROBI.lat, lng=NAIROBI.lng, price=Decimal(100),
                hours=hours, distance_km=0.1,
            )

        baby, other = place('Baby Room', '6:00 AM – 7:00 AM'), place('Open One', '24 hours')

        def nearest(location, at=None, leave_out=frozenset(), need=''):
            found = [baby] if need else [baby, other]
            return [f for f in found if f is not baby] if at else found

        t = ChatTester(restrooms=SimpleNamespace(nearest=nearest))
        t.say('hi')
        t.say('5')
        out = texts(t.share_location())
        self.assertIn('I found *washroom to change baby diapers* near you, but it is closed now', out)
        self.assertNotIn("couldn't find", out)
        self.assertIn('*1.* Open One', out)

    def test_a_second_pass_does_not_replace_the_first_one(self):
        t = ChatTester()
        t.pay_by_card('1')  # City Mall
        t.say('3')  # choose a different restroom while walking
        t.say('1')  # Central Bus Station (City Mall, already paid for, is left out)
        t.say('1')
        t.say('3')
        t.say('1')
        self.assertEqual(paid().count(), 2)
        out = texts(t.say('pass'))
        self.assertIn('Restroom: City Mall Restroom (demo)', out)
        self.assertIn('Restroom: Central Bus Station Restroom (demo)', out)
        reminders = texts(t.say('hi'))
        self.assertIn('access pass for *City Mall Restroom (demo)*', reminders)
        self.assertIn('access pass for *Central Bus Station Restroom (demo)*', reminders)


class ReportTests(EngineTestCase):
    def test_report_a_location_problem_while_navigating_then_continue(self):
        t = ChatTester()
        t.reach_payment_summary()
        t.say('1')  # pay
        self.assertIn('Describe it in one message', texts(t.say('2')))
        out = texts(t.say('The gate is locked'))
        self.assertIn('Our team has your report', out)
        self.assertIn('I have arrived', out, 'back to the navigation question')
        [report] = reports()
        self.assertEqual(report.message, 'The gate is locked')
        self.assertEqual(report.step, Step.NAVIGATING)
        self.assertEqual(report.phone, PHONE)
        self.assertEqual(report.facility_name, 'City Mall Restroom (demo)')
        self.assertEqual(report.status, ProblemReport.Status.NEW)

    def test_while_reporting_commands_still_work_and_are_not_saved_as_the_report(self):
        t = ChatTester()
        t.reach_payment_summary()
        t.say('1')  # pay
        t.say('2')  # report

        out = texts(t.say('help'))
        self.assertIn('How to use TapFlash', out)
        self.assertIn('Describe it in one message', out, 'still asking for the report')
        self.assertIn('Your access details', texts(t.say('pass')))
        self.assertIn('I have arrived', texts(t.say('back')), 'back is the Back option')
        t.say('2')
        for not_words in ['99', '\U0001f44d']:
            self.assertIn('Please describe the problem in words', texts(t.say(not_words)))
        self.assertIn("I'm *TapFlash AI*", texts(t.say('Hi!')), 'hi starts over, as the help text says')
        self.assertEqual(reports(), [])

    def test_0_or_cancel_leaves_the_report_step_without_saving(self):
        t = ChatTester()
        t.reach_payment_summary()
        t.say('1')
        t.say('2')
        self.assertIn('I have arrived', texts(t.say('0')))
        t.say('2')
        self.assertIn('I have arrived', texts(t.say('cancel')))
        self.assertEqual(reports(), [])

    def test_exit_still_works_while_reporting(self):
        t = ChatTester()
        t.reach_payment_summary()
        t.say('1')
        t.say('2')
        self.assertIn('Thanks for using TapFlash', texts(t.say('exit')))
        self.assertEqual(reports(), [])

    def test_after_a_report_the_user_can_choose_another_restroom(self):
        t = ChatTester()
        t.pay_by_card()
        t.say('2')
        out = texts(t.say('The restroom is closed'))
        self.assertIn('Our team has your report', out)
        self.assertIn('*3.* Choose a different restroom', out)
        self.assertIn('Here are the closest restrooms', texts(t.say('3')))

    def test_a_problem_can_be_reported_from_the_access_screen(self):
        t = ChatTester()
        t.pay_by_card()
        t.say('1')
        self.assertIn('*4.* Report a problem', texts(t.say('1')))  # the access screen
        t.say('4')
        out = texts(t.say('The code does not work'))
        self.assertIn('Our team has your report', out)
        self.assertEqual(reports()[0].step, Step.ACCESS)
        self.assertIn('*1.* View QR code', out, 'back on the access screen')

    def test_a_report_that_cannot_be_saved_does_not_break_the_chat(self):
        t = ChatTester()
        t.pay_by_card()
        t.say('2')
        broken = mock.patch.object(ProblemReport.objects, 'create', side_effect=RuntimeError('disk full'))
        with broken, self.assertLogs('bot.records', 'ERROR'):
            out = texts(t.say('The door is locked'))
        self.assertIn('Our team has your report', out)
        self.assertIn('I have arrived', out)


class TimeoutTests(EngineTestCase):
    def test_idle_chats_are_forgotten_after_session_timeout_minutes_and_the_user_is_told(self):
        t = ChatTester()
        t.say('hi')
        t.share_location()
        t.advance(31)
        out = texts(t.say('1'))
        self.assertRegex(out, r'^Our chat timed out, so I have started again\.\n')
        self.assertNotIn('Nothing was charged', out, 'nothing was being paid for')
        self.assertIn('What are you looking for?', out)
        self.assertEqual(Conversation.objects.get().step, Step.CHOOSE_NEED)
        self.assertIn("I'm *TapFlash AI*", texts(t.say('hi')), 'after that, a normal start')

    def test_a_chat_that_times_out_at_the_payment_summary_says_nothing_was_charged(self):
        t = ChatTester()
        t.reach_payment_summary()
        t.advance(31)
        out = texts(t.say('1'))
        self.assertIn('Our chat timed out, so I have started again. Nothing was charged.', out)
        self.assertIn('What are you looking for?', out)
        self.assertFalse(Payment.objects.exists())

    def test_a_paid_journey_does_not_time_out_while_its_pass_works_and_a_late_report_is_saved(self):
        t = ChatTester()
        t.pay_by_card()
        t.say('2')  # report
        t.advance(31)  # longer than SESSION_TIMEOUT_MINUTES, but the pass still works
        self.assertIn('Our team has your report', texts(t.say('The door is locked')))
        self.assertEqual(reports()[-1].message, 'The door is locked')

        t.say('2')  # report again
        t.advance(70)  # the pass, and then the chat, have both run out
        out = texts(t.say('Nobody opened it'))
        self.assertIn('Our team has your report', out)
        self.assertIn('Our chat timed out', out)
        self.assertEqual(reports()[-1].message, 'Nobody opened it')
        self.assertEqual(reports()[-1].facility_name, 'City Mall Restroom (demo)')

    def test_after_a_chat_timed_out_help_or_pass_do_not_use_up_the_timeout_notice(self):
        for command in ['pass', 'help']:
            with self.subTest(command):
                t = ChatTester(user=command)
                t.reach_payment_summary()
                t.advance(31)
                t.say(command)
                out = texts(t.say('1'))  # meant as Authorize payment
                self.assertRegex(out, r'^Our chat timed out, so I have started again\. Nothing was charged\.')
                self.assertFalse(Payment.objects.filter(user_id=command).exists())

    def test_after_exit_an_old_timeout_is_no_longer_mentioned(self):
        t = ChatTester()
        t.reach_payment_summary()
        t.advance(31)
        t.say('exit')
        out = texts(t.say('1'))
        self.assertRegex(out, '^Hello, I')
        self.assertNotIn('timed out', out)


class PictureTests(EngineTestCase):
    def test_choices_come_as_menus_with_pictures_without_repeating_them_as_text(self):
        t = ChatTester(pictures=FakePictures())
        t.say('hi')

        menu = last_menu(t.share_location())
        self.assertEqual(menu.text, 'Here are the closest restrooms. Which one would you like?')
        self.assertEqual([option.id for option in menu.options], ['1', '2', '3', '4', 'exit'])
        self.assertEqual(menu.options[0].title, 'City Mall Restroom (demo)')
        self.assertRegex(menu.options[0].description, '^[0-9]+ m, about [0-9]+ min walk, KES 100$')
        self.assertEqual(menu.options[0].icon.filename, 'restroom_thumb.png')
        self.assertTrue(menu.options[4].secondary)

        menu = last_menu(t.say('1'))
        self.assertEqual(menu.image.filename, 'route_preview.png')
        self.assertEqual([option.title for option in menu.options], ['Pay', 'Exit', 'Choose a different restroom'])

        menu = last_menu(t.say('1'))
        self.assertEqual(
            [option.title for option in menu.options],
            ['M-Pesa', 'Airtel Money', 'Card (Visa / Mastercard)', 'PayPal', 'Back'],
        )
        self.assertEqual(menu.options[0].icon.filename, 'payment_icon.png')

        self.assertEqual(last_menu(t.say('1')).input_placeholder, '0712 345 678')

        t.say('0712345678')
        out = t.say('1')
        self.assertRegex(out[1].filename, '^receipt-TF')
        self.assertEqual(out[2].filename, 'restroom_photo.png')
        self.assertIn('Finding the shortest route', out[2].caption)
        menu = last_menu(out)
        self.assertEqual(menu.image.filename, 'route_map.png')
        self.assertIn('google.com/maps', menu.text)

        t.say('1')
        menu = last_menu(t.say('1'))
        self.assertEqual(menu.image.filename, 'access_card.png')
        self.assertNotIn('Amount paid', menu.text, 'the card already shows the details')
        self.assertEqual(
            [option.title for option in menu.options], ['View QR code', 'View OTP', 'Done', 'Report a problem']
        )

        self.assertEqual(t.say('pass')[0].filename, 'access_card.png')

    def test_repeating_a_question_does_not_resend_the_map(self):
        t = ChatTester(pictures=FakePictures())
        t.say('hi')
        t.share_location()
        menu = last_menu(t.say('9'))
        self.assertEqual(menu.options[0].icon.filename, 'restroom_thumb.png', 'small icons are fine')
        t.say('1')
        self.assertIsNone(last_menu(t.say('9')).image)

    def check_the_chat_without_pictures(self, pictures):
        t = ChatTester(pictures=pictures)
        t.say('hi')
        out = t.share_location()
        self.assertIsNone(last_menu(out).options[0].icon)
        self.assertRegex(texts(out), r'\*1\.\* City Mall Restroom \(demo\)\n_[0-9]+ m, about')
        self.assertIsNone(last_menu(t.say('1')).image)
        t.say('1')
        t.say('1')
        t.say('0712345678')
        out = t.say('1')
        self.assertEqual(out[2], Text('Finding the shortest route to *City Mall Restroom (demo)*…'))
        self.assertIsNone(last_menu(out).image)
        t.say('1')
        out = t.say('1')
        self.assertIn('Access code (OTP)', texts(out), 'details as text')
        self.assertRegex(last_menu(out).image.filename, '^access-TF', 'plain QR code instead of the card')

    def test_if_a_picture_cannot_be_drawn_the_chat_goes_on_without_it(self):
        with self.assertLogs('bot.engine', 'ERROR'):
            self.check_the_chat_without_pictures(FakePictures('fail'))

    def test_a_missing_picture_is_left_out(self):
        self.check_the_chat_without_pictures(FakePictures('none'))

    def test_without_pictures_only_qr_codes_are_sent(self):
        t = ChatTester()
        t.reach_payment_summary()
        out = t.say('1')
        self.assertEqual([image.filename[:10] for image in images(out)], ['receipt-TF'])
        t.say('1')
        out = t.say('1')
        self.assertEqual([image.filename[:9] for image in images(out)], ['access-TF'])


class SecondRoundTests(EngineTestCase):
    """Found in the Node version's second round of testing."""

    def pay_for_kenyatta(self, t):
        # From City Market, pay by card for Kenyatta Avenue (280 m, 4 min walk).
        t.say('hi')
        t.share_location(Location(-1.2833, 36.8219))
        t.say('2')
        t.say('1')
        t.say('3')
        return t.say('1')

    def test_a_location_shared_after_paying_is_kept_for_the_next_search(self):
        t = ChatTester(REAL_DATA)
        self.pay_for_kenyatta(t)
        out = texts(t.share_location(FAR_AWAY))
        self.assertIn('Got your location', out)
        self.assertIn('I have arrived', out, 'the journey goes on')

        out = texts(t.say('3'))  # choose a different restroom
        self.assertIn('*1.* Railways Bus Terminus Restroom (sample)\n_7.4 km, about 1 h 55 min walk', out)
        self.assertNotIn(' m, about', out, 'nothing is a few metres away any more')
        t.say('1')
        t.say('1')
        t.say('3')
        t.say('1')  # paid at 10:00
        t.say('1')
        # 10:00, a 115 minute walk from where the user is now, and 30 minutes.
        self.assertIn('Valid until: *12:25*', texts(t.say('1')))

    def test_find_a_restroom_after_the_pass_ran_out_uses_the_location_shared_on_the_way(self):
        t = ChatTester(REAL_DATA)
        self.pay_for_kenyatta(t)
        t.share_location(FAR_AWAY)
        t.advance(40)  # the 4 minute walk and 30 minutes have passed
        t.say('1')  # the pass has run out
        out = texts(t.say('2'))  # Find a restroom
        self.assertIn('*1.* Railways Bus Terminus Restroom (sample)\n_7.4 km', out)

    def test_choose_a_different_restroom_leaves_out_the_one_the_user_has_a_pass_for(self):
        t = ChatTester()
        t.pay_by_card()  # City Mall
        out = texts(t.say('3'))
        self.assertNotIn('City Mall', out)
        self.assertIn('*1.* Central Bus Station Restroom (demo)', out)
        self.assertIn('*4.* Fuel Station Restroom (demo)', out, 'the next closest takes its place')

        # A new search leaves it out too, and reminds the user of the pass.
        self.assertIn('You still have an access pass for *City Mall Restroom (demo)*', texts(t.say('hi')))
        self.assertNotIn('City Mall', texts(t.share_location()))

        # Once the pass has run out, it can be bought again.
        t.advance(37)
        t.say('hi')
        self.assertIn('*1.* City Mall Restroom (demo)', texts(t.share_location()))
        self.assertEqual(paid().count(), 1)

    def test_when_the_user_has_a_pass_for_every_open_restroom_nearby_the_bot_says_so(self):
        only = Facility(
            code='only', name='Only Restroom', area='', lat=NAIROBI.lat, lng=NAIROBI.lng, price=Decimal(100),
            hours='24 hours', distance_km=0.1,
        )
        restrooms = SimpleNamespace(
            nearest=lambda location, at=None, leave_out=frozenset(), need='': [] if 'only' in leave_out else [only]
        )
        t = ChatTester(restrooms=restrooms)
        t.pay_by_card()
        out = texts(t.say('3'))  # choose a different restroom
        self.assertIn('You already have an access pass for the open restrooms near you', out)
        self.assertNotRegex(out, "closed now|couldn't find")
        self.assertEqual(paid().count(), 1)

    def test_a_pass_ends_when_the_restroom_closes_and_the_summary_says_so_before_paying(self):
        t = ChatTester(REAL_DATA)
        t.advance(7 * 60 + 45)  # 17:45 in Nairobi; Uhuru Park closes at 6:00 PM
        t.say('hi')
        t.share_location(UHURU_PARK)
        self.assertRegex(texts(t.say('1')), r'(?m)^\*Uhuru Park Restroom \(sample\)\*$')
        t.say('1')
        self.assertIn('The restroom closes at 18:00, so your pass ends then.', texts(t.say('3')))  # card
        t.say('1')  # paid
        t.say('1')
        self.assertIn('Valid until: *18:00*', texts(t.say('1')), 'not 18:16 (1 minute walk and 30 minutes)')
        self.assertEqual(AccessPass.objects.get().expires_at, START.replace(hour=15))

    def test_earlier_in_the_day_the_pass_is_not_cut_short_and_nothing_is_said(self):
        t = ChatTester(REAL_DATA)
        t.say('hi')
        t.share_location(UHURU_PARK)
        t.say('1')
        t.say('1')
        self.assertNotIn('closes at', texts(t.say('3')))


class ParseChoiceTests(SimpleTestCase):
    def test_common_ways_of_typing_a_number(self):
        self.assertEqual(parse_choice('1'), 1)
        self.assertEqual(parse_choice(' 2. '), 2)
        self.assertEqual(parse_choice('3)'), 3)
        self.assertEqual(parse_choice('1\ufe0f\u20e3'), 1)  # the keycap emoji
        self.assertIsNone(parse_choice('one'))
        self.assertIsNone(parse_choice('12345'))
        self.assertIsNone(parse_choice('\u0663'), 'only the digits 0 to 9')


class NeedTests(TestCase):
    def pick(self, number):
        t = ChatTester()
        t.say('hi')
        t.say(str(number))
        return texts(t.share_location())

    def test_most_clean_lists_the_best_rated_first(self):
        out = self.pick(1)
        self.assertLess(out.index('City Mall'), out.index('Riverside Park'))
        self.assertLess(out.index('Riverside Park'), out.index('Central Bus Station'))

    def test_baby_changing_lists_only_restrooms_that_have_it(self):
        out = self.pick(5)
        self.assertIn('City Mall', out)
        self.assertNotIn('Central Bus Station', out)
        self.assertNotIn('Market Square', out)

    def test_disabled_toilet_lists_only_restrooms_that_have_it(self):
        out = self.pick(4)
        self.assertIn('Riverside Park', out)
        self.assertNotIn('Central Bus Station', out)
