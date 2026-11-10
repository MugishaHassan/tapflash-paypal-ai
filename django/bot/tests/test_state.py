import json
from datetime import datetime, timezone
from decimal import Decimal

from django.test import SimpleTestCase

from bot.state import PaymentDraft, Session, Step
from payments.methods import PaymentMethod
from restrooms.facility import Facility
from restrooms.geo import Location

MALL = Facility(
    code='demo-1', name='City Mall Restroom (demo)', area='Shopping mall', lat=-1.284, lng=36.819,
    price=Decimal('100.00'), hours='6:00 AM – 10:00 PM', distance_km=0.39,
)
CARD = PaymentMethod('card', 'Card (Visa / Mastercard)', needs_phone=False, fee_percent=Decimal('1.5'))


class SessionJsonTests(SimpleTestCase):
    def test_a_session_comes_back_from_json_exactly(self):
        session = Session(
            step=Step.AUTHORIZE,
            location=Location(-1.2864, 36.8172),
            options=[MALL],
            restroom=MALL,
            method=CARD,
            payment=PaymentDraft(
                facility=MALL, method=CARD, phone=None, price=Decimal('100.00'), fee=Decimal('1.50'),
                total=Decimal('101.50'), closes_at=datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc),
            ),
            pass_reference='TF0123456789',
            fail_reason='Declined.',
            return_step=Step.NAVIGATING,
            choices={'authorize payment': '1', 'cancel': '2'},
            whatsapp_phone='+254712345678',
        )
        # Through text, as the database stores it.
        back = Session.from_json(json.loads(json.dumps(session.to_json())))
        self.assertEqual(back, session)
        self.assertEqual(back.payment.total, Decimal('101.50'))
        self.assertEqual(back.payment.closes_at.utcoffset().total_seconds(), 0)

    def test_a_new_session_round_trips_too(self):
        self.assertEqual(Session.from_json(json.loads(json.dumps(Session().to_json()))), Session())

    def test_a_step_this_version_does_not_know_is_refused(self):
        with self.assertRaises(ValueError):
            Session.from_json({'step': 'old_step_name'})
