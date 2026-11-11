from django.urls import path

from . import septic_views, studio_views, views

urlpatterns = [
    path('', views.dashboard, name='ops-dashboard'),
    path('data', views.data, name='ops-data'),
    path('studio/', studio_views.studio, name='ops-studio'),
    path('payout', views.payout, name='ops-payout'),
    path('sensor-sandbox', septic_views.sensor_sandbox, name='ops-sensor-sandbox'),
    path('septic.json', septic_views.septic_data, name='ops-septic'),
    path('report.json', septic_views.report_data, name='ops-report'),
    path('statement/<str:code>', septic_views.statement, name='ops-statement'),
    path('refund', views.refund, name='ops-refund'),
    path('visit', views.visit, name='ops-visit'),
    path('cleaning', views.cleaning, name='ops-cleaning'),
    path('restroom', views.edit_restroom, name='ops-restroom'),
    path('cleaner', views.save_cleaner, name='ops-cleaner'),
    path('attendant', views.save_attendant, name='ops-attendant'),
    path('photo/<int:check_id>', views.photo, name='ops-photo'),
]
