import os

import requests
from django.db.models import F, Q
from django.utils import timezone

from bills.models import Bill, BillVote


def verify_smile_identity(*, id_number: str, first_name: str, last_name: str) -> dict:
    """
    Smile ID Enhanced KYC check.
    Returns: {"ok": bool, "matched": bool, "message": str}
    """
    base_url = (os.getenv("SMILE_ID_BASE_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("SMILE_ID_API_KEY") or "").strip()

    if not base_url or not api_key:
        return {
            "ok": False,
            "matched": False,
            "message": "KYC provider is not configured. Please contact support.",
        }

    payload = {
        "id_number": id_number,
        "first_name": first_name,
        "last_name": last_name,
        "country": "KE",
        "id_type": "NATIONAL_ID",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        resp = requests.post(f"{base_url}/enhanced-kyc", json=payload, headers=headers, timeout=45)
        data = resp.json() if resp.content else {}
    except Exception:
        return {
            "ok": False,
            "matched": False,
            "message": "KYC verification failed. Please try again shortly.",
        }

    if not resp.ok:
        return {
            "ok": False,
            "matched": False,
            "message": data.get("message") or "KYC verification failed. Please try again.",
        }

    matched = bool(data.get("matched") or data.get("identity_match") or data.get("result") == "match")
    return {
        "ok": True,
        "matched": matched,
        "message": data.get("message") or "",
    }


def send_identity_verified_sms(*, user, first_name: str = "") -> None:
    profile = getattr(user, "profile", None)
    phone = getattr(profile, "phone_number", None) if profile else None
    if not phone:
        return
    from bills.services.sms_gateway import send_sms_via_africastalking

    name = (first_name or user.first_name or user.username or "Citizen").strip()
    msg = (
        f"Congratulations {name}, your identity is verified. "
        "Your votes now count officially in our National Pulse reports to Parliament."
    )
    send_sms_via_africastalking(phone, msg[:160])


def promote_user_ongoing_votes_to_verified(*, user) -> None:
    """
    Upgrade already-cast votes to official counters only for ongoing published bills.
    Closed bills are intentionally ignored.
    """
    today = timezone.now().date()
    ongoing_votes = BillVote.objects.filter(
        user=user,
        bill__status=Bill.Status.PUBLISHED,
    ).filter(
        Q(bill__closing_date__isnull=True) | Q(bill__closing_date__gte=today)
    )

    support_bill_ids = list(
        ongoing_votes.filter(vote_type="support").values_list("bill_id", flat=True)
    )
    oppose_bill_ids = list(
        ongoing_votes.filter(vote_type="oppose").values_list("bill_id", flat=True)
    )

    if support_bill_ids:
        Bill.objects.filter(id__in=support_bill_ids).update(
            verified_citizen_votes=F("verified_citizen_votes") + 1,
            verified_support_count=F("verified_support_count") + 1,
        )
    if oppose_bill_ids:
        Bill.objects.filter(id__in=oppose_bill_ids).update(
            verified_citizen_votes=F("verified_citizen_votes") + 1,
            verified_oppose_count=F("verified_oppose_count") + 1,
        )
