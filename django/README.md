# TapFlash (Django)

A WhatsApp bot that finds the nearest paid restroom, takes a (test) payment
and sends an access code with a QR code, a route map and pictures. This is
the Django version of the Node.js bot in the folder above, with the same
wording and behaviour.

## Run it on your computer (Windows)

Run these inside the `django` folder:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

In `.env` set `DEBUG=true` (a computer only). Then:

```
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py createsuperuser
.venv\Scripts\python.exe manage.py runserver 8000
```

- http://localhost:8000/ is the browser test chat (on while `DEBUG=true`).
- http://localhost:8000/admin/ is the admin: restrooms, payments, passes,
  problem reports and chats.
- `manage.py simulate` chats in the terminal. Type `hi`, then `/loc` to share
  a sample location (or `/loc -1.29,36.82`), `/quit` to leave. Pictures are
  saved to `out/`.
- `manage.py import_restrooms restrooms/sample_restrooms.json` loads the five
  sample restrooms. Set `DEMO_RESTROOMS_NEAR_USER=false` to use the admin's
  restrooms instead of invented ones.

Tests: `.venv\Scripts\python.exe manage.py test` (no network needed).

## Addresses

| Path | What it is |
|---|---|
| `/` | The test chat page when `WEB_CHAT` is on, otherwise "TapFlash is running." |
| `/api/message` | JSON chat endpoint for the test chat page and the WhatsApp Web bridge (`BRIDGE_TOKEN`) |
| `/webhook` | Meta WhatsApp Cloud API webhook (GET verify, POST messages) |
| `/admin/` | Admin (or `ADMIN_PATH`) |

## How it fits together

- `bot/`: the conversation (`engine.py`), its texts (`messages.py`) and
  `handler.handle_message`, which every channel calls. Each user's turn runs
  with their chat row locked in the database, so several server processes
  answer one user's messages in order.
- `channels/`: Meta webhook and Graph API client (`cloud_api.py`, `inbox.py`),
  the test chat and bridge (`web_chat.py`), the `simulate` and `drain_inbox`
  commands.
- `pictures/`: thumbnails, payment icons, the access card, route maps
  (OpenStreetMap tiles and OSRM walking routes, cached in `.cache/`) and QR
  codes, drawn with Pillow. If Pillow can't load, the bot sends text only.
  The font is Barlow (SIL Open Font License 1.1, `pictures/fonts/OFL.txt`).
- `restrooms/`, `payments/`: models, admin, restroom search and the test
  payment provider.

Every setting is explained in `.env.example`.
