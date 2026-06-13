from django.urls import path
from . import views

urlpatterns = [
    path('ussd/', views.ussd_callback, name='ussd_callback'),
    path('sms-callback/', views.sms_callback, name='sms_callback'),
    path('voice/callback/', views.voice_callback, name='voice_callback'),
    path('voice/vote/', views.voice_vote_callback, name='voice_vote_callback'),
]