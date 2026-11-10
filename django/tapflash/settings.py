"""Django settings for TapFlash.

Everything that differs between a computer and the server comes from
environment variables, or from the .env file next to manage.py. All of them
are explained in .env.example.
"""
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from bot.config import build_config, load_env_file, read_bool, read_int, read_list, read_str

BASE_DIR = Path(__file__).resolve().parent.parent

load_env_file(BASE_DIR / '.env')
env = os.environ

DEBUG = read_bool(env, 'DEBUG', False)
SECRET_KEY = read_str(env, 'SECRET_KEY')
if not SECRET_KEY:
    if not DEBUG and 'test' not in sys.argv[1:2]:
        raise ImproperlyConfigured('SECRET_KEY must be set when DEBUG is off (see .env.example).')
    SECRET_KEY = 'django-insecure-local-development-only'

# The bot's own settings: prices, time zone, WhatsApp keys and so on.
TAPFLASH = build_config(env)

ALLOWED_HOSTS = read_list(env, 'ALLOWED_HOSTS')
# Cleaners' photos come in as JSON (up to 5 MB in base64); other messages stay under 64 KB.
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
CSRF_TRUSTED_ORIGINS = read_list(env, 'CSRF_TRUSTED_ORIGINS')
# The WhatsApp Web bridge on the owner's PC sends "Authorization: Bearer <BRIDGE_TOKEN>"
# to /api/message, which is then accepted even with the web chat off.
BRIDGE_TOKEN = read_str(env, 'BRIDGE_TOKEN')
ADMIN_PATH = read_str(env, 'ADMIN_PATH', 'admin').strip('/') or 'admin'
GATE_LOGIN_URL = '/gate/login/'
# The judges' page (/judges/). JUDGE_ONE_CLICK lets anyone open the dashboard and the door check as the
# limited "judge" user with one click: for a sandbox demo only. The sandbox buyer login is shown there too.
JUDGE_ONE_CLICK = read_bool(env, 'JUDGE_ONE_CLICK', False)
DEMO_BUYER_EMAIL = read_str(env, 'DEMO_BUYER_EMAIL')
DEMO_BUYER_PASSWORD = read_str(env, 'DEMO_BUYER_PASSWORD')
# Money actions (payouts, refunds, cleaning approvals) the shared judge login may take per day, so one visitor
# cannot use up the sandbox balance before the judges arrive.
JUDGE_MONEY_ACTIONS_PER_DAY = read_int(env, 'JUDGE_MONEY_ACTIONS_PER_DAY', 60)
# Septic tanks: sensors post readings with SENSOR_TOKEN (X-Sensor-Token header); emptying is ordered from the alert.
SENSOR_TOKEN = read_str(env, 'SENSOR_TOKEN')
EMPTYING_PROVIDER = read_str(env, 'EMPTYING_PROVIDER', 'City Exhausters (sample company)')
EMPTYING_PRICE_USD = read_str(env, 'EMPTYING_PRICE_USD', '25.00')
EMPTYING_MAX_USD = read_str(env, 'EMPTYING_MAX_USD', '60.00')
# Messages one address may send to the web chat in a minute (the tests send many more).
CHAT_MESSAGES_PER_MINUTE = 10 ** 6 if 'test' in sys.argv[1:2] else read_int(env, 'CHAT_MESSAGES_PER_MINUTE', 40)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'restrooms',
    'payments',
    'bot',
    'channels',
    'ops',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'tapflash.urls'
WSGI_APPLICATION = 'tapflash.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

DB_ENGINE = read_str(env, 'DB_ENGINE', 'sqlite').lower()
if DB_ENGINE == 'sqlite':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': read_str(env, 'DB_NAME', str(BASE_DIR / 'db.sqlite3')),
            # Turns take the write lock when they start and wait for each other,
            # instead of failing with "database is locked".
            'OPTIONS': {'transaction_mode': 'IMMEDIATE', 'timeout': 20},
        }
    }
