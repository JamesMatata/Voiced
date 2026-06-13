import os
import logging
import requests

import africastalking

logger = logging.getLogger(__name__)

_initialized = False


def _ensure_initialized():
    global _initialized
    if _initialized:
        return
    username = os.getenv("AFRICASTALKING_USERNAME") or os.getenv("AT_USERNAME")
    api_key = os.getenv("AFRICASTALKING_API_KEY") or os.getenv("AT_API_KEY")
    if not username or not api_key:
        raise RuntimeError("Africa's Talking credentials missing (AFRICASTALKING_USERNAME / AFRICASTALKING_API_KEY).")
    africastalking.initialize(username, api_key)
    _initialized = True


def send_sms_via_africastalking(to_phone: str, message: str) -> dict:
    """
    Send a single SMS via Africa's Talking.
    Returns a dict with keys: ok (bool), raw (provider response or error text).
    """
    username = (os.getenv("AFRICASTALKING_USERNAME") or os.getenv("AT_USERNAME") or "").strip()
    sender = (os.getenv("AFRICASTALKING_SENDER_ID") or os.getenv("AT_SENDER_ID") or "").strip()
    # In sandbox, branded sender IDs are not allowed; use shortcode or omit sender.
    if username.lower() == "sandbox":
        if sender and sender.isdigit():
            pass  # allow explicit numeric shortcode such as 21000
        else:
            sender = ""
    try:
        _ensure_initialized()
    except RuntimeError as e:
        logger.warning("SMS gateway not configured: %s", e)
        return {"ok": False, "raw": str(e)}

    sms = africastalking.SMS
    try:
        print(
            f"DEBUG: AT SMS send start | username={username or 'unset'} | to={to_phone} | "
            f"sender={sender or 'None'} | message='{message[:120]}'"
        )
        if sender:
            response = sms.send(message, [to_phone], sender)
        else:
            response = sms.send(message, [to_phone])
        print(f"DEBUG: AT Response: {response}")
        ok = True
        if isinstance(response, dict):
            recipients = response.get("SMSMessageData", {}).get("Recipients", [])
            if recipients:
                ok = recipients[0].get("status") == "Success"
        return {"ok": ok, "raw": str(response)}
    except Exception as exc:
        print(f"DEBUG: AT SDK Error: {str(exc)}")
        # Some local Python/OpenSSL stacks fail TLS handshake with the SDK on sandbox.
        # Fallback to direct HTTP sandbox endpoint for dev/sandbox testing.
        if username.lower() == "sandbox" and "WRONG_VERSION_NUMBER" in str(exc):
            try:
                endpoint = "http://api.sandbox.africastalking.com/version1/messaging"
                payload = {
                    "username": "sandbox",
                    "to": to_phone,
                    "message": message,
                }
                if sender:
                    payload["from"] = sender
                direct = requests.post(
                    endpoint,
                    data=payload,
                    headers={"apiKey": os.getenv("AT_API_KEY") or os.getenv("AFRICASTALKING_API_KEY") or ""},
                    timeout=20,
                )
                raw = direct.text
                print(f"DEBUG: AT Direct Fallback Response ({direct.status_code}): {raw}")
                if direct.ok:
                    return {"ok": True, "raw": raw}
                return {"ok": False, "raw": raw}
            except Exception as fallback_exc:
                print(f"DEBUG: AT Direct Fallback Error: {fallback_exc}")
        logger.exception("Africa's Talking send failed")
        return {"ok": False, "raw": str(exc)}
