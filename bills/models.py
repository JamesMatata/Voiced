import uuid
import hashlib
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.models import User
from django.utils import timezone
from django.conf import settings

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

class BillManager(models.Manager):
    def active_bills(self):
        today = timezone.now().date()
        return self.get_queryset().filter(
            status=Bill.Status.PUBLISHED
        ).filter(
            models.Q(closing_date__isnull=True) | models.Q(closing_date__gte=today)
        )

    def recently_closed_bills(self):
        today = timezone.now().date()
        threshold = today - timezone.timedelta(days=30)
        return self.get_queryset().filter(
            status=Bill.Status.PUBLISHED,
            closing_date__lt=today,
            closing_date__gte=threshold
        )

class Bill(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'DR', _('Processing')
        PENDING_REVIEW = 'RV', _('Pending Review')
        PUBLISHED = 'AC', _('Published')
        CLOSED = 'CL', _('Closed')

    class GovernmentLevel(models.TextChoices):
        NATIONAL = 'NA', _('National')
        COUNTY = 'CO', _('County')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    short_id = models.PositiveIntegerField(unique=True, db_index=True, blank=True)
    title = models.CharField(max_length=500, db_index=True)
    title_en = models.CharField(max_length=500, blank=True)
    title_sw = models.CharField(max_length=500, blank=True)
    title_sh = models.CharField(max_length=500, blank=True)
    summary_en = models.TextField(blank=True)
    summary_sw = models.TextField(blank=True)
    summary_sh = models.TextField(blank=True)
    ai_analysis_summary_en = models.TextField(blank=True)
    ai_analysis_summary_sw = models.TextField(blank=True)
    ai_analysis_summary_sh = models.TextField(blank=True)
    is_sw_ready = models.BooleanField(default=False, db_index=True)
    is_sh_ready = models.BooleanField(default=False, db_index=True)
    source_url = models.URLField(unique=True)
    government_level = models.CharField(
        max_length=2,
        choices=GovernmentLevel.choices,
        default=GovernmentLevel.NATIONAL,
        db_index=True,
    )
    county = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Required when level is County; official county name when known."),
    )
    document_hash = models.CharField(max_length=64, blank=True)
    ai_analysis = models.JSONField(default=dict, blank=True)
    is_processed_by_ai = models.BooleanField(default=False)
    closing_date = models.DateField(null=True, blank=True)
    notification_sent = models.BooleanField(default=False, db_index=True)
    status = models.CharField(max_length=2, choices=Status.choices, default=Status.DRAFT)
    view_count = models.PositiveIntegerField(default=0)
    total_votes = models.PositiveIntegerField(default=0)
    verified_citizen_votes = models.PositiveIntegerField(default=0)
    support_count = models.PositiveIntegerField(default=0)
    oppose_count = models.PositiveIntegerField(default=0)
    verified_support_count = models.PositiveIntegerField(default=0)
    verified_oppose_count = models.PositiveIntegerField(default=0)
    pdf_report = models.FileField(upload_to='bill_reports/', null=True, blank=True)
    last_report_vote_count = models.PositiveIntegerField(default=0)
    report_generation_in_progress = models.BooleanField(default=False)
    sms_summary = models.CharField(
        max_length=160,
        blank=True,
        help_text="Ultra-concise SMS copy (≤160 chars); includes bill code for USSD/alerts.",
    )
    audio_summary_en = models.FileField(upload_to="bill_audio/", null=True, blank=True)
    audio_summary_sw = models.FileField(upload_to="bill_audio/", null=True, blank=True)
    audio_summary_sh = models.FileField(upload_to="bill_audio/", null=True, blank=True)

    objects = BillManager()

    class Meta:
        verbose_name = "Bill"
        verbose_name_plural = "Bills"

    def clean(self):
        super().clean()
        if self.government_level == self.GovernmentLevel.COUNTY and not (self.county or "").strip():
            raise ValidationError(
                {"county": _("County name is required for county-level bills.")}
            )

    def save(self, *args, **kwargs):
        if not self.short_id:
            last_bill = Bill.objects.all().order_by('short_id').last()
            self.short_id = (last_bill.short_id + 1) if last_bill else 100
        if self.government_level == self.GovernmentLevel.NATIONAL:
            self.county = ""
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.short_id} - {self.title}"

    @property
    def list_card_summary(self) -> str:
        """Best-effort teaser for list cards (AI JSON, then flat summary fields)."""
        ai = self.ai_analysis if isinstance(self.ai_analysis, dict) else {}
        eng = ai.get("english") or {}
        if isinstance(eng, dict):
            s = (eng.get("short_summary") or "").strip()
            if s:
                return s
        for field in (
            self.ai_analysis_summary_en,
            self.summary_en,
            self.sms_summary,
        ):
            if field and str(field).strip():
                return str(field).strip()
        return ""

    @property
    def current_status(self):
        if self.status == self.Status.PUBLISHED and self.closing_date and timezone.now().date() > self.closing_date:
            return self.Status.CLOSED
        return self.status

    @property
    def is_closed(self):
        return self.current_status == self.Status.CLOSED

    @property
    def is_archived(self):
        if not self.closing_date:
            return False
        return timezone.now().date() > (self.closing_date + timezone.timedelta(days=30))