elif DB_ENGINE == 'mysql':
    # Shared hosting can't build mysqlclient, so the pure Python PyMySQL stands in for it.
    import pymysql

    pymysql.install_as_MySQLdb()
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': read_str(env, 'DB_NAME'),
            'USER': read_str(env, 'DB_USER'),
            'PASSWORD': env.get('DB_PASSWORD', ''),
            'HOST': read_str(env, 'DB_HOST', 'localhost'),
            'PORT': read_str(env, 'DB_PORT', '3306'),
            # Keep connections for a minute, and check them before reuse
            # (the server closes idle ones).
            'CONN_MAX_AGE': 60,
            'CONN_HEALTH_CHECKS': True,
            'OPTIONS': {
                'charset': 'utf8mb4',
                'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }
else:
    raise ImproperlyConfigured(f'DB_ENGINE must be sqlite or mysql, got "{DB_ENGINE}"')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-gb'
TIME_ZONE = TAPFLASH.timezone
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = Path(read_str(env, 'STATIC_ROOT', str(BASE_DIR / 'staticfiles')))
MEDIA_URL = 'media/'
MEDIA_ROOT = Path(read_str(env, 'MEDIA_ROOT', str(BASE_DIR / 'media')))
# Downloaded map tiles and routes.
CACHE_DIR = Path(read_str(env, 'CACHE_DIR', str(BASE_DIR / '.cache')))
# Django serves the uploaded photos itself, unless the web server does.
# The wrong-password counter of the /link page lives here, so both gunicorn
# workers share it and a restart does not reset it.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': str(CACHE_DIR / 'django'),
    }
}
SERVE_MEDIA = read_bool(env, 'SERVE_MEDIA', DEBUG)

# While developing, static files are found without collectstatic. Set here
# because tests turn DEBUG off once Django has started.
WHITENOISE_AUTOREFRESH = DEBUG

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = 'DENY'
# HTTPS: Apache on cPanel ends TLS and tells Passenger, so Django knows a
# request was secure. Turn SECURE_SSL_REDIRECT off if pages loop ("too many redirects").
SECURE_SSL_REDIRECT = read_bool(env, 'SECURE_SSL_REDIRECT', False)
SECURE_HSTS_SECONDS = read_int(env, 'SECURE_HSTS_SECONDS', 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = read_bool(env, 'SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
# BEHIND_PROXY: nginx on the VPS ends HTTPS and says so in X-Forwarded-Proto.
if read_bool(env, 'BEHIND_PROXY', False):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# The /link page shows the WhatsApp login QR code to whoever knows
# LINK_PASSWORD. Empty (the default) turns the page off. The WhatsApp
# connection writes its state to LINK_STATUS_FILE.
LINK_PASSWORD = read_str(env, 'LINK_PASSWORD')
LINK_STATUS_FILE = Path(read_str(env, 'LINK_STATUS_FILE', str(BASE_DIR.parent / 'records' / 'link.json')))

# HSTS preload is for whole domains, not a bot on a subdomain.
SILENCED_SYSTEM_CHECKS = ['security.W021']

# Warnings and errors go to stderr, which Passenger writes to the app's log.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'stderr': {'class': 'logging.StreamHandler', 'level': 'WARNING'}},
    'root': {'handlers': ['stderr']},
    'loggers': {
        'django': {'handlers': [], 'propagate': True},
        # Pages refused or not found, and requests for other host names (bots
        # probe for them all day), are not worth a log line.
        'django.request': {'level': 'ERROR'},
        'django.security.DisallowedHost': {'level': 'CRITICAL'},
    },
}
# LOG_FILE: also write warnings and errors to this file (kept to 5 files of 1 MB).
LOG_FILE = read_str(env, 'LOG_FILE')
if LOG_FILE:
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    LOGGING['handlers']['file'] = {
        'class': 'logging.handlers.RotatingFileHandler', 'filename': LOG_FILE, 'level': 'WARNING',
        'maxBytes': 1024 * 1024, 'backupCount': 4, 'encoding': 'utf-8', 'formatter': 'timed',
    }
    LOGGING['formatters'] = {'timed': {'format': '%(asctime)s %(levelname)s %(name)s: %(message)s'}}
    LOGGING['root']['handlers'].append('file')
