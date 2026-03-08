from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.core.mail import send_mail
from django.conf import settings
from .models import Notification


def send_live_notification(user, title, message, link, n_type='BILL'):
    # Check if the user wants notifications based on your Profile model field
    if not hasattr(user, 'profile') or not user.profile.email_notifications:
        return

    # 1. Save to Database
    Notification.objects.create(
        user=user,
        title=title,
        message=message,
        link=link,
        notification_type=n_type
    )

    # 2. Push to WebSocket for the "Live" effect
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"notify_user_{user.id}",
        {
            "type": "send_notification",
            "data": {
                "title": title,
                "message": message,
                "link": link,
                "type": n_type,
            }
        }
    )

    # 3. Send the Email
    site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')
    full_link = f"{site_url}{link}"

    send_mail(
        subject=f"VOICED: {title}",
        message=f"{message}\n\nRead more here: {full_link}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True
    )