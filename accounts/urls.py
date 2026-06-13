from django.urls import path
from django.contrib.auth import views as auth_views
from django.views.generic import TemplateView
from . import views

urlpatterns = [
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='home'), name='logout'),
    path('activate/<uidb64>/<token>/', views.activate, name='activate'),
    path('verify-email-otp/', views.verify_email_otp, name='verify_email_otp'),
    path('profile/', views.profile_view, name='profile'),
    path('email-change/state/', views.email_change_state, name='email_change_state'),
    path('email-change/request-code/', views.email_change_request_code, name='email_change_request_code'),
    path('email-change/verify-code/', views.email_change_verify_code, name='email_change_verify_code'),
    path('password-change/state/', views.password_change_state, name='password_change_state'),
    path('password-change/request-code/', views.password_change_request_code, name='password_change_request_code'),
    path('password-change/verify-code/', views.password_change_verify_code, name='password_change_verify_code'),
    path('password-change/submit/', views.password_change_submit, name='password_change_submit'),
    path('verification/state/', views.verification_state, name='verification_state'),
    path('verification/submit/', views.submit_kyc_modal, name='verification_submit'),
    path('password-reset/', views.password_reset_request_otp, name='password_reset'),
    path('password-reset/verify/', views.password_reset_verify_otp, name='password_reset_done'),
    path('password-reset/new/', views.password_reset_set_new, name='password_reset_confirm'),
    path('password-reset/complete/', TemplateView.as_view(template_name='accounts/password_reset_complete.html'), name='password_reset_complete'),
]