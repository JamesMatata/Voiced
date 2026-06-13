import base64
import json
import os
from datetime import datetime
from decimal import Decimal

import requests

from django.conf import settings


def _base_url():
    env = (os.getenv("MPESA_ENVIRONMENT") or "sandbox").lower()
    if env in ("production", "live", "prod"):
        return "https://api.safaricom.co.ke"
    return "https://sandbox.safaricom.co.ke"


def get_mpesa_access_token() -> str:
    key = os.getenv("MPESA_CONSUMER_KEY") or ""
    secret = os.getenv("MPESA_CONSUMER_SECRET") or ""
    if not key or not secret:
        raise RuntimeError("MPESA_CONSUMER_KEY / MPESA_CONSUMER_SECRET not configured.")
    resp = requests.get(
        f"{_base_url()}/oauth/v1/generate?grant_type=client_credentials",
        auth=(key, secret),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _timestamp():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _password(shortcode: str, passkey: str, ts: str) -> str:
    raw = f"{shortcode}{passkey}{ts}"
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


def initiate_stk_push(
    *,
    phone: str,
    amount: Decimal,
    account_reference: str,
    transaction_desc: str,
    callback_url: str,
) -> dict:
    """
    Lipa na M-Pesa Online STK Push.
    phone: 2547... format
    Returns dict with keys: ok (bool), checkout_request_id, merchant_request_id, customer_message, raw
    """
    shortcode = os.getenv("MPESA_SHORTCODE") or ""
    passkey = os.getenv("MPESA_PASSKEY") or ""
    if not shortcode or not passkey:
        raise RuntimeError("MPESA_SHORTCODE / MPESA_PASSKEY not configured.")

    token = get_mpesa_access_token()
    ts = _timestamp()
    pwd = _password(shortcode, passkey, ts)

    party_b = phone
    if party_b.startswith("+"):
        party_b = party_b[1:]
    if party_b.startswith("0"):
        party_b = "254" + party_b[1:]

    amt_int = int(amount) if isinstance(amount, (int, float)) else int(Decimal(amount))
    payload = {
        "BusinessShortCode": shortcode,
        "Password": pwd,
        "Timestamp": ts,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amt_int,
        "PartyA": party_b,
        "PartyB": shortcode,
        "PhoneNumber": party_b,
        "CallBackURL": callback_url,
        "AccountReference": account_reference[:12],
        "TransactionDesc": transaction_desc[:13],
    }

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(
        f"{_base_url()}/mpesa/stkpush/v1/processrequest",
        headers=headers,
        data=json.dumps(payload),
        timeout=45,
    )
    try:
        data = resp.json()
    except Exception:
        return {"ok": False, "raw": resp.text}

    if resp.status_code != 200:
        return {"ok": False, "raw": data}

    cid = data.get("CheckoutRequestID")
    mid = data.get("MerchantRequestID")
    response_code = data.get("ResponseCode")
    ok = str(response_code) == "0"
    return {
        "ok": ok,
        "checkout_request_id": cid,
        "merchant_request_id": mid,
        "customer_message": data.get("CustomerMessage"),
        "raw": data,
    }


def parse_stk_callback(body: dict) -> dict:
    """
    Normalize Safaricom STK callback body.
    Returns: success (bool), amount (Decimal|None), mpesa_receipt, phone, checkout_request_id,
             result_code, result_desc
    """
    try:
        stk = body.get("Body", {}).get("stkCallback", {})
    except AttributeError:
        return {"success": False}

    result_code = stk.get("ResultCode")
    checkout_id = stk.get("CheckoutRequestID")
    metadata = stk.get("CallbackMetadata", {}).get("Item", []) if stk.get("CallbackMetadata") else []
    meta_map = {i.get("Name"): i.get("Value") for i in metadata if isinstance(i, dict)}

    receipt = meta_map.get("MpesaReceiptNumber") or ""
    amount = meta_map.get("Amount")
    phone = meta_map.get("PhoneNumber") or ""

    if result_code in (0, "0"):
        amt = Decimal(str(amount)) if amount is not None else None
        return {
            "success": True,
            "amount": amt,
            "mpesa_receipt": str(receipt),
            "phone": str(phone),
            "checkout_request_id": checkout_id,
            "result_code": result_code,
            "result_desc": stk.get("ResultDesc"),
        }

    return {
        "success": False,
        "amount": None,
        "mpesa_receipt": "",
        "phone": str(phone),
        "checkout_request_id": checkout_id,
        "result_code": result_code,
        "result_desc": stk.get("ResultDesc"),
    }


def get_callback_base_url() -> str:
    return getattr(settings, "BASE_URL", "http://127.0.0.1:8000").rstrip("/")
