import json
import logging
from decimal import Decimal
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import VerificationAttempt, Wallet
from accounts.services import promote_user_ongoing_votes_to_verified, verify_smile_identity
from bills.models import Bill, BillVote
from bills.services.sms_gateway import send_sms_via_africastalking
from core.ai_drafts import generate_submission_draft_text
from core.pdf_reports import build_legislative_report_pdf_buffer
from payments.constants import LEGAL_DRAFT_PRICE, REPORT_PRICE, VERIFICATION_FEE
from payments.models import Purchase, Transaction
from payments.services.mpesa import parse_stk_callback
from payments.services.mpesa_flow import start_stk_topup
from payments.services.wallet_ops import (
    commit_deduction,
    complete_pending_topup,
    get_or_create_wallet,
    mark_topup_failed,
    release_deduction,
    reserve_for_service,
)

logger = logging.getLogger(__name__)

signer = TimestampSigner(salt="voiced.payments.download")


def _send_ussd_verification_sms(phone: str, message: str) -> None:
    if not phone:
        return
    try:
        send_sms_via_africastalking(phone, message[:160])
    except Exception:
        logger.exception("Failed sending USSD verification SMS")


def _process_paid_ussd_verification(*, attempt: VerificationAttempt, user) -> None:
    """
    For USSD verification: run Enhanced KYC immediately after successful callback.
    ID number is already stored encrypted on VerificationAttempt.
    """
    id_number = (attempt.id_number or "").strip()
    if not id_number:
        return

    full_name = (attempt.full_name or "").strip()
    parts = full_name.split()
    first_name = (parts[0] if parts else user.first_name or "").strip()
    last_name = (" ".join(parts[1:]) if len(parts) > 1 else user.last_name or "").strip()

    result = verify_smile_identity(id_number=id_number, first_name=first_name, last_name=last_name)
    phone = getattr(getattr(user, "profile", None), "phone_number", "") or ""
    ussd_code = getattr(settings, "USSD_SHORTCODE", "*789#")

    if not result.get("ok"):
        attempt.status = VerificationAttempt.Status.PAID
        attempt.failure_reason = "KYC provider is temporarily unavailable. Retry in progress."
        attempt.save(update_fields=["status", "failure_reason", "updated_at"])
        try:
            from payments.tasks import retry_ussd_kyc_task

            retry_ussd_kyc_task.apply_async(args=[attempt.id], countdown=120)
        except Exception:
            logger.exception("Failed queuing USSD KYC retry for attempt %s", attempt.id)
        _send_ussd_verification_sms(
            phone,
            "Voiced: Payment received. Verification is delayed due to provider downtime. We will retry automatically shortly.",
        )
        return

    attempt.kyc_attempts += 1
    attempt.save(update_fields=["kyc_attempts", "updated_at"])

    if result.get("ok") and result.get("matched"):
        profile = user.profile
        was_kenyan = bool(profile.is_kenyan)
        profile.is_kenyan = True
        profile.id_number = id_number
        profile.save(update_fields=["is_kenyan", "id_number"])
        if not was_kenyan:
            promote_user_ongoing_votes_to_verified(user=user)

        attempt.status = VerificationAttempt.Status.VERIFIED
        attempt.failure_reason = ""
        attempt.save(update_fields=["status", "failure_reason", "updated_at"])
        _send_ussd_verification_sms(
            phone,
            "Voiced: Identity Verified! Your votes are now Official Citizen Votes. You can now use all features via USSD and Web.",
        )
        return

    attempt.status = VerificationAttempt.Status.FAILED
    if attempt.kyc_attempts >= 2:
        attempt.payment_status = VerificationAttempt.PaymentStatus.PENDING
        attempt.failure_reason = "Verification failed. Payment exhausted. Please pay again to retry."
        attempt.save(update_fields=["status", "payment_status", "failure_reason", "updated_at"])
        _send_ussd_verification_sms(
            phone,
            f"Voiced: Verification failed. The ID provided does not match the National Registry. No attempts left. Dial {ussd_code} to retry with new payment.",
        )
        return

    attempt.failure_reason = (
        "Verification failed. The ID provided does not match the National Registry. You have 1 more attempt left."
    )
    attempt.save(update_fields=["status", "failure_reason", "updated_at"])
    _send_ussd_verification_sms(
        phone,
        f"Voiced: Verification failed. The ID provided does not match the National Registry. You have 1 more attempt left. Dial {ussd_code} to retry.",
    )


