from decimal import Decimal
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import Wallet
from payments.constants import (
    DEDUCTION_PENDING_TTL_MINUTES,
    LEGAL_DRAFT_PRICE,
    REPORT_PRICE,
    TOPUP_PENDING_TTL_MINUTES,
)
from payments.models import Purchase, Transaction


def get_or_create_wallet(user):
    w, _ = Wallet.objects.get_or_create(user=user)
    return w


def price_for_service(service_type: str) -> Decimal:
    if service_type == Transaction.ServiceType.REPORT:
        return REPORT_PRICE
    if service_type == Transaction.ServiceType.DRAFT:
        return LEGAL_DRAFT_PRICE
    raise ValueError("Unknown service type")


@transaction.atomic
def release_pending_deductions(user, bill, service_type):
    qs = Transaction.objects.select_for_update().filter(
        user=user,
        bill=bill,
        service_type=service_type,
        transaction_type=Transaction.TransactionType.DEDUCTION,
        status=Transaction.Status.PENDING,
    )
    for txn in qs:
        Wallet.objects.filter(pk=txn.wallet_id).update(
            reserved_balance=F("reserved_balance") - txn.amount
        )
        txn.status = Transaction.Status.FAILED
        txn.description = (txn.description or "")[:200] + " [released]"
        txn.save(update_fields=["status", "description", "updated_at"])


@transaction.atomic
def reserve_for_service(user, bill, service_type: str, amount: Decimal):
    """
    Reserve funds for a download. Caller must verify business rules (PDF ready, etc.) before calling.
    Returns Transaction instance or None if insufficient funds.
    """
    wallet = Wallet.objects.select_for_update().get(user=user)
    release_stale_pending_deductions(user)
    release_pending_deductions(user, bill, service_type)

    if Purchase.objects.filter(user=user, bill=bill, service_type=service_type).exists():
        return None, "already_purchased"

    if wallet.available_balance < amount:
        return None, "insufficient"

    txn = Transaction.objects.create(
        user=user,
        wallet=wallet,
        amount=amount,
        transaction_type=Transaction.TransactionType.DEDUCTION,
        status=Transaction.Status.PENDING,
        bill=bill,
        service_type=service_type,
        description=f"Reserved for {service_type}",
    )
    Wallet.objects.filter(pk=wallet.pk).update(reserved_balance=F("reserved_balance") + amount)
    return txn, None


@transaction.atomic
def commit_deduction(txn_id):
    txn = Transaction.objects.select_for_update().select_related("wallet").get(id=txn_id)
    if txn.status != Transaction.Status.PENDING:
        return
    if txn.transaction_type != Transaction.TransactionType.DEDUCTION:
        return
    Wallet.objects.filter(pk=txn.wallet_id).update(
        balance=F("balance") - txn.amount,
        reserved_balance=F("reserved_balance") - txn.amount,
    )
    txn.status = Transaction.Status.COMPLETED
    txn.save(update_fields=["status", "updated_at"])
    try:
        Purchase.objects.create(
            user=txn.user,
            bill=txn.bill,
            service_type=txn.service_type,
            transaction=txn,
        )
    except IntegrityError:
        pass


