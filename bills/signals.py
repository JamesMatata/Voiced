import random

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Bill
from notifications.utils import send_live_notification
from engagement.utils import send_at_sms


@receiver(post_save, sender=Bill)
def bill_processed_notification(sender, instance, created, **kwargs):
    if instance.is_processed_by_ai and instance.status == Bill.Status.ACTIVE:
        users = User.objects.filter(profile__isnull=False)

        for user in users:
            profile = user.profile
            user_lang = profile.language or 'en'

            if profile.email_notifications:
                send_live_notification(
                    user=user,
                    title="New Analysis Published",
                    message=f"We just finished analyzing: {instance.title[:50]}...",
                    link=f"/bills/{instance.id}/",
                    n_type='BILL'
                )

            if profile.sms_notifications and profile.phone_number:
                summary = instance.ai_analysis.get(user_lang) or instance.ai_analysis.get('en')

                if not summary:
                    hooks = [
                        f"The mtaa is talking about this! Be the first to read and vote.",
                        f"Big moves in Parliament. Check it out and give your take.",
                        f"Your voice matters on this. Dial in to see what's changing.",
                        f"New legislation alert. Join the debate now."
                    ]
                    summary = random.choice(hooks)

                prefix = {
                    'en': "VOICED ALERT",
                    'sw': "ILANI YA VOICED",
                    'sh': "RADA YA VOICED"
                }.get(user_lang, "VOICED")

                sms_text = (
                    f"{prefix}: {instance.title[:40]}..\n"
                    f"{summary[:110]}..\n"
                    f"Dial *483*XYZ# ID: {instance.short_id}"
                )

                send_at_sms(profile.phone_number, sms_text)