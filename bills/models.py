import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.models import User
from django.utils import timezone

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

class BillManager(models.Manager):
    def active_bills(self):
        today = timezone.now().date()
        return self.get_queryset().filter(status='AC').exclude(closing_date__lt=today)

class Bill(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'DR', _('Processing')
        REVIEW = 'RV', _('Pending Review')
        ACTIVE = 'AC', _('Active')
        CLOSED = 'CL', _('Closed')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    short_id = models.PositiveIntegerField(unique=True, db_index=True, blank=True)
    title = models.CharField(max_length=500, db_index=True)
    source_url = models.URLField(unique=True)
    document_hash = models.CharField(max_length=64, blank=True)
    ai_analysis = models.JSONField(default=dict, blank=True)
    is_processed_by_ai = models.BooleanField(default=False)
    closing_date = models.DateField(null=True, blank=True)
    notification_sent = models.BooleanField(default=False, db_index=True)
    status = models.CharField(max_length=2, choices=Status.choices, default=Status.DRAFT)
    view_count = models.PositiveIntegerField(default=0)
    support_count = models.PositiveIntegerField(default=0)
    oppose_count = models.PositiveIntegerField(default=0)
    pdf_report = models.FileField(upload_to='bill_reports/', null=True, blank=True)
    last_report_vote_count = models.PositiveIntegerField(default=0)
    report_generation_in_progress = models.BooleanField(default=False)

    objects = BillManager()

    class Meta:
        verbose_name = "Bill"
        verbose_name_plural = "Bills"

    def save(self, *args, **kwargs):
        if not self.short_id:
            last_bill = Bill.objects.all().order_by('short_id').last()
            self.short_id = (last_bill.short_id + 1) if last_bill else 100
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.short_id} - {self.title}"

    @property
    def current_status(self):
        if self.status == self.Status.ACTIVE and self.closing_date and self.closing_date < timezone.now().date():
            return self.Status.CLOSED
        return self.status

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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('bill', 'user')

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new:
            if self.vote_type == 'support':
                self.bill.support_count += 1
            else:
                self.bill.oppose_count += 1
            total_votes = self.bill.support_count + self.bill.oppose_count
            update_fields = ['support_count', 'oppose_count']

            if total_votes >= 50 and total_votes >= self.bill.last_report_vote_count + 50:
                self.bill.last_report_vote_count = total_votes
                update_fields.append('last_report_vote_count')
                self.bill.report_generation_in_progress = True
                update_fields.append('report_generation_in_progress')

            self.bill.save(update_fields=update_fields)

            if 'last_report_vote_count' in update_fields:
                from .tasks import generate_bill_report_pdf
                generate_bill_report_pdf.delay(str(self.bill.id))