import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from bills.services.sms_gateway import send_sms_via_africastalking
from notifications.models import Notification

logger = logging.getLogger(__name__)


def _notify_in_app(user, title: str, message: str, link: str):
    Notification.objects.create(
        user=user,
        title=title,
        message=message,
        link=link,
        notification_type=Notification.Type.SYSTEM,
    )
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"notify_user_{user.id}",
            {
                "type": "send_notification",
                "data": {
                    "title": title,
                    "message": message,
                    "link": link,
                    "type": Notification.Type.SYSTEM,
                },
            },
        )
    except Exception:
        logger.exception("Live notification push failed for user=%s", user.id)


def _notify_email(user, title: str, message: str, link: str):
    if not getattr(user, "email", ""):
        return
    site_url = getattr(settings, "BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    full_link = f"{site_url}{link}"
    send_mail(
        subject=f"VOICED: {title}",
        message=f"{message}\n\nOpen wallet: {full_link}",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@voiced.co.ke"),
        recipient_list=[user.email],
        fail_silently=True,
    )


def _notify_sms(user, message: str):
    profile = getattr(user, "profile", None)
    phone = (getattr(profile, "phone_number", "") or "").strip()
    if not phone:
        return
    send_sms_via_africastalking(phone, message[:160])


def notify_refund_unused_reservation(*, txn):
    if not txn or not txn.user_id:
        return
    link = reverse("wallet")
    service = "premium service"
    if txn.service_type == "DR":
        service = "AI draft"
    elif txn.service_type == "RP":
        service = "analysis report"
    title = "Wallet refund processed"
    message = (
        f"KES {txn.amount} reserved for {service} was returned to your wallet "
        f"because the payment was not completed in time."
    )
    try:
        _notify_in_app(txn.user, title, message, link)
    except Exception:
        logger.exception("Failed in-app refund notification for txn=%s", txn.id)
    try:
        _notify_email(txn.user, title, message, link)
    except Exception:
        logger.exception("Failed email refund notification for txn=%s", txn.id)
    try:
        _notify_sms(txn.user, f"Voiced: KES {txn.amount} refunded to wallet. Open app wallet.")
    except Exception:
        logger.exception("Failed SMS refund notification for txn=%s", txn.id)


def notify_stale_topup_expired(*, txn):
    if not txn or not txn.user_id:
        return
    link = reverse("wallet")
    title = "M-Pesa request expired"
    message = (
        f"Your pending M-Pesa top-up of KES {txn.amount} expired before confirmation. "
        "No money was added. You can retry safely."
    )
    try:
        _notify_in_app(txn.user, title, message, link)
    except Exception:
        logger.exception("Failed in-app topup expiry notification for txn=%s", txn.id)
    try:
        _notify_email(txn.user, title, message, link)
    except Exception:
        logger.exception("Failed email topup expiry notification for txn=%s", txn.id)
    try:
        _notify_sms(txn.user, f"Voiced: M-Pesa top-up KES {txn.amount} expired. Retry if needed.")
    except Exception:
        logger.exception("Failed SMS topup expiry notification for txn=%s", txn.id)