def _friendly_topup_failure(txn: Transaction) -> str:
    """User-facing message for a failed STK top-up (description may include Safaricom text)."""
    desc = (txn.description or "").strip()
    low = desc.lower()
    if "cancel" in low or "1037" in desc or "1032" in desc:
        return "Payment was cancelled on your phone."
    if "insufficient" in low and "balance" in low:
        return "Insufficient M-Pesa balance. Top up and try again."
    if "stk_denied" in low or "did not accept" in low:
        return "M-Pesa could not start that payment. Try again."
    if 3 < len(desc) < 220:
        return desc
    return "Payment was not completed."


def _start_verification_stk(*, user, attempt: VerificationAttempt, phone: str):
    result = start_stk_topup(user=user, phone=phone, amount=VERIFICATION_FEE)
    if not result.get("ok"):
        return JsonResponse(
            {"ok": False, "mode": "mpesa", "error": result.get("error") or "M-Pesa request failed."},
            status=400,
        )

    checkout_id = result.get("checkout_request_id") or ""
    txn_id = result.get("transaction_id")
    if txn_id:
        Transaction.objects.filter(id=txn_id, user=user).update(
            description=f"KYC verification fee | attempt:{attempt.id}",
        )
    attempt.mpesa_checkout_request_id = checkout_id
    attempt.payment_reference = checkout_id
    attempt.payment_status = VerificationAttempt.PaymentStatus.PENDING
    attempt.status = VerificationAttempt.Status.PENDING
    attempt.failure_reason = ""
    attempt.save(
        update_fields=[
            "mpesa_checkout_request_id",
            "payment_reference",
            "payment_status",
            "status",
            "failure_reason",
            "updated_at",
        ]
    )
    return JsonResponse(
        {
            "ok": True,
            "mode": "mpesa",
            "message": result.get("message") or "Check your phone to complete payment.",
            "checkout_request_id": checkout_id,
        }
    )


@login_required
@require_POST
def initiate_verification_payment(request):
    method = (request.POST.get("method") or request.GET.get("method") or "wallet").strip().lower()
    phone = (request.POST.get("phone") or request.GET.get("phone") or "").strip()

    attempt = VerificationAttempt.objects.create(
        user=request.user,
        payment_status=VerificationAttempt.PaymentStatus.PENDING,
        status=VerificationAttempt.Status.PENDING,
        amount_paid=Decimal("0.00"),
    )

    if method not in ("wallet", "mpesa"):
        return JsonResponse({"ok": False, "error": "Invalid payment method."}, status=400)

    if method == "wallet":
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=request.user)
            updated = Wallet.objects.filter(
                id=wallet.id,
                balance__gte=F("reserved_balance") + VERIFICATION_FEE,
            ).update(balance=F("balance") - VERIFICATION_FEE)
            if updated:
                tx = Transaction.objects.create(
                    user=request.user,
                    wallet=wallet,
                    amount=VERIFICATION_FEE,
                    transaction_type=Transaction.TransactionType.DEDUCTION,
                    status=Transaction.Status.COMPLETED,
                    description=f"KYC verification fee | attempt:{attempt.id}",
                )
                attempt.status = VerificationAttempt.Status.PAID
                attempt.payment_status = VerificationAttempt.PaymentStatus.PAID
                attempt.amount_paid = VERIFICATION_FEE
                attempt.payment_reference = f"WALLET-{tx.id}"
                attempt.failure_reason = ""
                attempt.save(
                    update_fields=[
                        "status",
                        "payment_status",
                        "amount_paid",
                        "payment_reference",
                        "failure_reason",
                        "updated_at",
                    ]
                )
                return JsonResponse(
                    {
                        "ok": True,
                        "mode": "wallet",
                        "status": attempt.status,
                        "message": "Verification fee paid from wallet.",
                    }
                )

        # Insufficient wallet balance -> fallback to M-Pesa as requested.
        return _start_verification_stk(user=request.user, attempt=attempt, phone=phone)

    return _start_verification_stk(user=request.user, attempt=attempt, phone=phone)


def _parse_bill_id(raw):
    try:
        return UUID(str(raw))
    except (ValueError, TypeError):
        return None


def _national_pulse_ready(bill: Bill) -> bool:
    total = bill.verified_support_count + bill.verified_oppose_count
    return (
        total >= 50
        and bool(bill.pdf_report)
        and not bill.report_generation_in_progress
    )


