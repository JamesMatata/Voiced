from celery import shared_task

from accounts.models import VerificationAttempt
from payments.constants import VERIFICATION_FEE
from payments.services.reliability_notifications import (
    notify_refund_unused_reservation,
    notify_stale_topup_expired,
)
from payments.services.wallet_ops import (
    mark_topup_failed,
    release_deduction,
    stale_pending_deductions_qs,
    stale_pending_topups_qs,
)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5)
def retry_ussd_kyc_task(attempt_id):
    from payments.views import _process_paid_ussd_verification

    attempt = VerificationAttempt.objects.select_related("user", "user__profile").filter(id=attempt_id).first()
    if not attempt:
        return "Attempt not found"
    if attempt.payment_status != VerificationAttempt.PaymentStatus.PAID:
        return "Attempt not in paid state"
    if attempt.amount_paid < VERIFICATION_FEE:
        return "Payment not settled"
    _process_paid_ussd_verification(attempt=attempt, user=attempt.user)
    return "Retried"


@shared_task
def sweep_stale_pending_transactions():
    """
    Reliability sweeper:
    - stale pending TOPUP -> FAILED (expired)
    - stale pending DEDUCTION reservation -> release reserved wallet funds
    """
    topups = list(stale_pending_topups_qs().select_related("user", "wallet"))
    deductions = list(stale_pending_deductions_qs().select_related("user", "wallet", "bill"))

    topup_expired = 0
    deduction_released = 0

    for txn in topups:
        mark_topup_failed(txn.id, "timeout_no_callback")
        txn.refresh_from_db(fields=["status", "description"])
        if txn.status == txn.Status.FAILED:
            topup_expired += 1
            notify_stale_topup_expired(txn=txn)

    for txn in deductions:
        released = release_deduction(txn.id, note="auto_refund_unused_reservation")
        if released:
            deduction_released += 1
            notify_refund_unused_reservation(txn=released)

    return {
        "topup_expired": topup_expired,
        "deduction_released": deduction_released,
    }
