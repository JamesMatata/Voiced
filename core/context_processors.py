from django.conf import settings
from django.utils.translation import get_language


def language_context(request):
    current = (get_language() or settings.LANGUAGE_CODE or "en").split("-")[0]
    available = [{"code": code, "label": label} for code, label in settings.LANGUAGES]
    return {
        "current_language_code": current,
        "available_languages": available,
    }
