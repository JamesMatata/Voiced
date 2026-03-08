from django.contrib import admin
from django.contrib import messages
from .models import Bill, ScrapeLog
from .tasks import process_bill_with_ai, run_all_scrapers


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'is_processed_by_ai', 'support_count', 'oppose_count', 'created_at')
    list_filter = ('status', 'is_processed_by_ai', 'created_at')
    search_fields = ('title',)
    readonly_fields = ('id', 'created_at', 'updated_at', 'document_hash')

    # Custom Admin Action to trigger Gemini from the dashboard
    actions = ['trigger_ai_analysis']

    @admin.action(description='Generate AI Analysis for selected bills')
    def trigger_ai_analysis(self, request, queryset):
        count = 0
        for bill in queryset:
            if not bill.is_processed_by_ai:
                process_bill_with_ai.delay(bill.id)
                count += 1

        self.message_user(
            request,
            f"Successfully queued {count} bill(s) for AI processing.",
            messages.SUCCESS
        )

    fieldsets = (
        ('Source Information', {
            'fields': ('id', 'title', 'source_url', 'document_hash')
        }),
        ('AI Summaries (JSON)', {
            'fields': ('ai_analysis', 'is_processed_by_ai'),
            'classes': ('wide',)
        }),
        ('Status & Metrics', {
            'fields': ('status', 'support_count', 'oppose_count')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(ScrapeLog)
class ScrapeLogAdmin(admin.ModelAdmin):
    list_display = ('source_name', 'was_successful', 'bills_found', 'bills_added', 'created_at')
    list_filter = ('was_successful', 'source_name')
    readonly_fields = ('created_at', 'updated_at', 'source_name', 'bills_found', 'bills_added', 'was_successful',
                       'error_message')

    # Custom action to trigger the scraper manually
    actions = ['trigger_manual_scrape']

    @admin.action(description='Run Scraper Pipeline Now')
    def trigger_manual_scrape(self, request, queryset):
        run_all_scrapers.delay()
        self.message_user(
            request,
            "The web scraper has been queued and is running in the background.",
            messages.SUCCESS
        )