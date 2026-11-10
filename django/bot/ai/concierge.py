"""The AI concierge: what a user means when they write instead of tapping.

"Nataka choo safi karibu na Kencom", "the second one please", "is it open
at night?". Gemini picks one action from ACTIONS; the engine carries it out
with the same rules as a tapped choice. The model never sees phone numbers,
and only the facts in the prompt may be used to answer questions.
"""
from restrooms.geo import describe_distance

NEED_KEYS = ['clean', 'convenient', 'accessible', 'disabled', 'baby']

SYSTEM = """You are the assistant inside TapFlash, a WhatsApp service in Nairobi, Kenya. TapFlash finds the nearest \
clean public restroom, takes payment (M-Pesa, Airtel Money, card or PayPal) and gives an access code to get in.

You read ONE message from a user and call ONE function that does what they want.
- Users write English, Swahili or Sheng, often short and with typos. Understand all of them.
- `reply` is one short sentence (at most 25 words) sent before the bot's own menu. Write it in the same language \
as the user's message (a message in English gets an English reply, in Swahili a Swahili one), warm and plain, with no emojis. Don't repeat the menu or list choices.
- Use only the facts given below. Never invent a restroom, a price, opening hours or a feature.
- Never promise refunds, payments or codes: the bot does those.
- A place name (street, building, stage, market, landmark) means the user wants restrooms near it.
- If the message picks one of the numbered choices on screen, by number, name or description, call pick_option."""

NEED = {'type': 'STRING', 'enum': NEED_KEYS,
        'description': 'clean: most clean; convenient: nearest/quickest; accessible: step-free; '
                       'disabled: toilet for people with disabilities; baby: baby changing.'}
REPLY = {'type': 'STRING', 'description': "One short sentence in the user's language."}

ACTIONS = [
    {'name': 'set_need', 'description': 'The user says what kind of restroom they need.',
     'parameters': {'type': 'OBJECT', 'properties': {
         'need': NEED,
         'place': {'type': 'STRING', 'description': 'A place in Nairobi they named, if any (else empty).'},
         'reply': REPLY}, 'required': ['need', 'reply']}},
    {'name': 'search_near', 'description': 'The user names a place and wants restrooms near it.',
     'parameters': {'type': 'OBJECT', 'properties': {
         'place': {'type': 'STRING', 'description': 'The place, as written, for example "Kencom" or "Archives".'},
         'reply': REPLY}, 'required': ['place', 'reply']}},
    {'name': 'pick_option', 'description': 'The user picks one of the numbered choices on screen.',
     'parameters': {'type': 'OBJECT', 'properties': {
         'number': {'type': 'INTEGER', 'description': 'The number of the choice.'},
         'reply': REPLY}, 'required': ['number', 'reply']}},
    {'name': 'answer_question', 'description': 'The user asks a question. Answer from the facts given only.',
     'parameters': {'type': 'OBJECT', 'properties': {
         'answer': {'type': 'STRING', 'description': "The answer, in the user's language, at most 50 words."}},
         'required': ['answer']}},
    {'name': 'report_problem', 'description': 'The user complains about a restroom or a payment.',
     'parameters': {'type': 'OBJECT', 'properties': {
         'problem': {'type': 'STRING', 'description': 'The problem in English, in a few words.'},
         'reply': REPLY}, 'required': ['problem', 'reply']}},
    {'name': 'not_understood', 'description': 'Nothing above fits.',
     'parameters': {'type': 'OBJECT', 'properties': {
         'reply': {'type': 'STRING', 'description': "Ask them, in their language, to say it another way."}},
         'required': ['reply']}},
]

STEP_NAMES = {
    'choose_need': 'choosing what kind of restroom they need',
    'await_location': 'asked to share their location',
    'choose_restroom': 'choosing a restroom from the list',
    'confirm_pay': 'deciding whether to pay for the chosen restroom',
    'choose_method': 'choosing how to pay',
    'await_phone': 'asked for the mobile money number to pay from',
    'authorize': 'looking at the payment summary',
    'await_checkout': 'paying on PayPal (a link was sent)',
    'payment_failed': 'after a failed payment',
    'navigating': 'walking to the restroom they paid for',
    'arrived': 'arrived at the restroom',
    'access': 'holding their access code',
    'pass_expired': 'their access pass has run out',
}


def facility_facts(facility):
    features = [label for flag, label in [
        (facility.accessible, 'step-free'), (facility.disabled_toilet, 'toilet for the disabled'),
        (facility.baby_changing, 'baby changing'),
    ] if flag]
    parts = [facility.name, facility.area, f'KES {facility.price:.0f}']
    if facility.distance_km is not None:
        parts.append(describe_distance(facility.distance_km))
    parts.append(f'open {facility.hours}' if facility.hours else 'always open')
    if facility.cleanliness:
        parts.append(f'cleanliness {facility.cleanliness}/5')
    if features:
        parts.append(', '.join(features))
    return '; '.join(str(part) for part in parts if part)


def build_prompt(session, menu_lines, text):
    lines = [f"The user is {STEP_NAMES.get(session.step or '', 'starting a chat')}."]
    if menu_lines:
        lines.append('Choices on screen:\n' + '\n'.join(menu_lines))
    if session.options and session.step == 'choose_restroom':
        lines.append('Restrooms on the list:\n' + '\n'.join(
            f'{number}. {facility_facts(facility)}' for number, facility in enumerate(session.options, start=1)
        ))
    if session.restroom:
        lines.append(f'Chosen restroom: {facility_facts(session.restroom)}')
    lines.append(f'The user wrote: """{text[:600]}"""')
    return '\n\n'.join(lines)


class Concierge:
    def __init__(self, client):
        self.client = client

    def decide(self, session, menu_lines, text):
        """(action name, args) or None."""
        return self.client.decide(system=SYSTEM, prompt=build_prompt(session, menu_lines, text), functions=ACTIONS)
