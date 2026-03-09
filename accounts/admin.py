from django.contrib import admin
from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'chat_alias', 'phone_number', 'language', 'sms_notifications', 'email_notifications')
    list_filter = ('language', 'sms_notifications', 'email_notifications', 'use_alias')
    search_fields = ('user__username', 'chat_alias', 'phone_number')
    readonly_fields = ('chat_alias',)

    fieldsets = (
        ('User Info', {
            'fields': ('user', 'chat_alias', 'phone_number')
        }),
        ('Preferences', {
            'fields': ('language', 'use_alias')
        }),
        ('Notifications', {
            'fields': ('sms_notifications', 'email_notifications'),
            'classes': ('collapse',),
        }),
    )

    class Media:
        css = {
            'all': ('admin/css/custom_admin.css',)
        }