from django.conf import settings
from django.urls import reverse

from ..models import Bill, SMSLog


def normalize_kenya_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    raw = "".join(c for c in str(phone).strip() if c.isdigit() or c == "+")
    if not raw:
        return None
    if raw.startswith("+254"):
        return raw
    if raw.startswith("254"):
        return "+" + raw
    if raw.startswith("0") and len(raw) >= 10:
        return "+254" + raw[1:]
    if len(raw) == 9 and raw[0] in "17":
        return "+254" + raw
    return raw if raw.startswith("+") else None


def build_bill_sms_body(bill: Bill) -> str:
    """Compose ≤160 char body using sms_summary or a safe fallback."""
    text = (bill.sms_summary or "").strip()
    if text:
        return text[:160]
    eng = (bill.ai_analysis or {}).get("english") or {}
    short = (eng.get("short_summary") or "").strip()
    code = bill.short_id
    base = f"#{code} {short}" if short else f"Voiced Bill #{code}: summary pending."
    site = getattr(settings, "BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    path = reverse("bill_detail", kwargs={"pk": str(bill.id)})
    url = f"{site}{path}"
    if len(base) <= 160:
        return base[:160]
    # Prefer a short link line if the narrative is too long
    tail = f" More: {url}"
    room = 160 - len(tail)
    if room > 20:
        return (base[: room] + tail)[:160]
    return base[:160]


def send_bill_notification(
    user,
    bill: Bill,
    *,
    purpose=SMSLog.Purpose.BILL_NOTIFICATION,
    require_subscription: bool = True,
) -> bool:
    """
    Send one bill-related SMS and record SMSLog.
    For automated new-bill alerts, set require_subscription=True (uses profile.sms_notifications).
    USSD handoff uses require_subscription=False.
    """
    from django.contrib.auth.models import User

    profile = getattr(user, "profile", None)
    phone_raw = getattr(profile, "phone_number", None) if profile else None
    phone = normalize_kenya_phone(phone_raw)

    if not phone:
        SMSLog.objects.create(
            user=user,
            bill=bill,
            phone=phone_raw or "",
            message="",
            status=SMSLog.Status.SKIPPED,
            purpose=purpose,
            error_message="No valid phone number on profile.",
        )
        return False

    if require_subscription and profile and not profile.sms_notifications:
        SMSLog.objects.create(
            user=user,
            bill=bill,
            phone=phone,
            message="",
            status=SMSLog.Status.SKIPPED,
            purpose=purpose,
            error_message="User has not opted in to SMS alerts (sms_notifications=False).",
        )
        return False

    body = build_bill_sms_body(bill)
    from .sms_gateway import send_sms_via_africastalking

    print(
        f"DEBUG: send_bill_notification | user={getattr(user, 'id', None)} "
        f"| bill={getattr(bill, 'short_id', None)} | phone={phone} | purpose={purpose}"
    )
    result = send_sms_via_africastalking(phone, body)
    print(f"DEBUG: send_bill_notification result: {result}")
    if result["ok"]:
        SMSLog.objects.create(
            user=user,
            bill=bill,
            phone=phone,
            message=body[:200],
            status=SMSLog.Status.SENT,
            purpose=purpose,
            provider_response=result.get("raw", "")[:2000],
        )
        return True

    SMSLog.objects.create(
        user=user,
        bill=bill,
        phone=phone,
        message=body[:200],
        status=SMSLog.Status.FAILED,
        purpose=purpose,
        error_message="Provider reported failure or exception.",
        provider_response=str(result.get("raw", ""))[:2000],
    )
    return False
