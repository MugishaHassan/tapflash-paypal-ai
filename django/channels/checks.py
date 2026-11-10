from django.conf import settings
from django.core.checks import Warning, register


@register()
def whatsapp_settings(app_configs, **kwargs):
    config = settings.TAPFLASH
    if not config.whatsapp_token:
        return []  # Meta is not set up yet
    problems = []
    if not config.whatsapp_app_secret:
        problems.append(Warning(
            'WHATSAPP_APP_SECRET is not set, so webhook signatures are not checked.',
            hint='Copy the App secret from Meta (App settings > Basic) into .env.', id='channels.W001',
        ))
    for name, value in (('WHATSAPP_PHONE_NUMBER_ID', config.whatsapp_phone_number_id),
                        ('WHATSAPP_VERIFY_TOKEN', config.whatsapp_verify_token)):
        if not value:
            problems.append(Warning(f'{name} is missing, so WhatsApp messages cannot be answered.',
                                    hint='Add it to .env (see .env.example).', id='channels.W002'))
    return problems
