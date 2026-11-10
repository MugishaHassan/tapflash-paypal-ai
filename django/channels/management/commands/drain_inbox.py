"""Answer WhatsApp messages a stopped process left pending. Run it from cron
every minute: rows older than 10 minutes are given up, not answered."""
from django.conf import settings
from django.core.management.base import BaseCommand

from channels import inbox
from channels.cloud_api import GraphClient


class Command(BaseCommand):
    help = 'Answer pending WhatsApp messages (run every minute from cron).'

    def handle(self, *args, **options):
        users = inbox.pending_users()
        client = GraphClient(settings.TAPFLASH)
        for user_id in users:
            inbox.answer_user(user_id, client)
        if users and options['verbosity'] > 1:
            self.stdout.write(f'Answered pending messages of {len(users)} user(s).')
