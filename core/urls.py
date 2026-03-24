from django.urls import path
from .views import HomeView, BillListView, BillDetailView, DiscussionListView, AboutView, PrivacyView, TermsView, generate_bill_pdf, generate_write_up, bill_vote_counts, download_report, national_pulse_status

urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('bills/', BillListView.as_view(), name='bill_list'),
    path('bills/<uuid:pk>/', BillDetailView.as_view(), name='bill_detail'),
    path('bills/<uuid:pk>/vote-counts/', bill_vote_counts, name='bill_vote_counts'),
    path('bills/<uuid:bill_id>/download-report/', download_report, name='download_report'),
    path('bills/<uuid:bill_id>/pulse-status/', national_pulse_status, name='national_pulse_status'),
    path('discussions/', DiscussionListView.as_view(), name='discussion_list'),
    path('about/', AboutView.as_view(), name='about'),
    path('privacy/', PrivacyView.as_view(), name='privacy'),
    path('terms/', TermsView.as_view(), name='terms'),
    path('bill/<uuid:bill_id>/report/', generate_bill_pdf, name='bill_report_pdf'),
    path('bill/<uuid:bill_id>/write-up/', generate_write_up, name='generate_write_up'),
]