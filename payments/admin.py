from django.contrib import admin

from .models import Purchase, Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "amount", "transaction_type", "status", "bill", "service_type", "created_at")
    list_filter = ("transaction_type", "status", "service_type")
    search_fields = ("user__username", "mpesa_receipt_number", "mpesa_checkout_request_id")
    raw_id_fields = ("user", "wallet", "bill")


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "bill", "service_type", "created_at")
    list_filter = ("service_type",)
    raw_id_fields = ("user", "bill", "transaction")
