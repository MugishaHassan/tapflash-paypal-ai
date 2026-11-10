"""Chat with the bot in the terminal, no phone needed. Pictures are saved to
the out/ folder next to manage.py.

    /loc              share a sample location (Nairobi CBD)
    /loc -1.29,36.82  share a specific location
    /quit             leave
"""
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from bot.engine import Incoming
from bot.handler import handle_message
from bot.replies import menu_text
from channels.web_chat import coordinate
from restrooms.geo import Location

SAMPLE_LOCATION = Location(-1.285, 36.82)  # Kenyatta Avenue, Nairobi CBD
USER_ID = 'console-user'


def parse_location(arg):
    if not arg:
        return SAMPLE_LOCATION
    lat, _, lng = arg.partition(',')
    return Location(coordinate(lat.strip()), coordinate(lng.strip()))


class Command(BaseCommand):
    help = 'Chat with the bot in the terminal. Type "/loc" to share a location, "/quit" to leave.'

    def add_arguments(self, parser):
        parser.add_argument('--out', default=str(Path(settings.BASE_DIR) / 'out'),
                            help='Folder for the pictures the bot sends.')

    def handle(self, *args, **options):
        config = settings.TAPFLASH
        self.name = config.bot_name
        self.out = Path(options['out'])
        phone = f'+{config.country_code}712345678'
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        interactive = sys.stdin.isatty()
        self.stdout.write(f'\n{self.name} simulator. You are {phone}.')
        self.stdout.write('   Type "hi" to begin, "/loc" to share a location, "/quit" to leave.\n')
        while True:
            if interactive:
                self.stdout.write('You: ', ending='')
                self.stdout.flush()
            line = sys.stdin.readline()
            if not line:
                break
            text = line.strip()
            if not interactive:
                self.stdout.write(f'You: {text}')
            if text == '/quit':
                break
            if text.startswith('/loc'):
                incoming = Incoming(USER_ID, 'location', location=parse_location(text[4:].strip()), phone=phone)
            else:
                incoming = Incoming(USER_ID, 'text', text=text, phone=phone)
            handle_message(incoming, self.show)

    def show(self, reply):
        if reply.kind == 'menu':
            if reply.image:
                self.show(reply.image)
            self.stdout.write(f'\n{self.name}:\n{menu_text(reply)}\n')
        elif reply.kind == 'text':
            self.stdout.write(f'\n{self.name}:\n{reply.text}\n')
        else:
            self.out.mkdir(parents=True, exist_ok=True)
            path = self.out / Path(reply.filename or 'image.png').name
            path.write_bytes(reply.data)
            self.stdout.write(f'\n{self.name}: [image: {path}]')
            if reply.caption:
                self.stdout.write(reply.caption)
            self.stdout.write('')
