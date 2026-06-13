from django.utils.translation import gettext as _

from bills.models import MissingTranslation


def resolve_bill_language_payload(bill, language_code: str):
    lang = (language_code or "en").split("-")[0]
    fallback = False
    if lang == "sh":
        lang = "sr"
    target = lang if lang in ("sw", "sr") else "en"

    if target == "sw":
        ready = bool(
            (bill.title_sw or "").strip()
            or (bill.summary_sw or "").strip()
            or (bill.ai_analysis_summary_sw or "").strip()
            or ((bill.ai_analysis or {}).get("swahili", {}).get("short_summary") or "").strip()
            or ((bill.ai_analysis or {}).get("swahili", {}).get("markdown_overview") or "").strip()
            or bool(bill.is_sw_ready)
        )
        if ready:
            title = bill.title_sw or bill.title_en or bill.title
            summary = (
                bill.summary_sw
                or ((bill.ai_analysis or {}).get("swahili", {}).get("short_summary"))
                or bill.summary_en
            )
            analysis_summary = (
                bill.ai_analysis_summary_sw
                or ((bill.ai_analysis or {}).get("swahili", {}).get("markdown_overview"))
                or bill.ai_analysis_summary_en
            )
        else:
            fallback = True
            title = bill.title_en or bill.title
            summary = bill.summary_en
            analysis_summary = bill.ai_analysis_summary_en
            MissingTranslation.objects.get_or_create(
                bill=bill,
                language=MissingTranslation.Language.SW,
                resolved=False,
                defaults={"note": "Auto-flagged via fallback"},
            )
    elif target == "sr":
        ready = bool(
            (bill.title_sh or "").strip()
            or (bill.summary_sh or "").strip()
            or (bill.ai_analysis_summary_sh or "").strip()
            or ((bill.ai_analysis or {}).get("sheng", {}).get("short_summary") or "").strip()
            or ((bill.ai_analysis or {}).get("sheng", {}).get("markdown_overview") or "").strip()
            or bool(bill.is_sh_ready)
        )
        if ready:
            title = bill.title_sh or bill.title_en or bill.title
            summary = (
                bill.summary_sh
                or ((bill.ai_analysis or {}).get("sheng", {}).get("short_summary"))
                or bill.summary_en
            )
            analysis_summary = (
                bill.ai_analysis_summary_sh
                or ((bill.ai_analysis or {}).get("sheng", {}).get("markdown_overview"))
                or bill.ai_analysis_summary_en
            )
        else:
            fallback = True
            title = bill.title_en or bill.title
            summary = bill.summary_en
            analysis_summary = bill.ai_analysis_summary_en
            MissingTranslation.objects.get_or_create(
                bill=bill,
                language=MissingTranslation.Language.SH,
                resolved=False,
                defaults={"note": "Auto-flagged via fallback"},
            )
    else:
        title = bill.title_en or bill.title
        summary = bill.summary_en
        analysis_summary = bill.ai_analysis_summary_en

    fallback_message = ""
    if fallback:
        name = "Kiswahili" if target == "sw" else "Sheng"
        fallback_message = _("Translation for this bill is not available in %(lang)s yet. Showing English version.") % {
            "lang": name
        }

    return {
        "language_code": target if not fallback else "en",
        "requested_language_code": lang,
        "fallback_to_english": fallback,
        "fallback_message": fallback_message,
        "title": title or bill.title,
        "summary": summary or "",
        "analysis_summary": analysis_summary or "",
    }
