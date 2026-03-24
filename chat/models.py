from django.db import models
from django.contrib.auth.models import User
from bills.models import Bill
from .utils import generate_random_alias


class ChatMessageAlias(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_message_aliases')
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='chat_message_aliases')
    alias_name = models.CharField(max_length=100)

    class Meta:
        unique_together = ('user', 'bill')
        verbose_name = "Chat Message Alias"
        verbose_name_plural = "Chat Message Aliases"

    def __str__(self):
        return f"{self.alias_name} ({self.bill.short_id})"

class ChatMessage(models.Model):
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='messages')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    author_alias = models.CharField(max_length=100, default="Citizen")
    content = models.TextField()
    parent_message = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replies')
    upvotes = models.IntegerField(default=0)
    downvotes = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Chat Message"
        verbose_name_plural = "Chat Messages"

    def get_display_alias(self):
        if self.author_alias and self.author_alias != "Citizen":
            return self.author_alias
        alias_obj = ChatMessageAlias.objects.filter(user=self.user, bill=self.bill).first()
        if alias_obj and alias_obj.alias_name:
            return alias_obj.alias_name
        return "Citizen"

    def save(self, *args, **kwargs):
        # Ensure every message gets the per-bill ghost alias, even when
        # author_alias is prefilled by default ("Citizen").
        if self._state.adding or not self.author_alias or self.author_alias == "Citizen":
            alias_obj, _ = ChatMessageAlias.objects.get_or_create(
                user=self.user,
                bill=self.bill,
                defaults={'alias_name': generate_random_alias()}
            )
            if not alias_obj.alias_name:
                alias_obj.alias_name = generate_random_alias()
                alias_obj.save(update_fields=['alias_name'])
            self.author_alias = alias_obj.alias_name or "Citizen"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_display_alias()}: {self.content[:20]}"

class MessageReaction(models.Model):
    REACTION_CHOICES = [('up', 'Upvote'), ('down', 'Downvote')]
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    reaction_type = models.CharField(max_length=10, choices=REACTION_CHOICES)

    class Meta:
        unique_together = ('message', 'user')
        verbose_name = "Message Reaction"
        verbose_name_plural = "Message Reactions"

    def __str__(self):
        return f"{self.user.username} - {self.reaction_type} on {self.message.id}"