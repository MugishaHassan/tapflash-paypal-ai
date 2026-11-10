"""Every text the bot sends, so the wording can be changed in one place.

*text* is bold in WhatsApp. Choices are not written here: the engine sends
them as menu options, and each channel shows them its own way.
"""
from datetime import datetime

from restrooms.geo import describe_distance

from .formatting import format_money, format_time

SHARE_LOCATION_HELP = 'Tap 📎 (or +), then *Location*, then *Send your current location*.'


class Messages:
    def __init__(self, config):
        self.config = config

    def _money(self, amount):
        return format_money(amount, self.config.currency)

    def _time(self, when):
        return format_time(when, self.config.timezone)

    def greeting(self):
        return (
            f"Hello, I'm *{self.config.bot_name} AI*, an AI assistant that helps you find "
            'the nearest clean, convenient and accessible washroom or toilet.'
        )

    def ask_need(self):
        return 'What are you looking for?'

    def no_match(self, need):
        return f"I couldn't find *{need.lower()}* near you, so here are the closest restrooms."

    def need_closed(self, need):
        return (
            f'I found *{need.lower()}* near you, but it is closed now, '
            'so here are the closest open restrooms.'
        )

    def need_noted(self, need):
        return f'Got it: *{need}*.'


    def active_pass_reminder(self, access_pass):
        until = self._time(access_pass.expires_at)
        return f'You still have an access pass for *{access_pass.facility_name}* until {until}. Reply *pass* to see it.'

    def ask_location(self):
        return f"Share your location and I'll find restrooms near you.\n\n{SHARE_LOCATION_HELP}"

    def need_location(self):
        return f"I don't have your location yet.\n\n{SHARE_LOCATION_HELP}"

    def bad_location(self):
        return "I couldn't read that location."

    def timed_out(self, nothing_charged):
        return 'Our chat timed out, so I have started again.' + (' Nothing was charged.' if nothing_charged else '')

    def none_nearby(self):
        return (
            f"I couldn't find a restroom within {self.config.max_distance_km} km of you. "
            'Share a different location, or reply *exit* to leave.'
        )

    def all_closed(self):
        return (
            'The restrooms near you are closed now, or will be before you get there. '
            'Share a different location, or try again later.'
        )

    def all_paid_for(self):
        return (
            'You already have an access pass for the open restrooms near you. '
            'Reply *pass* to see it, or share a different location.'
        )

    def closed_on_arrival(self, facility):
        return (
            f'*{facility.name}* will be closed by the time you get there (open {facility.hours}), '
            'so nothing was charged. Please choose another restroom.'
        )

    def choose_restroom(self):
        return 'Here are the closest restrooms. Which one would you like?'

    @staticmethod
    def _cleaned_ago(facility, now):
        """'25 min ago' for a cleaning a checked photo proved in the last 24 hours, else ''."""
        if not facility.cleaned_at or now is None:
            return ''
        minutes = int((now - datetime.fromisoformat(facility.cleaned_at)).total_seconds() // 60)
        if minutes < 0 or minutes >= 24 * 60:
            return ''
        if minutes < 1:
            return 'just now'
        return f'{minutes} min ago' if minutes < 60 else f'{minutes // 60} h ago'

    def restroom_details(self, facility, now=None):
        cleaned = self._cleaned_ago(facility, now)
        return (f'{describe_distance(facility.distance_km)}, {self._money(facility.price)}'
                + (f', cleaned {cleaned}' if cleaned else ''))

    def pay_prompt(self, facility, now=None):
        hours = f'\nOpen {facility.hours}' if facility.hours else ''
        cleaned = self._cleaned_ago(facility, now)
        checked = 'the cleaner\'s photo was checked by AI' if facility.cleaned_checked_by_ai else 'a supervisor checked the photo'
        cleaned = f'\nCleaned {cleaned} ({checked})' if cleaned else ''
        return (
            f'*{facility.name}*\n'
            f'{describe_distance(facility.distance_km)}\n'
            f'Price: {self._money(facility.price)}{hours}{cleaned}\n\n'
            'Pay to get the route and your access code.'
        )

    def choose_payment_method(self):
        return 'How would you like to pay?'

    def method_fee(self, method):
        return f'{method.fee_percent}% fee' if method.fee_percent else 'No fees'

    def ask_phone(self, method):
        return f'Enter your {method.label} number, for example 0712 345 678.'

    def use_whatsapp_number(self, phone):
        return f'Use {phone}'

    def bad_phone(self, method):
        return (
            f"That doesn't look like an {method.label} number. "
            f'Send it like 0712 345 678 or +{self.config.country_code} 712 345 678.'
        )

    def payment_summary(self, draft, test_mode, paypal_amount=None):
        phone = f' ({draft.phone})' if draft.phone else ''
        fee = self._money(draft.fee) if draft.fee else 'none'
        text = (
            '*Payment summary*\n'
            f'Restroom: {draft.facility.name}\n'
            f'Method: {draft.method.label}{phone}\n'
            f'Price: {self._money(draft.price)}\n'
            f'Fee: {fee}\n'
            f'*Total: {self._money(draft.total)}*'
        )
        if paypal_amount is not None:
            text += f'\nPayPal charges *{format_money(paypal_amount, self.config.paypal_currency)}* for this.'
        if draft.closes_at:
            text += f'\n\nThe restroom closes at {self._time(draft.closes_at)}, so your pass ends then.'
        if test_mode:
            text += '\n\nTest mode: no real money is taken.'
        return text

    def checkout_link(self, draft, test_mode):
        amount = format_money(draft.checkout_amount, draft.checkout_currency)
        if draft.method.id == 'card':
            how = "Tap the link to pay by card on PayPal's secure page. You don't need a PayPal account."
        else:
            how = 'Tap the link to pay with your PayPal account (or a card).'
        text = (
            f'*Pay {amount}* ({self._money(draft.total)}) for {draft.facility.name}\n\n'
            f'{how}\n{draft.checkout_url}\n\n'
            'Your receipt and access code come here as soon as PayPal confirms the payment.'
        )
        if test_mode:
            text += '\n\nPayPal sandbox: log in with a sandbox test account. No real money is taken.'
        return text

    def place_not_found(self, place):
        return f"I couldn't find *{place}* on the map of Nairobi. Share your location instead, or write another place."

    def searching_near(self, place):
        return f'Looking for restrooms near *{place}*.'

    def refunded(self, refund, payment):
        amount = format_money(refund.amount, refund.currency)
        ref = refund.provider_refund_id or ''
        if ref.startswith('VOID-'):
            return f'PayPal released the hold of *{amount}*: nothing was taken. Reference *{ref[5:]}*.'
        if ref.startswith('HOLD-'):
            return f'Only part of the held payment was taken: *{amount}* stays with you. Reference *{ref[5:]}*.'
        if payment.provider == 'tfpass':
            return 'The visit went back to your *TapFlash Pass*.'
        where = 'your PayPal' if payment.provider == 'paypal' else f'your {payment.get_method_display()}'
        test = '' if payment.provider == 'paypal' else ' (test mode: no real money moves)'
        return f'Refunded *{amount}* to {where}{test}. Refund reference *{refund.provider_refund_id}*.'

    def refund_for_staff(self):
        return 'A staff member will check your report today and refund you if it is due.'

    def checkout_not_finished(self):
        return "PayPal hasn't confirmed your payment yet. Finish paying with the link, then tap *I have paid*."

    def checkout_cancelled(self):
        return 'You cancelled the PayPal payment. Nothing was charged.'

    def payment_failed(self, reason):
        return f'Payment failed. {reason}'

    def tfpass_offer(self, offer, link):
        return (f'You have no *TapFlash Pass* visits yet. A pass is {offer.visits_total} visits for '
                f'{offer.currency} {offer.price} in one PayPal payment ({offer.currency} {offer.price / offer.visits_total:.2f} a visit):\n{link}\n\n'
                'Or choose another way to pay.')

    def payment_received(self, reference, provider=None):
        if provider == 'tfpass':
            from payments.tfpass import for_reference
            found = for_reference(reference)
            left = f' {found.visits_left} visits left.' if found else ''
            return f'Paid with your *TapFlash Pass*.{left} Reference *{reference}*.\nScan the QR code to see your payment details.'
        held = ('\nPayPal is holding the money: it is only taken when the attendant scans your code at the door.'
                if self.config.paypal_hold and provider == 'paypal' else '')
        return f'Payment received. Reference *{reference}*.{held}\nScan the QR code to see your payment details.'

    def receipt_text(self, access_pass, date_time):
        phone = f' {access_pass.phone}' if access_pass.phone else ''
        return (
            f'{self.config.bot_name} receipt\n'
            f'Ref: {access_pass.reference}\n'
            f'Restroom: {access_pass.facility_name}\n'
            f'Method: {access_pass.method_label}{phone}\n'
            f'Amount: {self._money(access_pass.total)}\n'
            f'Paid: {date_time}'
        )

    def searching_route(self, facility):
        return f'Finding the shortest route to *{facility.name}*…'

    def start_journey(self, url):
        return f"Start walking, I'll guide you. Open the route in Google Maps:\n{url}"

    def arrived(self, facility):
        return f'You have arrived at *{facility.name}*.'

    def access_details(self, access_pass):
        return (
            '*Your access details*\n'
            f'Restroom: {access_pass.facility_name}\n'
            f'Amount paid: {self._money(access_pass.total)}\n'
            f'Access code (OTP): *{access_pass.otp}*\n'
            f'Valid until: *{self._time(access_pass.expires_at)}*\n\n'
            'Show the QR code at the entrance, or enter the access code.'
        )

    def access_card_caption(self, access_pass):
        """Under the access card, which already shows the details."""
        return (
            f'Show this at the entrance of *{access_pass.facility_name}*, or enter the access code. '
            f'Valid until {self._time(access_pass.expires_at)}.'
        )

    def otp(self, access_pass):
        return f'Your access code (OTP) is *{access_pass.otp}*. It works until {self._time(access_pass.expires_at)}.'

    def what_next(self):
        return 'What would you like to do?'

    def done(self, access_pass):
        return (
            f'Enjoy! Your pass works until {self._time(access_pass.expires_at)}. Reply *pass* to see it again, '
            'or *hi* to find another restroom.'
        )

    def no_pass(self):
        return "You don't have an active access pass. Send *hi* to find a restroom."

    def pass_ended(self, access_pass):
        return (
            f'Your access pass for *{access_pass.facility_name}* expired at {self._time(access_pass.expires_at)}. '
            'Send *hi* to find a restroom.'
        )

    def pass_expired(self, access_pass):
        """The journey can't go on: the pass ran out before it was used."""
        return (
            f'Your access pass for *{access_pass.facility_name}* expired at {self._time(access_pass.expires_at)}. '
            'If you could not use it, report the problem and our team will look into it.'
        )

    def pass_refunded(self, access_pass):
        """The visit was refunded, so its code no longer works."""
        return (
            f'Your visit to *{access_pass.facility_name}* was refunded, so its access code no longer works. '
            'Find another restroom?'
        )

    def ask_report(self):
        return (
            'What\'s wrong? Describe it in one message, for example "The restroom is closed" or '
            '"I can\'t find the entrance".'
        )

    def describe_in_words(self):
        return 'Please describe the problem in words.'

    def report_thanks(self):
        return 'Thank you. Our team has your report and will look into it.'

    def goodbye(self, access_pass):
        text = f'Thanks for using {self.config.bot_name}. Send *hi* any time to find a restroom.'
        if access_pass:
            text += f'\n\nYour access pass works until {self._time(access_pass.expires_at)}. Reply *pass* to see it.'
        return text

    def did_not_understand(self):
        return "Sorry, I didn't get that. Please pick one of the options."

    def location_noted(self):
        return 'Got your location.'

    def text_only(self):
        return 'I can only read text and shared locations for now.'

    def help(self):
        return (
            f'*How to use {self.config.bot_name}*\n'
            'Send *hi* to start a new search.\n'
            'Share your location to see restrooms near you.\n'
            'Reply with the number of an option.\n'
            'Send *pass* to see your access pass.\n'
            'Send *exit* to stop at any time.'
        )
