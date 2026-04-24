from django.urls import path
from . import views

urlpatterns = [
    path('hr/', views.report_hr, name='report_hr'),
]