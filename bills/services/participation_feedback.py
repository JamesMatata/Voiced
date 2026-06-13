from django.conf import settings

from bills.services.sms_gateway import send_sms_via_africastalking


def send_vote_feedback_sms(*, user, bill, receipt_id: str) -> None:
    profile = getattr(user, "profile", None)
    phone = getattr(profile, "phone_number", None) if profile else None
    if not phone:
        return

    title = (bill.title or f"Bill #{bill.short_id}")[:12]
    verify_opt = getattr(settings, "USSD_VERIFY_OPTION_INDEX", "4")
    if bool(getattr(profile, "is_kenyan", False)):
        msg = (
            f"Voiced: Official vote on {title} recorded. Receipt:{receipt_id}. Asante."
        )
    else:
        msg = (
            f"Voiced: Vote on {title} recorded. Verify ID via USSD option {verify_opt}. "
            f"Receipt:{receipt_id}"
        )
    send_sms_via_africastalking(phone, msg[:160])
