from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .fields import EncryptedCharField

class UserProfile(models.Model):
    LANGUAGE_CHOICES = [
        ('en', 'English'),
        ('sw', 'Kiswahili'),
        ('sr', 'Sheng'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    sms_notifications = models.BooleanField(default=False)
    use_alias = models.BooleanField(default=True)
    language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES, default='en')
    email_notifications = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    is_kenyan = models.BooleanField(default=False)
    id_number = EncryptedCharField(max_length=512, blank=True, null=True)

    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    @property
    def subscribed_to_sms(self):
        """Opt-in for automated bill SMS alerts (USSD handoff ignores this)."""
        return self.sms_notifications

    def __str__(self):
        return f"{self.user.username}"

    @property
    def masked_id_number(self):
        if not self.id_number:
            return ""
        cleaned = str(self.id_number).strip()
        if len(cleaned) <= 4:
            return "*" * len(cleaned)
        return f"{'*' * (len(cleaned) - 4)}{cleaned[-4:]}"

    @property
    def can_access_authenticated_features(self):
        """Tier 1: verified account can access AI drafts, chat, and paid tools."""
        return self.is_verified

    @property
    def can_vote_officially(self):
        """Tier 2: Kenyan identity-verified account can participate in official voting."""
        return self.is_verified and self.is_kenyan


class VerificationAttempt(models.Model):
    class PaymentStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PAID = "PAID", "Paid"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PAID = "PAID", "Paid"
        VERIFIED = "VERIFIED", "Verified"
        FAILED = "FAILED", "Failed"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="verification_attempts")
    payment_status = models.CharField(max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    kyc_attempts = models.PositiveIntegerField(default=0)
    id_number = EncryptedCharField(max_length=512, blank=True, null=True)
    full_name = models.CharField(max_length=120, blank=True)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    payment_reference = models.CharField(max_length=120, blank=True)
    mpesa_checkout_request_id = models.CharField(max_length=128, blank=True, db_index=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    attempted_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-attempted_at",)
        verbose_name = "Verification Attempt"
        verbose_name_plural = "Verification Attempts"

    def __str__(self):
        return f"{self.user.username} - {self.status}"


class EmailChangeAttempt(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VERIFIED = "VERIFIED", "Verified"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_change_attempts")
    new_email = models.EmailField()
    code_hash = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.user.username} -> {self.new_email} ({self.status})"


class AccountOtp(models.Model):
    class Purpose(models.TextChoices):
        EMAIL_ACTIVATION = "EMAIL_ACTIVATION", "Email activation"
        PASSWORD_RESET = "PASSWORD_RESET", "Password reset"
        PASSWORD_CHANGE = "PASSWORD_CHANGE", "Password change"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VERIFIED = "VERIFIED", "Verified"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="account_otps")
    purpose = models.CharField(max_length=24, choices=Purpose.choices)
    email = models.EmailField()
    code_hash = models.CharField(max_length=255)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.user.username} {self.purpose} ({self.status})"


class Wallet(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    reserved_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Wallet"

    def __str__(self):
        return f"{self.user.username} — KES {self.balance}"

    @property
    def available_balance(self):
        return self.balance - self.reserved_balance


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)
        Wallet.objects.get_or_create(user=instance)
