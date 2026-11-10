"""Every message, word for word as the Node version's messages.js writes it.

The expected texts were produced by running messages.js with the same inputs.
"""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from django.test import SimpleTestCase

from bot.config import build_config
from bot.messages import Messages
from bot.state import PaymentDraft
from payments.methods import PaymentMethod
from payments.passes import Pass
from restrooms.facility import Facility

EXPIRES = datetime(2026, 10, 10, 7, 36, tzinfo=timezone.utc)
CITY_MARKET = Facility(
    code='nbi-city-market', name='City Market Restroom (sample)', area='', lat=-1.2833, lng=36.8219,
    price=Decimal(100), hours='6:00 AM \u2013 9:00 PM', distance_km=0.35,
)
RAILWAYS = Facility(
    code='nbi-railways', name='Railways', area='', lat=-1.2903, lng=36.8269, price=Decimal('101.5'),
    distance_km=12.7,
)
MPESA = PaymentMethod('mpesa', 'M-Pesa', needs_phone=True, fee_percent=Decimal(0))
CARD = PaymentMethod('card', 'Card (Visa / Mastercard)', needs_phone=False, fee_percent=Decimal('1.5'))
MPESA_PASS = Pass(
    reference='TF0123456789', facility_code=CITY_MARKET.code, facility_name=CITY_MARKET.name,
    method_label='M-Pesa', phone='+254712345678', total=Decimal('101.50'), otp='123456', expires_at=EXPIRES,
)
CARD_PASS = replace(MPESA_PASS, method_label=CARD.label, phone='')
MPESA_DRAFT = PaymentDraft(
    facility=CITY_MARKET, method=MPESA, phone='+254712345678', price=Decimal(100), fee=Decimal(0),
    total=Decimal(100),
)
CARD_DRAFT = PaymentDraft(
    facility=CITY_MARKET, method=CARD, phone=None, price=Decimal(100), fee=Decimal('1.50'),
    total=Decimal('101.50'), closes_at=EXPIRES,
)


