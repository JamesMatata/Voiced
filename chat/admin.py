from django.contrib import admin
from .models import ChatMessage, MessageReaction, ChatMessageAlias

@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('get_alias', 'bill_short', 'content_truncated', 'upvotes', 'downvotes', 'created_at')
    list_filter = ('created_at', 'bill')
    search_fields = ('content', 'user__username', 'author_alias')
    readonly_fields = ('upvotes', 'downvotes', 'created_at')
    raw_id_fields = ('parent_message', 'user', 'bill')

    def get_alias(self, obj):
        return obj.author_alias
    get_alias.short_description = 'User Alias'

    def bill_short(self, obj):
        return f"ID: {obj.bill.short_id}"
    bill_short.short_description = 'Bill'

    def content_truncated(self, obj):
        return obj.content[:50] + "..." if len(obj.content) > 50 else obj.content
    content_truncated.short_description = 'Message'

    class Media:
        css = {
            'all': ('admin/css/custom_admin.css',)
        }

@admin.register(MessageReaction)
class MessageReactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'reaction_type', 'message_id_link')
    list_filter = ('reaction_type',)
    search_fields = ('user__username', 'message__content')
    raw_id_fields = ('message', 'user')

    def message_id_link(self, obj):
        return f"Msg ID: {obj.message.id}"
    message_id_link.short_description = 'Target Message'

    class Media:
        css = {
            'all': ('admin/css/custom_admin.css',)
        }


@admin.register(ChatMessageAlias)
class ChatMessageAliasAdmin(admin.ModelAdmin):
    list_display = ('alias_name', 'user', 'bill')
    search_fields = ('alias_name', 'user__username', 'bill__title')
    raw_id_fields = ('user', 'bill')