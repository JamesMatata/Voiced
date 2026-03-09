from django.db import models
from django.contrib.auth.models import User

class Notification(models.Model):
    class Type(models.TextChoices):
        BILL_UPDATE = 'BILL', 'New Legislation'
        CHAT_MENTION = 'CHAT', 'Chat Mention'
        SYSTEM = 'SYS', 'System Update'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=10, choices=Type.choices, default=Type.BILL_UPDATE)
    title = models.CharField(max_length=255)
    message = models.TextField()
    link = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"

    def __str__(self):
        return f"{self.notification_type} for {self.user.username}: {self.title[:20]}"