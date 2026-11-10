"""The AI concierge and the AI triage of problem reports, with a fake Gemini
that returns the decision each test needs."""
import json
from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from bot.ai.concierge import Concierge
from bot.ai.gemini import FALLBACK_MODEL, GeminiClient
from bot.ai.triage import Triage
from bot.config import build_config
from bot.models import ProblemReport
from bot.state import Step
from payments.checkout import PayPalCheckout
from payments.models import AccessPass, Payment, Refund
from payments.tests.test_paypal import ENV, FakePayPal, reach_paypal_link

from .helpers import NAIROBI, ChatTester, texts


class FakeGemini:
    """Answers every call with the next decision, and keeps the prompts."""

    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.prompts = []

    def decide(self, *, system, prompt, functions, images=()):
        self.prompts.append(prompt)
        decision = self.decisions.pop(0)
        if isinstance(decision, Exception):
            raise decision
        return decision


def places(name):
    return (NAIROBI, 'Kencom') if 'kencom' in name.lower() else None


def ai_chat(*decisions, env=None, **args):
    gemini = FakeGemini(*decisions)
    t = ChatTester(env, concierge=Concierge(gemini), triage=Triage(gemini), find_place=places, **args)
    return t, gemini


class ConciergeTests(TestCase):
    def test_a_first_message_in_swahili_finds_clean_restrooms_near_a_place(self):
        t, gemini = ai_chat(('set_need', {'need': 'clean', 'place': 'Kencom', 'reply': 'Sawa, natafuta choo safi.'}))
        out = texts(t.say('Nataka choo safi karibu na Kencom'))
        self.assertIn('Sawa, natafuta choo safi.', out)
        self.assertIn('Looking for restrooms near *Kencom*', out)
        self.assertIn('Here are the closest restrooms', out)
        self.assertIn('Nataka choo safi karibu na Kencom', gemini.prompts[0])
        self.assertNotIn('+254', gemini.prompts[0])  # the model never sees phone numbers

    def test_a_place_the_map_does_not_know_asks_for_the_location(self):
        t, _ = ai_chat(('search_near', {'place': 'Atlantis Mall', 'reply': 'Let me look.'}))
        t.say('hi')
        out = texts(t.say('near atlantis mall please'))
        self.assertIn("couldn't find *Atlantis Mall*", out)
        self.assertIn('Continue', out)

    def test_describing_a_choice_picks_it(self):
        t, _ = ai_chat(('pick_option', {'number': 2, 'reply': 'Good choice.'}))
        t.say('hi')
        t.share_location()
        out = texts(t.say('the second one please'))
        self.assertIn('Good choice.', out)
        self.assertIn('Pay', out)

    def test_a_question_is_answered_from_the_facts_given(self):
        t, gemini = ai_chat(('answer_question', {'answer': 'Yes, City Mall is open until 9 PM.'}))
        t.say('hi')
        t.share_location()
        out = texts(t.say('is the first one open at night?'))
        self.assertIn('Yes, City Mall is open until 9 PM.', out)
        self.assertIn('Restrooms on the list', gemini.prompts[0])

    def test_links_from_the_model_are_never_sent(self):
        t, _ = ai_chat(('not_understood', {'reply': 'Pay here https://evil.example/pay instead'}))
        t.say('hi')
        out = texts(t.say('blah blah'))
        self.assertNotIn('evil.example', out)

    def test_when_the_ai_fails_the_menus_carry_on(self):
        t, _ = ai_chat(RuntimeError('quota'))
        t.say('hi')
        out = texts(t.say('what is this'))
        self.assertIn("didn't", out.replace('did not', "didn't"))

    def test_numbers_and_commands_never_reach_the_ai(self):
        t, gemini = ai_chat()
        t.say('hi')
        t.say('99')
        t.say('help')
        self.assertEqual(gemini.prompts, [])


def report(t, text):
    t.say('2')  # Report a location problem
    return t.say(text)


