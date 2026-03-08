from django.urls import path
from .views import BillChatView

urlpatterns = [
    path('bills/<uuid:pk>/chat/', BillChatView.as_view(), name='bill_chat'),
]