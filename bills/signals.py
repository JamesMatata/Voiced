from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Bill
from notifications.utils import send_live_notification


@receiver(post_save, sender=Bill)
def bill_processed_notification(sender, instance, created, **kwargs):
    # We only notify when the AI has finished its analysis and the bill is Active
    if instance.is_processed_by_ai and instance.status == Bill.Status.ACTIVE:

        # We look into the 'profile' (UserProfile) and check 'email_notifications'
        interested_users = User.objects.filter(profile__email_notifications=True)

        for user in interested_users:
            send_live_notification(
                user=user,
                title="New Analysis Published",
                message=f"We just finished analyzing: {instance.title[:50]}...",
                link=f"/bills/{instance.id}/",
                n_type='BILL'
            )