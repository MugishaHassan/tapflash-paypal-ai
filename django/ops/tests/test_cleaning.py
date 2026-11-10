"""Cleaning bonuses: a cleaner's photo, Gemini's verdict (faked here), the
rules the AI can't change, and the payout on approval."""
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from bot.engine import Incoming
from bot.models import OutgoingReply
from bot.tests.helpers import PHONE, ChatTester, texts
from ops.models import Cleaner, CleaningCheck
from payments.models import Payout
from restrooms.models import Restroom

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 64


def photo(n=0):
    return PNG + bytes([n])


class FakeJudge:
    def __init__(self, *verdicts):
        self.verdicts = list(verdicts)
        self.seen = []

    def judge(self, *, photo, mimetype, restroom_name, reference=None):
        self.seen.append((mimetype, restroom_name))
        self.references = getattr(self, 'references', []) + [reference]
        return self.verdicts.pop(0)


def verdict(score, is_restroom=True, issues='', reply='Nice work.', same_restroom=True):
    return {'is_restroom': is_restroom, 'same_restroom': same_restroom, 'score': score, 'issues': issues,
            'reply': reply}


class FakePayouts:
    def __init__(self):
        self.sent = []

    def create_payout(self, **kwargs):
        self.sent.append(kwargs)
        return {'batch_id': f'BATCH{len(self.sent)}', 'status': 'PENDING'}