def _legislative_ready(bill: Bill) -> bool:
    return (
        BillVote.objects.filter(bill=bill, user__profile__is_kenyan=True)
        .exclude(reason__isnull=True)
        .exclude(reason__exact="")
        .exists()
    )


def run_prepare_bill_purchase(user, bill_id, service_type, kind):
    """
    Shared prepare logic for JSON + HTMX.
    Returns dict: result in (ok, already_purchased, need_mpesa, error), plus fields.
    """
    kind = (kind or "national_pulse").strip()
    out = {"result": "error", "error": "Invalid request.", "bill_id": None, "kind": kind}

    if not bill_id or service_type not in (
        Transaction.ServiceType.REPORT,
        Transaction.ServiceType.DRAFT,
    ):
        return out

    try:
        bill = Bill.objects.get(id=bill_id, status=Bill.Status.PUBLISHED)
    except Bill.DoesNotExist:
        out["error"] = "Bill not found."
        return out

    wallet = get_or_create_wallet(user)
    price = REPORT_PRICE if service_type == Transaction.ServiceType.REPORT else LEGAL_DRAFT_PRICE

    if Purchase.objects.filter(user=user, bill=bill, service_type=service_type).exists():
        return {
            "result": "already_purchased",
            "bill": bill,
            "wallet": wallet,
            "price": price,
            "service_type": service_type,
            "kind": kind,
        }

    if service_type == Transaction.ServiceType.REPORT:
        if kind == "national_pulse" and not _national_pulse_ready(bill):
            out["error"] = "National Pulse report is not ready yet."
            out["code"] = "not_ready"
            return out
        if kind == "legislative" and not _legislative_ready(bill):
            out["error"] = "Not enough participation data for this report."
            out["code"] = "not_ready"
            return out

    if service_type == Transaction.ServiceType.DRAFT:
        if bill.is_closed:
            out["error"] = "This bill is closed."
            out["code"] = "closed"
            return out
        user_vote = BillVote.objects.filter(bill=bill, user=user).first()
        if not user_vote or not user_vote.reason:
            out["error"] = "Vote with a reason is required."
            out["code"] = "not_ready"
            return out

    txn, err = reserve_for_service(user, bill, service_type, price)
    if err == "already_purchased":
        return {
            "result": "already_purchased",
            "bill": bill,
            "wallet": wallet,
            "price": price,
            "service_type": service_type,
            "kind": kind,
        }
    if err == "insufficient":
        return {
            "result": "need_mpesa",
            "bill": bill,
            "wallet": wallet,
            "price": price,
            "service_type": service_type,
            "kind": kind,
        }

    token = signer.sign(str(txn.id))
    wallet.refresh_from_db()
    return {
        "result": "ok",
        "token": token,
        "bill": bill,
        "wallet": wallet,
        "price": price,
        "service_type": service_type,
        "kind": kind,
    }


