from django.db import models
from django.contrib.auth.models import User
from bills.models import Bill  # Import the Bill model from your data app


class ChatMessage(models.Model):
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='messages')

    # Security: Hard link to the verified user to prevent spam/bots
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    content = models.TextField()

    # Self-referential foreign key for the "Reply" feature
    parent_message = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replies')

    upvotes = models.IntegerField(default=0)
    downvotes = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        # Gracefully handle the string representation using the user's profile
        alias = self.user.profile.chat_alias if hasattr(self.user, 'profile') else "Unknown"
        return f"{alias}: {self.content[:20]}"

class MessageReaction(models.Model):
    REACTION_CHOICES = [('up', 'Upvote'), ('down', 'Downvote')]
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    reaction_type = models.CharField(max_length=10, choices=REACTION_CHOICES)

    class Meta:
        unique_together = ('message', 'user')