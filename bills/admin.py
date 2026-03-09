from django.contrib import admin
from .models import Bill, ScrapeLog, BillVote


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ('short_id', 'title_short', 'status', 'closing_date', 'is_processed_by_ai', 'support_count',
                    'oppose_count')
    list_filter = ('status', 'is_processed_by_ai', 'notification_sent', 'created_at')
    search_fields = ('title', 'short_id')
    readonly_fields = ('short_id', 'view_count', 'support_count', 'oppose_count', 'document_hash')

    fieldsets = (
        ('Basic Information', {
            'fields': ('short_id', 'title', 'source_url', 'status', 'closing_date')
        }),
        ('AI Analysis Details', {
            'fields': ('is_processed_by_ai', 'ai_analysis', 'notification_sent', 'document_hash'),
            'classes': ('collapse',),
        }),
        ('Engagement Stats', {
            'fields': ('view_count', 'support_count', 'oppose_count'),
        }),
    )

    def title_short(self, obj):
        return obj.title[:50] + "..." if len(obj.title) > 50 else obj.title

    title_short.short_description = 'Title'

    class Media:
        css = {
            'all': ('admin/css/custom_admin.css',)
        }


@admin.register(ScrapeLog)
class ScrapeLogAdmin(admin.ModelAdmin):
    list_display = ('source_name', 'bills_found', 'bills_added', 'was_successful', 'created_at')
    list_filter = ('was_successful', 'source_name')
    readonly_fields = ('created_at', 'updated_at')

    class Media:
        css = {
            'all': ('admin/css/custom_admin.css',)
        }


@admin.register(BillVote)
class BillVoteAdmin(admin.ModelAdmin):
    list_display = ('bill', 'user', 'vote_type', 'created_at')
    list_filter = ('vote_type', 'created_at')
    search_fields = ('bill__title', 'user__username', 'reason')
    readonly_fields = ('created_at',)

    class Media:
        css = {
            'all': ('admin/css/custom_admin.css',)
        }