@login_required
@require_POST
def prepare_purchase(request):
    """Reserve wallet funds or report that M-Pesa / existing purchase applies."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    bill_id = _parse_bill_id(payload.get("bill_id"))
    service_type = payload.get("service_type")
    kind = (payload.get("kind") or "national_pulse").strip()

    r = run_prepare_bill_purchase(request.user, bill_id, service_type, kind)

    if r["result"] == "error":
        code = r.get("code")
        status = 400
        if code == "closed":
            status = 403
        elif code == "not_ready" and service_type == Transaction.ServiceType.DRAFT:
            status = 425
        return JsonResponse({"error": r["error"], "code": code}, status=status)

    price = r["price"]
    wallet = r["wallet"]

    if r["result"] == "already_purchased":
        return JsonResponse(
            {
                "already_purchased": True,
                "price": str(price),
                "wallet_balance": str(wallet.balance),
                "available": str(wallet.available_balance),
            }
        )

    if r["result"] == "need_mpesa":
        st = r["service_type"]
        return JsonResponse(
            {
                "already_purchased": False,
                "need_mpesa": True,
                "price": str(price),
                "wallet_balance": str(wallet.balance),
                "available": str(wallet.available_balance),
                "suggested_stk_amount": str(REPORT_PRICE if st == Transaction.ServiceType.REPORT else LEGAL_DRAFT_PRICE),
            }
        )

    return JsonResponse(
        {
            "already_purchased": False,
            "token": r["token"],
            "price": str(price),
            "wallet_balance": str(wallet.balance),
            "available": str(wallet.available_balance),
            "need_mpesa": False,
        }
    )


@login_required
@require_http_methods(["GET"])
def wallet_balance_json(request):
    w = get_or_create_wallet(request.user)
    return JsonResponse(
        {
            "balance": str(w.balance),
            "reserved": str(w.reserved_balance),
            "available": str(w.available_balance),
        }
    )


@login_required
@require_http_methods(["GET"])
def mpesa_topup_status(request):
    """
    Poll pending STK top-up outcome (callback updates Transaction).
    Used by wallet / bill payment modals to detect failure vs success without misleading balance-only heuristics.
    """
    raw_id = request.GET.get("transaction_id")
    if raw_id is None or str(raw_id).strip() == "":
        return JsonResponse({"ok": False, "error": "transaction_id required"}, status=400)
    try:
        txn_id = int(raw_id)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "invalid transaction_id"}, status=400)

    txn = Transaction.objects.filter(
        id=txn_id,
        user=request.user,
        transaction_type=Transaction.TransactionType.TOPUP,
    ).first()
    if not txn:
        return JsonResponse({"ok": False, "error": "Not found"}, status=404)

    w = get_or_create_wallet(request.user)
    out = {
        "ok": True,
        "status": txn.status,
        "failure_message": _friendly_topup_failure(txn) if txn.status == Transaction.Status.FAILED else "",
        "balance": str(w.balance),
        "reserved": str(w.reserved_balance),
        "available": str(w.available_balance),
    }
    return JsonResponse(out)


@login_required
@require_POST
def initiate_mpesa_stk(request):
    """Top up wallet via STK (exact amount for the service when suggested)."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    phone = (payload.get("phone") or "").strip()
    amount_raw = payload.get("amount")
    try:
        amount = Decimal(str(amount_raw)) if amount_raw is not None else REPORT_PRICE
    except Exception:
        amount = REPORT_PRICE

    result = start_stk_topup(user=request.user, phone=phone, amount=amount)
    if not result.get("ok"):
        return JsonResponse({"ok": False, "error": result.get("error", "STK failed")}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "checkout_request_id": result.get("checkout_request_id"),
            "message": result.get("message") or "Check your phone to complete payment.",
            "transaction_id": result.get("transaction_id"),
        }
    )


def _hx_trigger_response(trigger_payload: dict, *, status=200):
    """HTMX: fire client events via HX-Trigger header."""
    resp = HttpResponse("", status=status)
    resp["HX-Trigger"] = json.dumps(trigger_payload)
    return resp


@login_required
@require_POST
def htmx_bill_prepare_wallet(request):
    """HTMX: reserve wallet for bill purchase; errors swap into modal, success uses HX-Trigger."""
    bill_id = _parse_bill_id(request.POST.get("bill_id"))
    service_type = request.POST.get("service_type")
    kind = (request.POST.get("kind") or "national_pulse").strip()

    r = run_prepare_bill_purchase(request.user, bill_id, service_type, kind)

    if r["result"] == "error":
        return _hx_trigger_response({"voicedModalError": {"message": r["error"]}})

    if r["result"] == "need_mpesa":
        short = str(r["price"])
        return _hx_trigger_response(
            {
                "voicedModalWarning": {
                    "message": f"Balance insufficient (KES {short} needed). Please pay via M-Pesa or top up your wallet.",
                }
            }
        )

    if r["result"] == "already_purchased":
        return _hx_trigger_response(
            {
                "voicedPaymentPurchased": {
                    "service_type": r["service_type"],
                    "kind": r.get("kind") or "",
                }
            }
        )

    return _hx_trigger_response(
        {
            "voicedPaymentReady": {
                "token": r["token"],
                "service_type": r["service_type"],
                "kind": r.get("kind") or "",
            }
        }
    )


@login_required
@require_POST
def htmx_bill_mpesa_stk(request):
    """HTMX: STK Push for bill flow (same wallet top-up; amount = service price)."""
    phone = (request.POST.get("phone") or "").strip()
    amount_raw = request.POST.get("amount")
    try:
        amount = Decimal(str(amount_raw)) if amount_raw else REPORT_PRICE
    except Exception:
        amount = REPORT_PRICE

    result = start_stk_topup(user=request.user, phone=phone, amount=amount)
    if not result.get("ok"):
        return _hx_trigger_response(
            {"voicedModalError": {"message": result.get("error") or "M-Pesa request failed."}}
        )

    return _hx_trigger_response(
        {
            "voicedMpesaStkSent": {
                "transaction_id": result.get("transaction_id"),
                "message": result.get("message")
                or "Check your phone and enter your M-Pesa PIN. We will continue automatically when payment completes.",
            }
        }
    )


