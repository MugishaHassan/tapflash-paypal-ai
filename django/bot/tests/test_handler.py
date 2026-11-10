from datetime import timedelta
from unittest import mock

from django.test import TestCase

from bot.engine import Incoming
from bot.handler import APOLOGY, handle_message
from bot.models import Conversation
from bot.replies import Text
from bot.sessions import QUICKEST_ANSWER, ChatStore
from bot.state import Step
from payments.models import Payment
from restrooms.geo import Location

from .helpers import START, USER, ChatTester, texts


class RestartTests(TestCase):
    def test_a_chat_in_the_middle_of_paying_survives_a_restart(self):
        t = ChatTester()
        t.say('hi')
        t.share_location(Location(-1.285, 36.82))
        for answer in ['1', '1', '3']:
            t.say(answer)
        t.engine = t.new_engine()
        self.assertIn('Payment received', texts(t.say('1')))
        t.engine = t.new_engine()
        self.assertIn('Access code (OTP)', texts(t.say('pass')))

    def test_the_chat_is_saved_in_the_users_row(self):
        t = ChatTester()
        t.say('hi')
        t.share_location()
        conversation = Conversation.objects.get(user_id=USER)
        self.assertEqual(conversation.status, Conversation.Status.ACTIVE)
        self.assertEqual(conversation.step, Step.CHOOSE_RESTROOM)
        self.assertEqual(conversation.last_seen, START)
        self.assertEqual(len(conversation.state['options']), 4)
        t.say('exit')
        conversation.refresh_from_db()
        self.assertEqual((conversation.status, conversation.step, conversation.state), ('idle', '', {}))


class FailureTests(TestCase):
    def test_if_the_engine_fails_nothing_it_did_is_kept_and_the_user_gets_an_apology(self):
        t = ChatTester()
        t.reach_payment_summary()
        with (
            mock.patch('bot.engine.qr_image', side_effect=RuntimeError('broken')),
            self.assertLogs('bot.handler', 'ERROR'),
        ):
            self.assertEqual(t.say('1'), [Text(APOLOGY)])
        self.assertFalse(Payment.objects.exists(), 'the payment saved before the error is undone')
        self.assertEqual(Conversation.objects.get().step, Step.AUTHORIZE)
        self.assertIn('Payment received', texts(t.say('1')), 'the user can try again')

    def test_a_reply_that_cannot_be_sent_is_logged_and_the_chat_is_kept(self):
        sent = []

        def send(reply):
            if reply.kind == 'menu':
                raise ConnectionError('Meta API 500')
            sent.append(reply)

        t = ChatTester()
        with self.assertLogs('bot.handler', 'ERROR'):
            handle_message(Incoming(USER, 'text', text='hi'), send, t.engine)
        self.assertEqual(sent[-1], Text(APOLOGY))
        self.assertEqual(Conversation.objects.get().step, Step.CHOOSE_NEED)

    def test_a_chat_saved_by_an_older_version_that_cannot_be_read_starts_again(self):
        for state in [
            {'step': 'old_step_name'},
            {'restroom': {'price': '100', 'field_since_removed': 1}},
            {'restroom': {'price': 'not money'}},
            {'payment': {'price': '100'}},
        ]:
            with self.subTest(state):
                Conversation.objects.update_or_create(
                    user_id=USER, defaults={'status': Conversation.Status.ACTIVE, 'last_seen': START, 'state': state}
                )
                with self.assertLogs('bot.sessions', 'ERROR'):
                    out = texts(ChatTester().say('1'))
                self.assertIn("I'm *TapFlash AI*", out)
                self.assertEqual(Conversation.objects.get().step, Step.CHOOSE_NEED)

    def test_a_channel_that_is_down_does_not_raise(self):
        t = ChatTester()
        with self.assertLogs('bot.handler', 'ERROR'):
            handle_message(Incoming(USER, 'text', text='hi'), mock.Mock(side_effect=ConnectionError), t.engine)


class DoubleSendTests(TestCase):
    """Ported from the Node version's app.test.js. Messages are stamped with the
    clock as they arrive; the clock only moves when the test says so."""

    def say(self, t, text):
        return texts(t.say(text, received_at=t.now))

    def test_the_same_message_sent_twice_at_once_is_answered_once(self):
        t = ChatTester()
        self.say(t, 'hi')
        t.send(Incoming(USER, 'location', location=Location(-1.2833, 36.8219), received_at=t.now))

        self.assertIn('Central Bus Station Restroom', self.say(t, '2'))
        self.assertEqual(self.say(t, '2'), '', 'the copy is dropped, not taken as Exit')

        self.say(t, '1')  # pay
        self.say(t, '3')  # card
        answers = [self.say(t, '1') for _ in range(3)]
        self.assertIn('Payment received', answers[0])
        self.assertEqual(answers[1:], ['', ''])
        self.assertEqual(Payment.objects.count(), 1)

        # A real answer to the next question, once the user has read it, still works.
        t.advance(milliseconds=350)
        self.assertIn('You have arrived', self.say(t, '1'))

    def test_a_repeat_is_only_dropped_if_the_user_could_not_have_seen_the_answer_yet(self):
        answered_at = START
        ms = timedelta(milliseconds=1)

        def store(user='u'):
            ChatStore.create_row(user)
            return ChatStore.lock(user, timeout=timedelta(minutes=30), clock=lambda: answered_at)

        def message(text, received_at):
            return Incoming('u', 'text', text=text, received_at=received_at)

        store().mark_answered(message('1', answered_at - 100 * ms))
        self.assertTrue(store().is_repeat(message('1', answered_at - 50 * ms)), 'sent while the bot was answering')
        self.assertTrue(store().is_repeat(message('1', answered_at + 10 * ms)), 'arrived just after the answer')
        self.assertFalse(store().is_repeat(message('1', answered_at + QUICKEST_ANSWER)), 'time to read it')
        self.assertFalse(store().is_repeat(message('2', answered_at - 50 * ms)), 'a different message')
        self.assertFalse(store().is_repeat(message('1', None)), 'no arrival time (the simulator)')
        self.assertFalse(store('other').is_repeat(message('1', answered_at - 50 * ms)), 'another user')

    def test_the_same_location_twice_is_a_repeat_but_a_new_one_is_not(self):
        t = ChatTester()
        self.say(t, 'hi')
        here = Incoming(USER, 'location', location=Location(-1.2833, 36.8219), received_at=t.now)
        self.assertTrue(t.send(here))
        self.assertEqual(t.send(here), [])
        moved = Incoming(USER, 'location', location=Location(-1.2834, 36.8219), received_at=t.now)
        self.assertTrue(t.send(moved))
