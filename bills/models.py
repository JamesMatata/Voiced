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
        return self.get_queryset().filter(status=Bill.Status.ACTIVE).exclude(closing_date__lt=today)

class Bill(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'DR', _('Draft / Processing')
        ACTIVE = 'AC', _('Active')
        CLOSED = 'CL', _('Closed')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=500, db_index=True)
    source_url = models.URLField(unique=True)
    document_hash = models.CharField(max_length=64, blank=True)

    ai_analysis = models.JSONField(default=dict, blank=True)
    is_processed_by_ai = models.BooleanField(default=False)
    closing_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=2, choices=Status.choices, default=Status.DRAFT)
    view_count = models.PositiveIntegerField(default=0)
    support_count = models.PositiveIntegerField(default=0)
    oppose_count = models.PositiveIntegerField(default=0)

    objects = BillManager()

    def __str__(self):
        return self.title

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

    def __str__(self):
        return f"{self.source_name} - {self.created_at.strftime('%Y-%m-%d')}"

class BillVote(models.Model):
    VOTE_CHOICES = [
        ('support', 'Support'),
        ('oppose', 'Oppose'),
    ]

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
            self.bill.save(update_fields=['support_count', 'oppose_count'])