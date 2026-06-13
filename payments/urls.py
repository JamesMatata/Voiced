from django.urls import path

from . import views

urlpatterns = [
    path("prepare/", views.prepare_purchase, name="prepare_purchase"),
    path("verification/initiate/", views.initiate_verification_payment, name="initiate_verification_payment"),
    path("htmx/bill/prepare-wallet/", views.htmx_bill_prepare_wallet, name="htmx_bill_prepare_wallet"),
    path("htmx/bill/mpesa-stk/", views.htmx_bill_mpesa_stk, name="htmx_bill_mpesa_stk"),
    path("wallet-balance/", views.wallet_balance_json, name="wallet_balance"),
    path("mpesa/topup-status/", views.mpesa_topup_status, name="mpesa_topup_status"),
    path("mpesa/stk/", views.initiate_mpesa_stk, name="mpesa_stk"),
    path("mpesa/callback/", views.mpesa_stk_callback, name="mpesa_callback"),
    path("download/national-pulse/<uuid:bill_id>/", views.download_national_pulse, name="download_national_pulse"),
    path("download/legislative/<uuid:bill_id>/", views.download_legislative_report, name="download_legislative"),
    path("draft/<uuid:bill_id>/", views.generate_draft_paid, name="generate_draft_paid"),
]