class BillOutcome(TimeStampedModel):
    class FinalStatus(models.TextChoices):
        PASSED = "PASSED", _("Passed")
        REJECTED = "REJECTED", _("Rejected")
        AMENDED = "AMENDED", _("Amended")
        WITHDRAWN = "WITHDRAWN", _("Withdrawn")
        OTHER = "OTHER", _("Other")

    bill = models.OneToOneField(Bill, on_delete=models.CASCADE, related_name="outcome")
    final_status = models.CharField(max_length=16, choices=FinalStatus.choices)
    summary_text = models.TextField()

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Bill outcome"
        verbose_name_plural = "Bill outcomes"

    def __str__(self):
        return f"{self.bill.short_id} outcome: {self.get_final_status_display()}"


class MissingTranslation(TimeStampedModel):
    class Language(models.TextChoices):
        SW = "sw", _("Kiswahili")
        SH = "sh", _("Sheng")

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="missing_translations")
    language = models.CharField(max_length=2, choices=Language.choices)
    resolved = models.BooleanField(default=False, db_index=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("bill", "language", "resolved")
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.bill.short_id} missing {self.language}"


class ScrapeLog(TimeStampedModel):
    source_name = models.CharField(max_length=100)
    bills_found = models.IntegerField(default=0)
    bills_added = models.IntegerField(default=0)
    was_successful = models.BooleanField(default=True)
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Scrape Log"
        verbose_name_plural = "Scrape Logs"

    def __str__(self):
        return f"{self.source_name} - {self.created_at.strftime('%Y-%m-%d')}"

class BillVote(models.Model):
    VOTE_CHOICES = [('support', 'Support'), ('oppose', 'Oppose')]
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='votes')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    vote_type = models.CharField(max_length=10, choices=VOTE_CHOICES)
    reason = models.TextField(blank=True, null=True)
    receipt_id = models.CharField(max_length=64, unique=True, db_index=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('bill', 'user')

    def save(self, *args, **kwargs):
        if not self.receipt_id and self.user_id and self.bill_id:
            secret_salt = getattr(settings, "VOTE_RECEIPT_SALT", "") or settings.SECRET_KEY
            payload = f"{self.user_id}:{self.bill_id}:{secret_salt}"
            self.receipt_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        # Bill aggregate counters are updated atomically in voting view logic.
        super().save(*args, **kwargs)


class SMSLog(TimeStampedModel):
    """Tracks outbound SMS for cost monitoring and delivery debugging."""

    class Status(models.TextChoices):
        SENT = "SN", _("Sent")
        FAILED = "FL", _("Failed")
        SKIPPED = "SK", _("Skipped")

    class Purpose(models.TextChoices):
        USSD_VIEW = "ussd", _("USSD view summary")
        BILL_NOTIFICATION = "bill", _("New bill notification")
        BILL_OUTCOME = "outcome", _("Bill outcome update")

    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="sms_logs"
    )
    bill = models.ForeignKey(
        Bill, on_delete=models.CASCADE, null=True, blank=True, related_name="sms_logs"
    )
    phone = models.CharField(max_length=32)
    message = models.CharField(max_length=200)
    status = models.CharField(max_length=2, choices=Status.choices, db_index=True)
    purpose = models.CharField(max_length=8, choices=Purpose.choices, db_index=True)
    error_message = models.TextField(blank=True)
    provider_response = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "SMS log"
        verbose_name_plural = "SMS logs"

    def __str__(self):
        return f"{self.get_status_display()} {self.phone} ({self.get_purpose_display()})"