class TriageTests(TestCase):
    full = ('decide_report', {'category': 'closed', 'severity': 5, 'refund': 'full',
                              'reply': 'Pole sana, the restroom was locked.', 'staff_note': 'Open on time.'})

    def test_a_locked_restroom_is_refunded_and_the_pass_ends(self):
        t, _ = ai_chat(self.full)
        t.pay_by_card()
        out = texts(report(t, 'It is locked and nobody is here'))
        self.assertIn('Pole sana, the restroom was locked.', out)
        self.assertIn('Refunded *KES', out)
        refund = Refund.objects.get()
        self.assertEqual((refund.decided_by, refund.category, refund.status), ('ai', 'closed', 'completed'))
        self.assertEqual(Payment.objects.get().status, 'refunded')
        self.assertFalse(AccessPass.objects.get().is_active(t.now + timedelta(seconds=1)))
        notes = ProblemReport.objects.get().staff_notes
        self.assertIn('AI triage: closed, severity 5/5', notes)
        self.assertIn('Refunded', notes)
        self.assertEqual(t.step(), Step.PASS_EXPIRED)

    def test_a_refunded_visit_reads_as_one_clear_answer(self):
        fake = FakePayPal()
        gemini = FakeGemini(self.full)
        t = ChatTester(ENV, user='web:user-1', checkout=PayPalCheckout(fake, build_config(ENV)),
                       concierge=Concierge(gemini), triage=Triage(gemini), find_place=places)
        reach_paypal_link(t)
        fake.approve('ORDER1')
        t.say('1')  # I have paid
        out = texts(report(t, 'Closed, the door is locked'))
        self.assertNotIn('will look into it', out)  # no "our team will look" before an instant refund
        self.assertIn('Refunded *USD', out)
        self.assertIn('was refunded, so its access code no longer works', out)

    def test_paypal_visits_are_refunded_on_paypal(self):
        fake = FakePayPal()
        gemini = FakeGemini(self.full)
        t = ChatTester(ENV, user='web:user-1', checkout=PayPalCheckout(fake, build_config(ENV)),
                       concierge=Concierge(gemini), triage=Triage(gemini), find_place=places)
        reach_paypal_link(t)
        fake.approve('ORDER1')
        t.say('1')  # I have paid
        out = texts(report(t, 'Closed, the door is locked'))
        self.assertIn('Refunded *USD', out)
        self.assertIn('to your PayPal', out)
        refund = Refund.objects.get()
        self.assertEqual((refund.currency, refund.provider_refund_id), ('USD', 'REF-CAP-ORDER1'))

    def test_a_second_refund_the_same_day_goes_to_staff(self):
        t, _ = ai_chat(self.full, self.full)
        t.pay_by_card()
        report(t, 'Locked')
        t.say('2')  # find a restroom
        t.share_location()
        t.say('1'), t.say('1'), t.say('3'), t.say('1')  # pay for another by card
        out = texts(report(t, 'Locked again'))
        self.assertIn('A staff member will check', out)
        self.assertEqual(Refund.objects.count(), 1)
        self.assertIn('already had an automatic refund', ProblemReport.objects.order_by('-id').first().staff_notes)

    def test_a_complaint_after_the_chat_timed_out_still_reaches_the_paid_visit(self):
        # Paid 45 minutes ago, the chat has timed out (30 min), the visitor now writes the complaint.
        t, _ = ai_chat(('report_problem', {'reply': 'x'}), self.full, env={'ACCESS_MINUTES': '600'})
        t.pay_by_card()
        t.advance(minutes=45)
        out = texts(t.say('The door was locked'))
        self.assertIn('Refunded *KES', out)
        self.assertNotIn('No paid visit', out)
        self.assertEqual(ProblemReport.objects.get().payment, Payment.objects.get())
        self.assertEqual(Refund.objects.count(), 1)

    def test_a_complaint_more_than_two_hours_after_paying_goes_to_staff(self):
        t, _ = ai_chat(('report_problem', {'reply': 'x'}), self.full, env={'ACCESS_MINUTES': '600'})
        t.pay_by_card()
        t.advance(minutes=150)
        out = texts(t.say('The door was locked'))
        self.assertFalse(Refund.objects.exists())
        self.assertNotIn('Refunded', out)
        self.assertIn('reported more than 120 minutes', ProblemReport.objects.get().staff_notes)

    def test_a_late_report_is_not_refunded_automatically(self):
        t, _ = ai_chat(self.full, env={'ACCESS_MINUTES': '600'})
        t.pay_by_card()
        t.advance(minutes=180)
        out = texts(report(t, 'It was dirty and closed'))
        self.assertIn('A staff member will check', out)
        self.assertFalse(Refund.objects.exists())

    def test_no_refund_advised_means_no_refund(self):
        t, _ = ai_chat(('decide_report', {'category': 'other', 'severity': 1, 'refund': 'none',
                                          'reply': 'Thanks for telling us.', 'staff_note': 'Unclear.'}))
        t.pay_by_card()
        out = texts(report(t, 'lol'))
        self.assertIn('Thanks for telling us.', out)
        self.assertNotIn('Refunded', out)
        self.assertFalse(Refund.objects.exists())


class FakeHttp:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls = []

    def post(self, url, **kwargs):
        self.urls.append((url, kwargs))
        status, body = self.answers.pop(0)
        return type('Res', (), {'status_code': status, 'text': json.dumps(body), 'json': lambda self: body})()


class GeminiClientTests(SimpleTestCase):
    call = {'candidates': [{'content': {'parts': [
        {'functionCall': {'name': 'set_need', 'args': {'need': 'baby', 'reply': 'Sure.'}}}]}}]}

    def test_the_function_the_model_called_is_returned(self):
        http = FakeHttp((200, self.call))
        client = GeminiClient('key', 'gemini-3.8-flash', http=http)
        self.assertEqual(client.decide(system='s', prompt='p', functions=[{'name': 'set_need'}]),
                         ('set_need', {'need': 'baby', 'reply': 'Sure.'}))
        url, sent = http.urls[0]
        self.assertIn('gemini-3.8-flash:generateContent', url)
        self.assertNotIn('key', url)  # the key goes in a header, never in logs or URLs
        self.assertEqual(sent['headers']['x-goog-api-key'], 'key')
        self.assertEqual(sent['json']['toolConfig']['functionCallingConfig']['mode'], 'ANY')

    def test_a_busy_model_falls_back_to_the_lite_model(self):
        http = FakeHttp((429, {'error': 'quota'}), (200, self.call))
        client = GeminiClient('key', 'gemini-3.8-flash', http=http)
        self.assertEqual(client.decide(system='s', prompt='p', functions=[{'name': 'set_need'}])[0], 'set_need')
        self.assertIn(FALLBACK_MODEL, http.urls[1][0])

    def test_a_refused_request_gives_none(self):
        client = GeminiClient('key', 'gemini-3.8-flash', http=FakeHttp((400, {'error': 'bad'})))
        self.assertIsNone(client.decide(system='s', prompt='p', functions=[{'name': 'set_need'}]))