class CleaningTests(TestCase):
    def setUp(self):
        self.restroom = Restroom.objects.create(code='city-market', name='City Market', latitude=-1.28,
                                                longitude=36.82)
        self.cleaner = Cleaner.objects.create(name='Achieng', phone=PHONE, payout_email='achieng@example.com',
                                              restroom=self.restroom, bonus=Decimal('1.50'))

    def chat(self, *verdicts, paypal=None):
        judge = FakeJudge(*verdicts)
        checkout = SimpleNamespace(client=paypal) if paypal else None
        t = ChatTester(cleaning_judge=judge, **({'checkout': checkout} if checkout else {}))
        if not paypal:
            t.engine.checkout = None
        return t, judge

    def send_photo(self, t, n=0, mimetype='image/png'):
        return texts(t.send(Incoming(t.user, 'image', image=photo(n), image_type=mimetype, phone=PHONE)))

    def test_a_clean_photo_is_paid_at_once_with_paypal_payouts(self):
        paypal = FakePayouts()
        t, judge = self.chat(verdict(5), paypal=paypal)
        out = self.send_photo(t)
        self.assertIn('Approved!', out)
        self.assertIn('USD 1.50', out)
        self.assertIn('BATCH1', out)
        self.assertEqual(judge.seen, [('image/png', 'City Market')])
        item = paypal.sent[0]['items'][0]
        self.assertEqual((item['receiver'], item['amount']), ('achieng@example.com', Decimal('1.50')))
        payout = Payout.objects.get()
        self.assertEqual((payout.kind, payout.status), (Payout.Kind.CLEANING, 'PENDING'))
        self.assertEqual(CleaningCheck.objects.get().payout, payout)

    def test_the_same_photo_is_never_paid_twice(self):
        t, _ = self.chat(verdict(5), verdict(5))
        self.send_photo(t)
        t.advance(minutes=60 * 5)
        out = self.send_photo(t)
        self.assertIn('sent before', out)
        self.assertEqual(Payout.objects.count(), 1)

    def test_one_bonus_every_few_hours(self):
        t, _ = self.chat(verdict(5), verdict(5))
        self.send_photo(t, 1)
        t.advance(minutes=60)
        self.assertIn('next bonus', self.send_photo(t, 2))
        t.advance(minutes=60 * 4)
        self.assertIn('Approved!', self.send_photo(t, 3))
        self.assertEqual(Payout.objects.count(), 2)

    def test_a_doubtful_photo_waits_for_staff_who_approve_and_pay(self):
        t, _ = self.chat(verdict(3, issues='Bin not emptied'))
        out = self.send_photo(t)
        self.assertIn('supervisor', out)
        check = CleaningCheck.objects.get()
        self.assertEqual(check.status, CleaningCheck.Status.REVIEW)
        self.assertFalse(Payout.objects.exists())

        staff = User.objects.create_user('ops', password='pw-for-tests', is_staff=True)
        self.client.force_login(staff)
        row = self.client.get('/ops/data').json()['cleaning'][0]
        self.assertEqual((row['status'], row['score'], row['note']), ('review', 3, 'Bin not emptied'))
        self.assertEqual(self.client.get(row['photo']).content, photo(0))

        response = self.client.post('/ops/cleaning', json.dumps({'check': check.pk, 'action': 'approve'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        check.refresh_from_db()
        self.assertEqual((check.status, check.decided_by), (CleaningCheck.Status.APPROVED, 'ops'))
        self.assertEqual(check.payout.amount, Decimal('1.50'))
        told = OutgoingReply.objects.get(user_id=t.user)
        self.assertIn('supervisor approved', json.dumps(told.replies))
        again = self.client.post('/ops/cleaning', json.dumps({'check': check.pk, 'action': 'approve'}),
                                 content_type='application/json')
        self.assertEqual(again.status_code, 409)
        self.assertEqual(Payout.objects.count(), 1)

    def test_a_dirty_photo_is_not_paid_and_says_what_is_left(self):
        t, _ = self.chat(verdict(2, issues='Floor still wet'))
        out = self.send_photo(t)
        self.assertIn('Not approved yet', out)
        self.assertIn('Floor still wet', out)
        self.assertFalse(Payout.objects.exists())
        # A rejected photo doesn't block the next try.
        t.advance(minutes=10)
        t.engine.cleaning_judge.verdicts.append(verdict(5))
        self.assertIn('Approved!', self.send_photo(t, 9))

    def test_a_photo_of_another_restroom_waits_for_staff(self):
        from django.core.files.base import ContentFile
        self.restroom.photo.save('city-market.jpg', ContentFile(b'\xff\xd8reference'), save=True)
        t, judge = self.chat(verdict(5, same_restroom=False))
        out = self.send_photo(t)
        self.assertIn('supervisor', out)
        self.assertEqual(judge.references, [(b'\xff\xd8reference', 'image/jpeg')])
        check = CleaningCheck.objects.get()
        self.assertEqual(check.status, CleaningCheck.Status.REVIEW)
        self.assertIn('May not be this restroom', check.ai_note)
        self.assertFalse(Payout.objects.exists())

    def test_not_a_restroom(self):
        t, _ = self.chat(verdict(5, is_restroom=False))
        self.assertIn("doesn't look like", self.send_photo(t))
        self.assertFalse(Payout.objects.exists())

    def test_without_ai_every_photo_waits_for_staff(self):
        t = ChatTester()
        t.engine.cleaning_judge = None
        t.engine.checkout = None
        self.assertIn('supervisor', self.send_photo(t))
        self.assertEqual(CleaningCheck.objects.get().status, CleaningCheck.Status.REVIEW)

    def test_a_photo_from_someone_else_is_answered_as_before(self):
        self.cleaner.delete()
        t, judge = self.chat()
        out = self.send_photo(t)
        self.assertNotIn('cleaning check', out)
        self.assertEqual(judge.seen, [])

    def test_photos_arrive_through_the_message_api(self):
        from base64 import b64encode
        from unittest import mock
        t, _ = self.chat(verdict(5))
        body = {'userId': 'u9', 'phone': PHONE, 'type': 'image', 'mimetype': 'image/png',
                'image': b64encode(photo(4)).decode()}
        with mock.patch('bot.handler.default_engine', return_value=t.engine), \
             self.settings(TAPFLASH=t.config.__class__(**{**t.config.__dict__, 'web_chat': True})):
            response = self.client.post('/api/message', json.dumps(body), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('Approved!', json.dumps(response.json()))
        self.assertEqual(bytes(CleaningCheck.objects.get().photo), photo(4))


class PhotoCopyTests(TestCase):
    """A re-saved copy of the restroom's own photo (WhatsApp recompresses it) is not a new cleaning."""

    def setUp(self):
        from pathlib import Path

        from django.core.files.base import ContentFile
        self.photos = Path(__file__).resolve().parents[2] / 'restrooms' / 'demo_photos'
        self.restroom = Restroom.objects.create(code='city-market', name='City Market', latitude=-1.28,
                                                longitude=36.82)
        self.restroom.photo.save('city-market.jpg', ContentFile((self.photos / '1.jpg').read_bytes()), save=True)
        self.cleaner = Cleaner.objects.create(name='Achieng', phone=PHONE, payout_email='a@example.com',
                                              restroom=self.restroom)

    def recompressed(self, name, quality=55, scale=0.6):
        from io import BytesIO

        from PIL import Image
        with Image.open(self.photos / name) as image:
            image = image.convert('RGB').resize((int(image.width * scale), int(image.height * scale)))
            out = BytesIO()
            image.save(out, 'JPEG', quality=quality)
            return out.getvalue()

    def send(self, photo, judge):
        from bot.config import build_config
        from ops.cleaning import check_photo
        return check_photo(cleaner=self.cleaner, user_id='u', channel='web', photo=photo, mimetype='image/jpeg',
                           judge=judge, checkout=None, config=build_config({}))

    def test_the_restrooms_own_photo_sent_back_is_refused(self):
        judge = FakeJudge(verdict(5))
        self.assertIn('sent before', self.send(self.recompressed('1.jpg'), judge))
        self.assertEqual(judge.seen, [])  # not even shown to Gemini
        self.assertFalse(CleaningCheck.objects.exists())

    def test_a_copy_of_an_earlier_cleaning_photo_is_refused(self):
        self.send((self.photos / '2.jpg').read_bytes(), FakeJudge(verdict(5)))
        self.assertEqual(CleaningCheck.objects.count(), 1)
        self.cleaner.checks.update(created_at=timezone.now() - timedelta(days=2))  # past the time limits
        self.assertIn('sent before', self.send(self.recompressed('2.jpg', quality=40), FakeJudge(verdict(5))))
        self.assertEqual(CleaningCheck.objects.count(), 1)

    def test_a_different_photo_goes_through(self):
        self.assertIn('Approved!', self.send((self.photos / '5.jpg').read_bytes(), FakeJudge(verdict(5))))
