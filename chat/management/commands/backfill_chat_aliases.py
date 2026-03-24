from django.core.management.base import BaseCommand
from django.db import transaction

from chat.models import ChatMessage, ChatMessageAlias
from chat.utils import generate_random_alias


class Command(BaseCommand):
    help = "Backfill per-bill aliases and ChatMessage.author_alias values."

    def handle(self, *args, **options):
        updated = 0
        created_aliases = 0

        queryset = ChatMessage.objects.select_related('user', 'bill').order_by('id')
        for message in queryset:
            with transaction.atomic():
                default_alias = "Citizen"
                if hasattr(message.user, 'profile'):
                    # Transitional compatibility: use old profile alias if present.
                    default_alias = getattr(message.user.profile, 'chat_alias', None) or default_alias

                alias_obj, created = ChatMessageAlias.objects.get_or_create(
                    user=message.user,
                    bill=message.bill,
                    defaults={'alias_name': default_alias if default_alias != "Citizen" else generate_random_alias()}
                )
                if created:
                    created_aliases += 1

                if not message.author_alias or message.author_alias == "Citizen":
                    message.author_alias = alias_obj.alias_name or "Citizen"
                    message.save(update_fields=['author_alias'])
                    updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Backfill complete. Aliases created: {created_aliases}, messages updated: {updated}"
        ))