@csrf_exempt
@require_POST
def mpesa_stk_callback(request):
    try:
        body = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ResultCode": 1, "ResultDesc": "bad json"})

    parsed = parse_stk_callback(body)
    checkout_id = parsed.get("checkout_request_id")
    if not checkout_id:
        return JsonResponse({"ResultCode": 0, "ResultDesc": "ignored"})

    txn = Transaction.objects.filter(
        mpesa_checkout_request_id=checkout_id,
        transaction_type=Transaction.TransactionType.TOPUP,
        status=Transaction.Status.PENDING,
    ).first()
    if not txn:
        logger.warning("No pending TOPUP for checkout %s", checkout_id)
        return JsonResponse({"ResultCode": 0, "ResultDesc": "ok"})

    linked = VerificationAttempt.objects.filter(
        user=txn.user,
        mpesa_checkout_request_id=checkout_id,
    ).order_by("-attempted_at").first()
    if not linked and "attempt:" in (txn.description or ""):
        try:
            aid = int((txn.description or "").split("attempt:")[-1].strip().split()[0])
        except Exception:
            aid = None
        if aid:
            linked = VerificationAttempt.objects.filter(id=aid, user=txn.user).first()

    is_kyc_fee = bool(linked) or ("KYC verification fee" in (txn.description or ""))

    if parsed.get("success"):
        receipt = parsed.get("mpesa_receipt") or ""
        if is_kyc_fee:
            Transaction.objects.filter(id=txn.id).update(
                status=Transaction.Status.COMPLETED,
                mpesa_receipt_number=receipt,
            )
        else:
            try:
                complete_pending_topup(txn.id, receipt)
            except Exception:
                logger.exception("Failed to complete top-up %s", txn.id)
        if linked:
            linked.status = VerificationAttempt.Status.PAID
            linked.payment_status = VerificationAttempt.PaymentStatus.PAID
            linked.amount_paid = VERIFICATION_FEE
            linked.payment_reference = receipt or linked.payment_reference
            linked.failure_reason = ""
            linked.save(
                update_fields=[
                    "status",
                    "payment_status",
                    "amount_paid",
                    "payment_reference",
                    "failure_reason",
                    "updated_at",
                ]
            )
            if "source:ussd" in (txn.description or ""):
                try:
                    _process_paid_ussd_verification(attempt=linked, user=txn.user)
                except Exception:
                    logger.exception("USSD post-payment KYC processing failed for attempt %s", linked.id)
    else:
        mark_topup_failed(txn.id, parsed.get("result_desc") or "failed")
        linked = VerificationAttempt.objects.filter(
            user=txn.user,
            mpesa_checkout_request_id=checkout_id,
        ).order_by("-attempted_at").first()
        if linked:
            linked.status = VerificationAttempt.Status.FAILED
            linked.payment_status = VerificationAttempt.PaymentStatus.PENDING
            linked.failure_reason = parsed.get("result_desc") or "M-Pesa callback failed."
            linked.save(update_fields=["status", "payment_status", "failure_reason", "updated_at"])

    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


def _verify_download_token(token):
    if not token:
        return None
    try:
        raw = signer.unsign(token, max_age=600)
        return int(raw)
    except (BadSignature, SignatureExpired, ValueError):
        return None


def _stream_pdf_from_file(bill: Bill, txn_id: int | None):
    def gen():
        completed = False
        try:
            f = bill.pdf_report.open("rb")
            try:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    yield chunk
                completed = True
            finally:
                f.close()
        finally:
            if txn_id:
                if completed:
                    commit_deduction(txn_id)
                else:
                    release_deduction(txn_id)

    return gen


def _stream_pdf_from_buffer(buf, txn_id: int | None):
    def gen():
        completed = False
        try:
            data = buf.read()
            completed = True
            yield data
        finally:
            if txn_id:
                if completed:
                    commit_deduction(txn_id)
                else:
                    release_deduction(txn_id)

    return gen


