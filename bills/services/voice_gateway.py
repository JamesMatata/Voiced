import logging
import os

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
        raise RuntimeError("Africa's Talking credentials missing.")
    africastalking.initialize(username, api_key)
    _initialized = True


def place_voice_summary_call(*, to_phone: str, callback_url: str) -> dict:
    """
    Initiate outbound IVR call through Africa's Talking Voice.
    """
    try:
        _ensure_initialized()
        at_voice = africastalking.Voice
        caller_id = os.getenv("AFRICASTALKING_VOICE_NUMBER") or os.getenv("AT_VOICE_NUMBER") or ""
        response = at_voice.call(caller_id, [to_phone], callback_url=callback_url)
        return {"ok": True, "raw": str(response)}
    except Exception as exc:
        logger.exception("Voice call initiation failed")
        return {"ok": False, "raw": str(exc)}
