import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from bot.config import build_config
from bot.engine import Engine
from channels.management.commands.simulate import SAMPLE_LOCATION, parse_location


class SimulateTests(TestCase):
    def test_a_piped_chat(self):
        engine = Engine(build_config({}))
        out = StringIO()
        script = 'hi\n/loc\n1\n/quit\nnever read\n'
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(sys, 'stdin', StringIO(script)), \
                mock.patch('bot.handler.default_engine', lambda: engine):
            call_command('simulate', out=folder, stdout=out)
            saved = list(Path(folder).iterdir())
        text = out.getvalue()
        self.assertIn('TapFlash simulator. You are +254712345678.', text)
        self.assertIn('You: hi', text)
        self.assertIn('*1.* Pay', text)
        self.assertNotIn('never read', text)
        self.assertEqual(saved, [])  # no pictures without the pictures collaborator

    def test_locations(self):
        self.assertEqual(parse_location(''), SAMPLE_LOCATION)
        self.assertEqual(tuple(parse_location('-1.29, 36.82')), (-1.29, 36.82))
