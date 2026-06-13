"""Shared M-Pesa STK top-up flow (wallet page + API)."""
import logging
from decimal import Decimal

from payments.models import Transaction
from payments.services.mpesa import get_callback_base_url, initiate_stk_push
from payments.services.wallet_ops import get_or_create_wallet, mark_topup_failed

logger = logging.getLogger(__name__)


def start_stk_topup(*, user, phone: str, amount: Decimal) -> dict:
    """
    Create pending TOPUP, call Safaricom STK Push.
    Returns: {ok: bool, error?: str, message?: str, checkout_request_id?: str, transaction_id?: int, raw?: any}
    """
    phone = (phone or "").strip()
    if not phone:
        return {"ok": False, "error": "Phone number required (2547…)."}

    try:
        amount = Decimal(str(amount))
    except Exception:
        return {"ok": False, "error": "Invalid amount."}

    if amount <= 0 or amount > Decimal("150000"):
        return {"ok": False, "error": "Invalid amount."}

    wallet = get_or_create_wallet(user)
    txn = Transaction.objects.create(
        user=user,
        wallet=wallet,
        amount=amount,
        transaction_type=Transaction.TransactionType.TOPUP,
        status=Transaction.Status.PENDING,
        description="M-Pesa STK top-up",
    )

    callback = f"{get_callback_base_url()}/payments/mpesa/callback/"
    try:
        result = initiate_stk_push(
            phone=phone,
            amount=amount,
            account_reference=f"V{user.id}",
            transaction_desc="Voiced wallet",
            callback_url=callback,
        )
    except Exception as exc:
        logger.exception("STK initiate failed")
        mark_topup_failed(txn.id, str(exc)[:80])
        return {"ok": False, "error": str(exc), "transaction_id": txn.id}

    if not result.get("ok"):
        mark_topup_failed(txn.id, "stk_denied")
        return {"ok": False, "error": "M-Pesa did not accept the request. Try again.", "raw": result.get("raw")}

    Transaction.objects.filter(pk=txn.id).update(
        mpesa_checkout_request_id=result.get("checkout_request_id") or "",
        mpesa_merchant_request_id=result.get("merchant_request_id") or "",
    )

    return {
        "ok": True,
        "message": result.get("customer_message") or "Check your phone to complete payment.",
        "checkout_request_id": result.get("checkout_request_id"),
        "transaction_id": txn.id,
    }
