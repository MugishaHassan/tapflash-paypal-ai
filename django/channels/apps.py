from django.apps import AppConfig


class ChannelsConfig(AppConfig):
    name = 'channels'
    verbose_name = 'WhatsApp and web chat'

    def ready(self):
        from . import checks  # noqa: F401 (registers the system checks)
