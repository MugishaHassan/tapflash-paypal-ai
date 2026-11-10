"""Entry point for Passenger ("Setup Python App" on cPanel)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tapflash.settings')

from django.core.wsgi import get_wsgi_application  # noqa: E402 (needs the path above)

application = get_wsgi_application()
