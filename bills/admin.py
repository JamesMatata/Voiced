from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from .models import Bill, ScrapeLog, BillVote
from .tasks import run_all_scrapers_sync


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    change_list_template = "admin/bills/bill/change_list.html"
    list_display = ('short_id', 'title_short', 'status_pill', 'is_processed_by_ai', 'support_count', 'oppose_count')
    list_filter = ('status', 'is_processed_by_ai')
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

    @admin.action(description="Approve and Publish selected bills")
    def approve_and_publish(self, request, queryset):
        updated = queryset.update(status=Bill.Status.PUBLISHED)
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