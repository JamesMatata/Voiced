from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Bill
from notifications.utils import send_live_notification
from .tasks import generate_bill_audio_task, notify_subscribers_sms_for_new_bill


@receiver(post_save, sender=Bill)
def bill_processed_notification(sender, instance, created, **kwargs):
    if instance.is_processed_by_ai and instance.status == Bill.Status.PUBLISHED and not instance.notification_sent:

        Bill.objects.filter(id=instance.id).update(notification_sent=True)

        users = User.objects.filter(profile__isnull=False)

        for user in users:
            profile = user.profile
            if profile.email_notifications:
                send_live_notification(
                    user=user,
                    title="New Analysis Published",
                    message=f"We just finished analyzing: {instance.title[:50]}...",
                    link=f"/bills/{instance.id}/",
                    n_type='BILL'
                )

        notify_subscribers_sms_for_new_bill.delay(str(instance.id))
        if not (instance.audio_summary_en and instance.audio_summary_sw and instance.audio_summary_sh):
            generate_bill_audio_task.delay(str(instance.id))