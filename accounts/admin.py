from django.contrib import admin
from .models import UserProfile, VerificationAttempt


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'phone_number',
        'language',
        'is_verified',
        'is_kenyan',
        'sms_notifications',
        'email_notifications',
    )
    list_filter = ('language', 'is_verified', 'is_kenyan', 'sms_notifications', 'email_notifications', 'use_alias')
    search_fields = ('user__username', 'phone_number')
    readonly_fields = ('masked_id_number',)

    fieldsets = (
        ('User Info', {
            'fields': ('user', 'phone_number')
        }),
        ('Verification & Identity', {
            'fields': ('is_verified', 'is_kenyan', 'id_number', 'masked_id_number')
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


@admin.register(VerificationAttempt)
class VerificationAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'amount_paid', 'payment_reference', 'attempted_at')
    list_filter = ('status', 'attempted_at')
    search_fields = ('user__username', 'payment_reference')