@login_required
@require_http_methods(["GET"])
def download_national_pulse(request, bill_id):
    bill = get_object_or_404(Bill, id=bill_id, status=Bill.Status.PUBLISHED)

    if Purchase.objects.filter(
        user=request.user, bill=bill, service_type=Transaction.ServiceType.REPORT
    ).exists():
        if not _national_pulse_ready(bill):
            return JsonResponse({"error": "Report not available."}, status=425)
        gen = _stream_pdf_from_file(bill, None)()
        resp = StreamingHttpResponse(gen, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="national_pulse_{bill.id}.pdf"'
        return resp

    txn_id = _verify_download_token(request.GET.get("token"))
    if not txn_id:
        return JsonResponse({"error": "Payment required", "code": "payment_required"}, status=402)

    txn = get_object_or_404(
        Transaction,
        id=txn_id,
        user=request.user,
        bill=bill,
        service_type=Transaction.ServiceType.REPORT,
        status=Transaction.Status.PENDING,
        transaction_type=Transaction.TransactionType.DEDUCTION,
    )

    if not _national_pulse_ready(bill):
        release_deduction(txn.id)
        return JsonResponse({"error": "Report not ready; reservation released."}, status=425)

    gen = _stream_pdf_from_file(bill, txn.id)()
    resp = StreamingHttpResponse(gen, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="national_pulse_{bill.id}.pdf"'
    return resp


@login_required
@require_http_methods(["GET"])
def download_legislative_report(request, bill_id):
    bill = get_object_or_404(Bill, id=bill_id, status=Bill.Status.PUBLISHED)

    if Purchase.objects.filter(
        user=request.user, bill=bill, service_type=Transaction.ServiceType.REPORT
    ).exists():
        if not _legislative_ready(bill):
            return JsonResponse({"error": "Insufficient data."}, status=400)
        try:
            buf = build_legislative_report_pdf_buffer(bill)
        except Exception as exc:
            return JsonResponse({"error": str(exc)}, status=503)
        gen = _stream_pdf_from_buffer(buf, None)()
        resp = StreamingHttpResponse(gen, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="Voiced_Report_{bill.id}.pdf"'
        return resp

    txn_id = _verify_download_token(request.GET.get("token"))
    if not txn_id:
        return JsonResponse({"error": "Payment required", "code": "payment_required"}, status=402)

    txn = get_object_or_404(
        Transaction,
        id=txn_id,
        user=request.user,
        bill=bill,
        service_type=Transaction.ServiceType.REPORT,
        status=Transaction.Status.PENDING,
        transaction_type=Transaction.TransactionType.DEDUCTION,
    )

    if not _legislative_ready(bill):
        release_deduction(txn.id)
        return JsonResponse({"error": "Insufficient data; reservation released."}, status=400)

    try:
        buf = build_legislative_report_pdf_buffer(bill)
    except Exception:
        release_deduction(txn.id)
        return JsonResponse({"error": "PDF generation failed."}, status=503)

    gen = _stream_pdf_from_buffer(buf, txn.id)()
    resp = StreamingHttpResponse(gen, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="Voiced_Report_{bill.id}.pdf"'
    return resp


@login_required
@require_POST
def generate_draft_paid(request, bill_id):
    bill = get_object_or_404(Bill, id=bill_id, status=Bill.Status.PUBLISHED)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    token = payload.get("token")

    if Purchase.objects.filter(
        user=request.user, bill=bill, service_type=Transaction.ServiceType.DRAFT
    ).exists():
        try:
            text = generate_submission_draft_text(bill, request.user)
        except PermissionError:
            return JsonResponse({"error": "This bill is closed."}, status=403)
        except ValueError:
            return JsonResponse(
                {"status": "not_ready", "error": "You must cast a vote with a reason to generate a submission."},
                status=425,
            )
        except Exception:
            return JsonResponse({"error": "Draft generation failed."}, status=503)
        return JsonResponse({"draft": text})

    txn_id = _verify_download_token(token)
    if not txn_id:
        return JsonResponse({"error": "Payment required", "code": "payment_required"}, status=402)

    txn = get_object_or_404(
        Transaction,
        id=txn_id,
        user=request.user,
        bill=bill,
        service_type=Transaction.ServiceType.DRAFT,
        status=Transaction.Status.PENDING,
        transaction_type=Transaction.TransactionType.DEDUCTION,
    )

    if bill.is_closed:
        release_deduction(txn.id)
        return JsonResponse({"error": "This bill is closed."}, status=403)

    try:
        text = generate_submission_draft_text(bill, request.user)
    except ValueError:
        release_deduction(txn.id)
        return JsonResponse(
            {"status": "not_ready", "error": "You must cast a vote with a reason to generate a submission."},
            status=425,
        )
    except Exception:
        release_deduction(txn.id)
        return JsonResponse({"error": "Draft generation failed."}, status=503)

    commit_deduction(txn.id)
    return JsonResponse({"draft": text})
