from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from coolname import generate_slug


class UserProfile(models.Model):
    LANGUAGE_CHOICES = [
        ('en', 'English'),
        ('sw', 'Kiswahili'),
        ('sh', 'Sheng'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    chat_alias = models.CharField(max_length=100, unique=True, blank=True)

    phone_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    sms_notifications = models.BooleanField(default=False)

    use_alias = models.BooleanField(default=True, help_text="Use alias instead of real username in chats.")
    language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES, default='en')
    email_notifications = models.BooleanField(default=False, help_text="Receive alerts for new bills.")

    def save(self, *args, **kwargs):
        if not self.chat_alias:
            raw_slug = generate_slug(2)
            self.chat_alias = raw_slug.replace('-', ' ').title()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} ({self.chat_alias})"


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)