class MessagesTests(SimpleTestCase):
    def test_every_message_has_the_node_wording(self):
        msg = Messages(build_config({}))
        for got, expected in [
            (msg.greeting(), "Hello, I'm *TapFlash AI*, an AI assistant that helps you find the nearest clean, convenient and accessible washroom or toilet."),
            (msg.active_pass_reminder(MPESA_PASS), "You still have an access pass for *City Market Restroom (sample)* until 10:36. Reply *pass* to see it."),
            (msg.ask_location(), "Share your location and I'll find restrooms near you.\n\nTap \U0001f4ce (or +), then *Location*, then *Send your current location*."),
            (msg.need_location(), "I don't have your location yet.\n\nTap \U0001f4ce (or +), then *Location*, then *Send your current location*."),
            (msg.bad_location(), "I couldn't read that location."),
            (msg.timed_out(False), "Our chat timed out, so I have started again."),
            (msg.timed_out(True), "Our chat timed out, so I have started again. Nothing was charged."),
            (msg.none_nearby(), "I couldn't find a restroom within 20 km of you. Share a different location, or reply *exit* to leave."),
            (msg.all_closed(), "The restrooms near you are closed now, or will be before you get there. Share a different location, or try again later."),
            (msg.all_paid_for(), "You already have an access pass for the open restrooms near you. Reply *pass* to see it, or share a different location."),
            (msg.closed_on_arrival(CITY_MARKET), "*City Market Restroom (sample)* will be closed by the time you get there (open 6:00 AM \u2013 9:00 PM), so nothing was charged. Please choose another restroom."),
            (msg.choose_restroom(), "Here are the closest restrooms. Which one would you like?"),
            (msg.restroom_details(CITY_MARKET), "350 m, about 5 min walk, KES 100"),
            (msg.restroom_details(RAILWAYS), "12.7 km, about 3 h 18 min walk, KES 101.50"),
            (msg.pay_prompt(CITY_MARKET), "*City Market Restroom (sample)*\n350 m, about 5 min walk\nPrice: KES 100\nOpen 6:00 AM \u2013 9:00 PM\n\nPay to get the route and your access code."),
            (msg.pay_prompt(RAILWAYS), "*Railways*\n12.7 km, about 3 h 18 min walk\nPrice: KES 101.50\n\nPay to get the route and your access code."),
            (msg.choose_payment_method(), "How would you like to pay?"),
            (msg.method_fee(MPESA), "No fees"),
            (msg.method_fee(CARD), "1.5% fee"),
            (msg.ask_phone(MPESA), "Enter your M-Pesa number, for example 0712 345 678."),
            (msg.use_whatsapp_number('+254712345678'), "Use +254712345678"),
            (msg.bad_phone(MPESA), "That doesn't look like an M-Pesa number. Send it like 0712 345 678 or +254 712 345 678."),
            (msg.payment_summary(MPESA_DRAFT, False), "*Payment summary*\nRestroom: City Market Restroom (sample)\nMethod: M-Pesa (+254712345678)\nPrice: KES 100\nFee: none\n*Total: KES 100*"),
            (msg.payment_summary(CARD_DRAFT, True), "*Payment summary*\nRestroom: City Market Restroom (sample)\nMethod: Card (Visa / Mastercard)\nPrice: KES 100\nFee: KES 1.50\n*Total: KES 101.50*\n\nThe restroom closes at 10:36, so your pass ends then.\n\nTest mode: no real money is taken."),
            (msg.payment_failed('The payment was declined.'), "Payment failed. The payment was declined."),
            (msg.payment_received('TF0123456789'), "Payment received. Reference *TF0123456789*.\nScan the QR code to see your payment details."),
            (msg.receipt_text(MPESA_PASS, '10 Oct 2026, 10:00'), "TapFlash receipt\nRef: TF0123456789\nRestroom: City Market Restroom (sample)\nMethod: M-Pesa +254712345678\nAmount: KES 101.50\nPaid: 10 Oct 2026, 10:00"),
            (msg.receipt_text(CARD_PASS, '10 Oct 2026, 10:00'), "TapFlash receipt\nRef: TF0123456789\nRestroom: City Market Restroom (sample)\nMethod: Card (Visa / Mastercard)\nAmount: KES 101.50\nPaid: 10 Oct 2026, 10:00"),
            (msg.searching_route(CITY_MARKET), "Finding the shortest route to *City Market Restroom (sample)*\u2026"),
            (msg.start_journey('https://www.google.com/maps/dir/?api=1'), "Start walking, I'll guide you. Open the route in Google Maps:\nhttps://www.google.com/maps/dir/?api=1"),
            (msg.arrived(CITY_MARKET), "You have arrived at *City Market Restroom (sample)*."),
            (msg.access_details(MPESA_PASS), "*Your access details*\nRestroom: City Market Restroom (sample)\nAmount paid: KES 101.50\nAccess code (OTP): *123456*\nValid until: *10:36*\n\nShow the QR code at the entrance, or enter the access code."),
            (msg.access_card_caption(MPESA_PASS), "Show this at the entrance of *City Market Restroom (sample)*, or enter the access code. Valid until 10:36."),
            (msg.otp(MPESA_PASS), "Your access code (OTP) is *123456*. It works until 10:36."),
            (msg.what_next(), "What would you like to do?"),
            (msg.done(MPESA_PASS), "Enjoy! Your pass works until 10:36. Reply *pass* to see it again, or *hi* to find another restroom."),
            (msg.no_pass(), "You don't have an active access pass. Send *hi* to find a restroom."),
            (msg.pass_ended(MPESA_PASS), "Your access pass for *City Market Restroom (sample)* expired at 10:36. Send *hi* to find a restroom."),
            (msg.pass_expired(MPESA_PASS), "Your access pass for *City Market Restroom (sample)* expired at 10:36. If you could not use it, report the problem and our team will look into it."),
            (msg.ask_report(), "What's wrong? Describe it in one message, for example \"The restroom is closed\" or \"I can't find the entrance\"."),
            (msg.describe_in_words(), "Please describe the problem in words."),
            (msg.report_thanks(), "Thank you. Our team has your report and will look into it."),
            (msg.goodbye(None), "Thanks for using TapFlash. Send *hi* any time to find a restroom."),
            (msg.goodbye(MPESA_PASS), "Thanks for using TapFlash. Send *hi* any time to find a restroom.\n\nYour access pass works until 10:36. Reply *pass* to see it."),
            (msg.did_not_understand(), "Sorry, I didn't get that. Please pick one of the options."),
            (msg.location_noted(), "Got your location."),
            (msg.text_only(), "I can only read text and shared locations for now."),
            (msg.help(), "*How to use TapFlash*\nSend *hi* to start a new search.\nShare your location to see restrooms near you.\nReply with the number of an option.\nSend *pass* to see your access pass.\nSend *exit* to stop at any time."),
        ]:
            with self.subTest(expected[:40]):
                self.assertEqual(got, expected)

    def test_the_bot_name_and_settings_come_from_the_config(self):
        msg = Messages(build_config({'BOT_NAME': 'Choo', 'COUNTRY_CODE': '255', 'MAX_DISTANCE_KM': '2.5'}))
        self.assertIn("I'm *Choo AI*", msg.greeting())
        self.assertIn('+255 712 345 678', msg.bad_phone(MPESA))
        self.assertIn('within 2.5 km of you', msg.none_nearby())
