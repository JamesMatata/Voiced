from django.contrib import admin
from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone_number', 'language', 'sms_notifications', 'email_notifications')
    list_filter = ('language', 'sms_notifications', 'email_notifications', 'use_alias')
    search_fields = ('user__username', 'phone_number')

    fieldsets = (
        ('User Info', {
            'fields': ('user', 'phone_number')
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