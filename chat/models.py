from django.db import models
from django.contrib.auth.models import User
from bills.models import Bill

class ChatMessage(models.Model):
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='messages')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    parent_message = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replies')
    upvotes = models.IntegerField(default=0)
    downvotes = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Chat Message"
        verbose_name_plural = "Chat Messages"

    def __str__(self):
        alias = self.user.profile.chat_alias if hasattr(self.user, 'profile') else self.user.username
        return f"{alias}: {self.content[:20]}"

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