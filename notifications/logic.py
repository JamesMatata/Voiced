from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
from django.utils.html import strip_tags

from bills.models import BillOutcome, BillVote, SMSLog
from bills.services.sms_gateway import send_sms_via_africastalking
from chat.models import ChatMessage
from notifications.models import Notification


def _public_bill_link(bill):
    site = getattr(settings, "BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{site}{reverse('bill_detail', kwargs={'pk': str(bill.id)})}"


def _truncate_sms(text: str, limit: int = 160):
    txt = (text or "").strip()
    if len(txt) <= limit:
        return txt
    return txt[: max(0, limit - 1)].rstrip() + "…"


def _should_send(user_id: int, outcome_id: int, channel: str) -> bool:
    # 24h per user/outcome/channel anti-spam guard.
    key = f"bill_outcome:{outcome_id}:user:{user_id}:{channel}"
    return cache.add(key, "1", timeout=24 * 60 * 60)


def _send_outcome_email(user, subject: str, plain: str, html: str):
    if not getattr(user, "email", ""):
        return False
    msg = EmailMultiAlternatives(
        subject=subject,
        body=plain,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@voiced.co.ke"),
        to=[user.email],
    )
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=True)
    return True


def _send_in_app_notification(user, title: str, message: str, link: str):
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
                "data": {"title": title, "message": message, "link": link, "type": Notification.Type.SYSTEM},
            },
        )
    except Exception:
        # In-app DB notification is already persisted; websocket push can fail silently.
        pass


def notify_bill_outcome_participants(outcome_id: int) -> dict:
    try:
        outcome = BillOutcome.objects.select_related("bill").get(id=outcome_id)
    except BillOutcome.DoesNotExist:
        return {"processed": 0, "sms_sent": 0, "email_sent": 0}

    bill = outcome.bill
    bill_url = _public_bill_link(bill)
    vote_qs = BillVote.objects.filter(bill=bill).select_related("user__profile")
    message_qs = ChatMessage.objects.filter(bill=bill).select_related("user__profile")

    vote_map = {v.user_id: v for v in vote_qs}
    participant_ids = set(vote_map.keys()) | set(message_qs.values_list("user_id", flat=True))
    total_participants = len(participant_ids)

    sms_sent = 0
    email_sent = 0
    processed = 0

    for uid in participant_ids:
        vote = vote_map.get(uid)
        user = vote.user if vote else message_qs.filter(user_id=uid).first().user
        processed += 1

        is_kenyan = bool(getattr(getattr(user, "profile", None), "is_kenyan", False))
        participation_kind = "official" if is_kenyan else "pulse"
        vote_text = "commented"
        if vote:
            vote_text = "supported" if vote.vote_type == "support" else "opposed"

        title = f"Outcome: {bill.title[:120]}"
        in_app_msg = (
            f"{bill.title} is now {outcome.get_final_status_display()}. "
            f"You {vote_text}; your {participation_kind} participation was 1 of {total_participants} voices."
        )
        _send_in_app_notification(
            user=user,
            title=title,
            message=in_app_msg,
            link=reverse("bill_detail", kwargs={"pk": str(bill.id)}),
        )

        if _should_send(user.id, outcome.id, "sms"):
            phone = (getattr(getattr(user, "profile", None), "phone_number", "") or "").strip()
            if phone:
                sms_text = _truncate_sms(
                    f"Voiced: The {bill.title} has {outcome.get_final_status_display()}. "
                    f"Your voice was 1 of {total_participants} citizens who spoke up. Summary: {bill_url}"
                )
                sms_resp = send_sms_via_africastalking(phone, sms_text)
                SMSLog.objects.create(
                    user=user,
                    bill=bill,
                    phone=phone,
                    message=sms_text,
                    status=SMSLog.Status.SENT if sms_resp.get("ok") else SMSLog.Status.FAILED,
                    purpose=SMSLog.Purpose.BILL_OUTCOME,
                    provider_response=str(sms_resp.get("raw", ""))[:500],
                    error_message="" if sms_resp.get("ok") else str(sms_resp.get("raw", ""))[:500],
                )
                if sms_resp.get("ok"):
                    sms_sent += 1

        if _should_send(user.id, outcome.id, "email"):
            greeting = user.first_name or user.username or "Citizen"
            outcome_text = outcome.get_final_status_display()
            official_word = "official" if is_kenyan else "pulse"
            html = f"""
            <h3>Hi {greeting},</h3>
            <p>Remember the <strong>{bill.title}</strong>? It just <strong>{outcome_text}</strong>.</p>
            <p>You {vote_text}. Your <strong>{official_word}</strong> voice was part of the data recorded on our platform.</p>
            <p><strong>Outcome summary:</strong> {outcome.summary_text}</p>
            <p>Thank you for participating in Kenya's democracy.</p>
            <p><a href="{bill_url}">View bill and final outcome</a></p>
            """
            plain = strip_tags(html)
            if _send_outcome_email(
                user=user,
                subject=f"VOICED: {bill.title} is now {outcome_text}",
                plain=plain,
                html=html,
            ):
                email_sent += 1

    return {"processed": processed, "sms_sent": sms_sent, "email_sent": email_sent}
