"""A login for hackathon judges: it opens the operators' dashboard (/ops/) and
nothing else in the admin (staff, but no admin permissions).

    python manage.py judge_login            # creates "judge" with a new password, or gives it a new one
"""
import secrets

from django.contrib.auth import get_user_model
from django.core.management import BaseCommand


class Command(BaseCommand):
    help = 'Create (or reset) the "judge" login for the operators dashboard.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='judge')

    def handle(self, *args, username, **options):
        password = secrets.token_urlsafe(9)
        user, created = get_user_model().objects.get_or_create(username=username)
        user.is_staff, user.is_superuser, user.is_active = True, False, True
        user.set_password(password)
        user.save()
        user.user_permissions.clear()
        self.stdout.write(self.style.SUCCESS(
            f'{"Created" if created else "Reset"} the dashboard login. Username: {username}  Password: {password}\n'
            'It opens /ops/ only. Put it in the Devpost testing instructions, not in the public README.'
        ))
