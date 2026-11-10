from django.conf import settings
from django.contrib import admin
from ops import septic_views
from payments import tfpass_views
from django.urls import include, path
from django.views.static import serve

from ops import judges as judges_views

admin.site.site_header = f'{settings.TAPFLASH.bot_name} admin'
admin.site.site_title = f'{settings.TAPFLASH.bot_name} admin'
admin.site.index_title = 'Restrooms, payments and reports'

urlpatterns = [
    path(f'{settings.ADMIN_PATH}/', admin.site.urls),
    path('ops/', include('ops.urls')),
    path('gate/', include('ops.gate_urls')),
    path('judges/', judges_views.judges, name='judges'),
    path('judges/enter/<str:place>', judges_views.judge_login, name='judge-enter'),
    path('judges/sample-photo/<str:kind>', judges_views.sample_photo, name='judge-sample-photo'),
    path('api/sensors', septic_views.sensor_api, name='sensor-api'),
    path('api/operator-link', septic_views.operator_link, name='operator-link'),
    path('empty/<str:token>', septic_views.emptying, name='emptying'),
    path('empty/<str:token>/pay', septic_views.emptying_pay, name='emptying-pay'),
    path('empty/<str:token>/paid', septic_views.emptying_paid, name='emptying-paid'),
    path('tfpass/<str:token>', tfpass_views.page, name='tfpass'),
    path('tfpass/<str:token>/pay', tfpass_views.pay, name='tfpass-pay'),
    path('tfpass/<str:token>/paid', tfpass_views.paid, name='tfpass-paid'),
    path('', include('channels.urls')),
]

if settings.SERVE_MEDIA:
    # MEDIA_URL is "media/"; Django adds the site's prefix to it on its own.
    urlpatterns.append(path('media/<path:path>', serve, {'document_root': settings.MEDIA_ROOT}))
