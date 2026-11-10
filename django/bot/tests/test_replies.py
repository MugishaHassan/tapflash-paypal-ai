from django.test import SimpleTestCase

from bot.engine import EXIT, choices_in, normalize_words, parse_choice
from bot.replies import Menu, Option, Text, menu_text, option_line

PAYMENT = Menu('How would you like to pay?', [
    Option('1', 'M-Pesa', description='No fees'),
    Option('2', 'Card', description='1% fee'),
    Option('0', 'Back', secondary=True),
])


class MenuTextTests(SimpleTestCase):
    def test_question_choices_with_descriptions_then_quiet_choices(self):
        self.assertEqual(
            menu_text(PAYMENT),
            'How would you like to pay?\n\n*1.* M-Pesa\n_No fees_\n\n*2.* Card\n_1% fee_\n\n*0.* Back',
        )
        simple = Menu('Ready?', [Option('1', 'Yes'), Option('2', 'No')])
        self.assertEqual(menu_text(simple), 'Ready?\n\n*1.* Yes\n*2.* No')

    def test_options_typed_as_a_word_show_their_hint_instead_of_a_number(self):
        menu = Menu('Pick one', [Option('1', 'A'), EXIT])
        self.assertEqual(menu_text(menu), 'Pick one\n\n*1.* A\nReply *exit* to leave.')
        self.assertEqual(option_line(EXIT), 'Reply *exit* to leave.')

    def test_a_menu_without_choices_is_just_the_question(self):
        self.assertEqual(menu_text(Menu('Hello', [])), 'Hello')


class TypedChoiceTests(SimpleTestCase):
    def test_words_are_compared_without_case_spaces_or_trailing_punctuation(self):
        self.assertEqual(normalize_words('Hi!'), 'hi')
        self.assertEqual(normalize_words('help?'), 'help')
        self.assertEqual(normalize_words('  My   Pass '), 'my pass')
        self.assertEqual(normalize_words('x' * 41), '', 'longer than any command')

    def test_the_numbered_choices_of_the_last_menu_can_be_typed_by_name(self):
        replies = [Text('Hi'), Menu('Old', [Option('1', 'Old one')]), PAYMENT]
        self.assertEqual(choices_in(replies), {'m-pesa': '1', 'card': '2', 'back': '0'})
        self.assertEqual(choices_in([Menu('Pick', [Option('1', 'A'), EXIT])]), {'a': '1'}, 'Exit is a word already')
        self.assertEqual(choices_in([Text('Bye')]), {})
        long_name = 'The Very Long Named Public Restroom At The Main Bus Terminal'
        self.assertEqual(choices_in([Menu('Pick', [Option('1', long_name)])]), {}, 'too long to type')

    def test_spaces_are_read_as_javascript_reads_them(self):
        self.assertEqual(parse_choice('\ufeff1'), 1)
        self.assertEqual(normalize_words('my\ufeffpass'), 'my pass')
        self.assertEqual(normalize_words('hi\u0085'), 'hi\u0085', 'not a space in JavaScript')
        self.assertEqual(normalize_words('my\u001cpass'), 'my\u001cpass')