@transaction.atomic
def release_deduction(txn_id, note: str = ""):
    try:
        txn = Transaction.objects.select_for_update().select_related("wallet").get(id=txn_id)
    except Transaction.DoesNotExist:
        return None
    if txn.status != Transaction.Status.PENDING:
        return None
    if txn.transaction_type != Transaction.TransactionType.DEDUCTION:
        return None
    Wallet.objects.filter(pk=txn.wallet_id).update(
        reserved_balance=F("reserved_balance") - txn.amount
    )
    txn.status = Transaction.Status.FAILED
    note_text = note.strip() if note else ""
    if note:
        txn.description = ((txn.description or "")[:120] + " | " + note[:120])[:255]
        txn.save(update_fields=["status", "description", "updated_at"])
    else:
        txn.save(update_fields=["status", "updated_at"])

    # Log a visible wallet credit entry when reserved money is released.
    refund_desc = "Refunded unused reservation"
    if txn.bill_id and txn.service_type == Transaction.ServiceType.DRAFT:
        refund_desc = f"Refunded unused reservation for AI draft: {txn.bill.title[:90]}"
    elif txn.bill_id and txn.service_type == Transaction.ServiceType.REPORT:
        refund_desc = f"Refunded unused reservation for analysis report: {txn.bill.title[:80]}"
    if note_text:
        refund_desc = (refund_desc[:190] + f" | {note_text[:60]}")[:255]
    Transaction.objects.create(
        user=txn.user,
        wallet=txn.wallet,
        amount=txn.amount,
        transaction_type=Transaction.TransactionType.REFUND,
        status=Transaction.Status.COMPLETED,
        bill=txn.bill,
        service_type=txn.service_type,
        description=refund_desc,
    )
    return txn


@transaction.atomic
def apply_topup(user, amount: Decimal, receipt: str, description: str = ""):
    wallet = Wallet.objects.select_for_update().get(user=user)
    Wallet.objects.filter(pk=wallet.pk).update(balance=F("balance") + amount)
    return Transaction.objects.create(
        user=user,
        wallet=wallet,
        amount=amount,
        transaction_type=Transaction.TransactionType.TOPUP,
        status=Transaction.Status.COMPLETED,
        description=description or "M-Pesa top-up",
        mpesa_receipt_number=receipt,
    )


@transaction.atomic
def complete_pending_topup(txn_id, receipt_number: str):
    txn = Transaction.objects.select_for_update().select_related("wallet").get(id=txn_id)
    if txn.status != Transaction.Status.PENDING:
        return
    if txn.transaction_type != Transaction.TransactionType.TOPUP:
        return
    Wallet.objects.filter(pk=txn.wallet_id).update(balance=F("balance") + txn.amount)
    txn.status = Transaction.Status.COMPLETED
    txn.mpesa_receipt_number = receipt_number
    txn.save(update_fields=["status", "mpesa_receipt_number", "updated_at"])


@transaction.atomic
def mark_topup_failed(txn_id: int, note: str = ""):
    txn = Transaction.objects.select_for_update().get(id=txn_id)
    if txn.status != Transaction.Status.PENDING:
        return
    txn.status = Transaction.Status.FAILED
    if note:
        combined = ((txn.description or "")[:120] + " | " + note[:200])[:255]
        txn.description = combined
    txn.save(update_fields=["status", "description", "updated_at"])


@transaction.atomic
def release_stale_pending_deductions(user):
    cutoff = timezone.now() - timedelta(minutes=DEDUCTION_PENDING_TTL_MINUTES)
    stale_ids = list(
        Transaction.objects.select_for_update()
        .filter(
            user=user,
            transaction_type=Transaction.TransactionType.DEDUCTION,
            status=Transaction.Status.PENDING,
            created_at__lt=cutoff,
        )
        .values_list("id", flat=True)
    )
    for txn_id in stale_ids:
        release_deduction(txn_id, note="auto_refund_unused_reservation")
    return stale_ids


def stale_pending_deductions_qs():
    cutoff = timezone.now() - timedelta(minutes=DEDUCTION_PENDING_TTL_MINUTES)
    return Transaction.objects.filter(
        transaction_type=Transaction.TransactionType.DEDUCTION,
        status=Transaction.Status.PENDING,
        created_at__lt=cutoff,
    )


def stale_pending_topups_qs():
    cutoff = timezone.now() - timedelta(minutes=TOPUP_PENDING_TTL_MINUTES)
    return Transaction.objects.filter(
        transaction_type=Transaction.TransactionType.TOPUP,
        status=Transaction.Status.PENDING,
        created_at__lt=cutoff,
    )
