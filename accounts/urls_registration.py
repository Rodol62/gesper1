from django.urls import path
from .views_registration import register

urlpatterns = [
    path('register/', register, name='register'),
]
