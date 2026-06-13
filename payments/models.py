from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from bills.models import Bill


class Transaction(models.Model):
    class TransactionType(models.TextChoices):
        TOPUP = "TP", _("Top-up")
        DEDUCTION = "DD", _("Deduction")
        REFUND = "RF", _("Refund")

    class Status(models.TextChoices):
        PENDING = "PE", _("Pending")
        COMPLETED = "CO", _("Completed")
        FAILED = "FL", _("Failed")

    class ServiceType(models.TextChoices):
        REPORT = "RP", _("Report / PDF")
        DRAFT = "DR", _("Legal draft")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_transactions"
    )
    wallet = models.ForeignKey(
        "accounts.Wallet", on_delete=models.CASCADE, related_name="transactions"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_type = models.CharField(max_length=2, choices=TransactionType.choices)
    status = models.CharField(max_length=2, choices=Status.choices, default=Status.PENDING)
    description = models.CharField(max_length=255, blank=True)
    mpesa_receipt_number = models.CharField(max_length=64, blank=True)
    mpesa_checkout_request_id = models.CharField(max_length=128, blank=True, db_index=True)
    mpesa_merchant_request_id = models.CharField(max_length=128, blank=True, db_index=True)
    bill = models.ForeignKey(Bill, on_delete=models.SET_NULL, null=True, blank=True, related_name="payment_transactions")
    service_type = models.CharField(max_length=2, choices=ServiceType.choices, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "status", "bill", "service_type"]),
        ]

    def __str__(self):
        return f"{self.get_transaction_type_display()} {self.amount} {self.get_status_display()}"


class Purchase(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="purchases"
    )
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="purchases")
    service_type = models.CharField(max_length=2, choices=Transaction.ServiceType.choices)
    transaction = models.OneToOneField(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_record",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "bill", "service_type"),
                name="unique_user_bill_service_purchase",
            )
        ]

    def __str__(self):
        return f"{self.user_id} {self.bill_id} {self.get_service_type_display()}"
