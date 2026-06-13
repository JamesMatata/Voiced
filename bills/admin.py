from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from .models import Bill, BillOutcome, MissingTranslation, ScrapeLog, BillVote, SMSLog
from .tasks import generate_bill_audio_task, notify_bill_outcome_participants_task, run_all_scrapers_sync


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    change_list_template = "admin/bills/bill/change_list.html"
    list_display = (
        'short_id', 'title_short', 'government_level', 'county',
        'status_pill', 'is_processed_by_ai',
        'is_sw_ready', 'is_sh_ready', 'translation_needed',
        'support_count', 'oppose_count'
    )
    list_filter = (
        'status',
        'government_level',
        'is_processed_by_ai',
        'is_sw_ready',
        'is_sh_ready',
    )
    search_fields = ('title', 'short_id')
    readonly_fields = ('short_id', 'view_count', 'support_count', 'oppose_count', 'document_hash')
    actions = ['approve_and_publish']

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'sync-bills-now/',
                self.admin_site.admin_view(self.sync_bills_now),
                name='bills_bill_sync_now'
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        last_run = ScrapeLog.objects.order_by('-created_at').first()
        status_msg = "System Status: Waiting for first scrape..."

        if last_run:
            next_run = last_run.created_at + timezone.timedelta(days=3)
            remaining = next_run - timezone.now()
            status_msg = f"System Status: Next automated scrape in {remaining.days}d {remaining.seconds // 3600}h"

        # This adds the message to the top of the admin page
        self.message_user(request, status_msg, level='info')
        return super().changelist_view(request, extra_context=extra_context)

    def sync_bills_now(self, request):
        result = run_all_scrapers_sync()
        self.message_user(request, f"Manual sync complete. {result}")
        return HttpResponseRedirect(reverse('admin:bills_bill_changelist'))

    def status_pill(self, obj):
        colors = {'DR': '#6b7280', 'RV': '#f59e0b', 'AC': '#10b981', 'CL': '#ef4444'}
        return format_html('<span style="color: {}; font-weight: bold;">{}</span>', colors.get(obj.status, 'black'),
                           obj.get_status_display())

    status_pill.short_description = 'Status'

    def title_short(self, obj):
        return obj.title[:50] + "..." if len(obj.title) > 50 else obj.title

    def translation_needed(self, obj):
        return obj.missing_translations.filter(resolved=False).exists()
    translation_needed.boolean = True
    translation_needed.short_description = "Requires Translation"

    @admin.action(description="Approve and Publish selected bills")
    def approve_and_publish(self, request, queryset):
        updated = queryset.update(status=Bill.Status.PUBLISHED)
        for bill in queryset:
            generate_bill_audio_task.delay(str(bill.id))
        self.message_user(request, f"{updated} bill(s) published.")


@admin.register(ScrapeLog)
class ScrapeLogAdmin(admin.ModelAdmin):
    list_display = ('source_name', 'bills_found', 'bills_added', 'was_successful', 'next_run_countdown')
    list_filter = ('was_successful', 'source_name')

    def next_run_countdown(self, obj):
        next_date = obj.created_at + timezone.timedelta(days=3)
        remaining = next_date - timezone.now()
        if remaining.days < 0:
            return "Pending..."
        return f"{remaining.days}d {remaining.seconds // 3600}h"

    next_run_countdown.short_description = "Next Scrape In"


@admin.register(BillVote)
class BillVoteAdmin(admin.ModelAdmin):
    list_display = ('bill', 'user', 'vote_type', 'created_at')


@admin.register(SMSLog)
class SMSLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'phone', 'status', 'purpose', 'bill', 'user')
    list_filter = ('status', 'purpose')
    search_fields = ('phone', 'message', 'error_message')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(BillOutcome)
class BillOutcomeAdmin(admin.ModelAdmin):
    list_display = ("bill", "final_status", "created_at")
    list_filter = ("final_status", "created_at")
    search_fields = ("bill__title", "summary_text")
    readonly_fields = ("created_at", "updated_at")
    actions = ["resend_outcome_notifications"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        notify_bill_outcome_participants_task.delay(obj.id)
        self.message_user(request, "Outcome saved. Participant notifications queued.")

    @admin.action(description="Resend outcome notifications (rate-limited)")
    def resend_outcome_notifications(self, request, queryset):
        count = 0
        for outcome in queryset:
            notify_bill_outcome_participants_task.delay(outcome.id)
            count += 1
        self.message_user(
            request,
            f"Queued resend for {count} outcome(s). Existing per-user rate limits still apply.",
        )


@admin.register(MissingTranslation)
class MissingTranslationAdmin(admin.ModelAdmin):
    list_display = ("bill", "language", "resolved", "created_at")
    list_filter = ("language", "resolved")
    search_fields = ("bill__title", "note")