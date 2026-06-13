from django.conf import settings
from django.utils import translation


class UserLanguageMiddleware:
    """
    Language policy:
    - Admin defaults to English.
    - Authenticated users use their profile language automatically.
    - Everyone else defaults to English unless a supported language is explicitly set.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.supported = {code.split("-")[0] for code, _ in settings.LANGUAGES}
        self.default = (settings.LANGUAGE_CODE or "en").split("-")[0]
        self.session_key = settings.LANGUAGE_COOKIE_NAME

    def __call__(self, request):
        active = (translation.get_language() or self.default).split("-")[0]
        target = active
        session_lang = ""
        if hasattr(request, "session"):
            session_lang = (request.session.get(self.session_key) or "").split("-")[0]

        # Keep Django admin in English by default.
        if request.path.startswith("/admin"):
            target = self.default
        elif getattr(request, "user", None) and request.user.is_authenticated:
            profile_lang = getattr(getattr(request.user, "profile", None), "language", "") or ""
            profile_lang = profile_lang.split("-")[0]
            if profile_lang == "sh":
                profile_lang = "sr"  # legacy alias migration
            # Respect Django set_language() choice from session, then persist to profile.
            if session_lang in self.supported and session_lang != profile_lang:
                target = session_lang
                profile = getattr(request.user, "profile", None)
                if profile is not None:
                    profile.language = session_lang
                    profile.save(update_fields=["language"])
            else:
                target = profile_lang if profile_lang in self.supported else self.default
        elif active not in self.supported:
            target = self.default

        if target != active:
            translation.activate(target)
            request.LANGUAGE_CODE = target
            if hasattr(request, "session"):
                request.session[self.session_key] = target

        return self.get_response(request)
