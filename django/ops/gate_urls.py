from django.urls import path

from . import gate_views

urlpatterns = [
    path('', gate_views.gate, name='gate'),
    path('check', gate_views.gate_check, name='gate-check'),
    path('login/', gate_views.GateLogin.as_view(), name='gate-login'),
    path('logout/', gate_views.gate_logout, name='gate-logout'),
]
