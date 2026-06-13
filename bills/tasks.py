from celery import shared_task
from django.db import transaction
from django.core.files.base import ContentFile
from django.utils import timezone
from django.conf import settings
from django.urls import reverse
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import io
from .models import Bill, ScrapeLog, SMSLog
from .services.ai_engine import BillAnalyzer
from .services.participation_feedback import send_vote_feedback_sms
from .services.sms_gateway import send_sms_via_africastalking
from .services.scraper import ParliamentScraper, MyGovScraper, GazetteScraper
from .services.voice_gateway import place_voice_summary_call
from .utils import generate_bill_audio
from notifications.logic import notify_bill_outcome_participants


def _bill_public_url(bill: Bill) -> str:
    site = getattr(settings, "BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{site}{reverse('bill_detail', kwargs={'pk': str(bill.id)})}"


@shared_task
def backfill_bill_sms_summary(bill_id):
    """One-time AI repair when sms_summary is missing (existing bills or failed extraction)."""
    try:
        bill = Bill.objects.get(id=bill_id)
    except Bill.DoesNotExist:
        return "Not found"
    if (bill.sms_summary or "").strip():
        return "Already set"
    try:
        analyzer = BillAnalyzer()
    except ValueError:
        return "No GEMINI_API_KEY"

    bill_url = _bill_public_url(bill)
    eng = (bill.ai_analysis or {}).get("english") or {}
    short = eng.get("short_summary", "") or eng.get("long_description", "")
    context = (short or "").strip()
    if len(context) < 40:
        pdf_text = analyzer.extract_text_from_pdf(bill.source_url)
        if pdf_text:
            context = pdf_text[:8000]
    if not context:
        return "No context"

    sms = analyzer.generate_sms_summary_repair(context, str(bill.short_id), bill_url)
    if sms:
        bill.sms_summary = sms[:160]
        bill.save(update_fields=["sms_summary"])
        return "Backfilled"
    return "Repair failed"


@shared_task
def send_ussd_bill_summary_sms(user_id, bill_id):
    from django.contrib.auth.models import User
    from .services.sms_delivery import send_bill_notification

    try:
        user = User.objects.get(id=user_id)
        bill = Bill.objects.get(id=bill_id)
    except (User.DoesNotExist, Bill.DoesNotExist):
        return "Missing user or bill"

    print(f"DEBUG: send_ussd_bill_summary_sms task executing | user_id={user_id} bill_id={bill_id}")
    send_bill_notification(
        user,
        bill,
        purpose=SMSLog.Purpose.USSD_VIEW,
        require_subscription=False,
    )
    return "Queued"


@shared_task
def notify_subscribers_sms_for_new_bill(bill_id):
    from django.contrib.auth.models import User
    from .services.sms_delivery import send_bill_notification

    try:
        bill = Bill.objects.get(id=bill_id)
    except Bill.DoesNotExist:
        return "Bill not found"

    qs = (
        User.objects.filter(profile__sms_notifications=True)
        .exclude(profile__phone_number__isnull=True)
        .exclude(profile__phone_number="")
    )
    for user in qs.iterator():
        send_bill_notification(
            user,
            bill,
            purpose=SMSLog.Purpose.BILL_NOTIFICATION,
            require_subscription=True,
        )
    return "Done"


@shared_task
def process_bill_with_ai(bill_id):
    try:
        bill = Bill.objects.get(id=bill_id)
    except Bill.DoesNotExist:
        return "Not found"

    if bill.is_processed_by_ai:
        return "Skip"

    try:
        analyzer = BillAnalyzer()
    except ValueError as e:
        return str(e)

    pdf_text = analyzer.extract_text_from_pdf(bill.source_url)

    if not pdf_text:
        return "PDF Error"

    bill_url = _bill_public_url(bill)
    analysis_data = analyzer.generate_comprehensive_analysis(
        pdf_text,
        bill_code=str(bill.short_id),
        bill_web_url=bill_url,
    )
    if analysis_data:
        sms_summary = (analysis_data.pop("sms_summary", "") or "").strip()[:160]
        eng = analysis_data.get("english") or {}
        sw = analysis_data.get("swahili") or {}
        sh = analysis_data.get("sheng") or {}

        title_en = (eng.get("translated_title") or bill.title or "").strip()
        title_sw = (sw.get("translated_title") or "").strip()
        title_sh = (sh.get("translated_title") or "").strip()
        summary_en = (eng.get("short_summary") or "").strip()
        summary_sw = (sw.get("short_summary") or "").strip()
        summary_sh = (sh.get("short_summary") or "").strip()
        ai_sum_en = (eng.get("markdown_overview") or "").strip()
        ai_sum_sw = (sw.get("markdown_overview") or "").strip()
        ai_sum_sh = (sh.get("markdown_overview") or "").strip()

        bill.ai_analysis = analysis_data
        bill.is_processed_by_ai = True
        bill.status = Bill.Status.PENDING_REVIEW
        bill.title_en = title_en
        bill.title_sw = title_sw
        bill.title_sh = title_sh
        bill.summary_en = summary_en
        bill.summary_sw = summary_sw
        bill.summary_sh = summary_sh
        bill.ai_analysis_summary_en = ai_sum_en
        bill.ai_analysis_summary_sw = ai_sum_sw
        bill.ai_analysis_summary_sh = ai_sum_sh
        bill.is_sw_ready = bool(summary_sw and ai_sum_sw)
        bill.is_sh_ready = bool(summary_sh and ai_sum_sh)
        if sms_summary:
            bill.sms_summary = sms_summary
        bill.save()
        if not (bill.sms_summary or "").strip():
            backfill_bill_sms_summary.delay(str(bill.id))
        return f"Awaiting Human Approval: {bill.title}"
    return "Analysis Failed"


def run_all_scrapers_sync():
    scrapers = [ParliamentScraper(), MyGovScraper(), GazetteScraper()]
    total_added = 0

    for scraper in scrapers:
        try:
            result = scraper.scrape()
        except Exception as exc:
            result = {"success": False, "error": str(exc), "data": []}

        log = ScrapeLog.objects.create(
            source_name=scraper.SOURCE_NAME,
            was_successful=result.get("success", False),
            error_message=result.get("error"),
            bills_found=len(result.get("data", []))
        )

        if not result.get("success"):
            continue

        added_count = 0
        with transaction.atomic():
            for item in result.get("data", []):
                source_url = item.get("source_url")
                normalized_title = item.get("normalized_title")
                if not source_url:
                    continue

                duplicate_qs = Bill.objects.filter(source_url=source_url)
                if normalized_title:
                    duplicate_qs = duplicate_qs | Bill.objects.filter(title__iexact=item.get("title", ""))

                if duplicate_qs.exists():
                    continue

                gov_level = item.get("government_level", Bill.GovernmentLevel.NATIONAL)
                county_val = (item.get("county") or "").strip()[:100]
                if gov_level == Bill.GovernmentLevel.COUNTY and not county_val:
                    gov_level = Bill.GovernmentLevel.NATIONAL
                    county_val = ""

                new_bill, created = Bill.objects.get_or_create(
                    source_url=source_url,
                    defaults={
                        'title': item.get("title", "Untitled Bill"),
                        'document_hash': item.get("document_hash", ""),
                        'status': Bill.Status.DRAFT,
                        'government_level': gov_level,
                        'county': county_val,
                    }
                )
                if not created:
                    continue
                added_count += 1
                total_added += 1
                process_bill_with_ai.delay(new_bill.id)

        log.bills_added = added_count
        log.save()
    return f"Done: {total_added} bills added"


@shared_task
def run_all_scrapers():
    return run_all_scrapers_sync()


@shared_task
def generate_bill_report_pdf(bill_id):
    try:
        bill = Bill.objects.get(id=bill_id)
    except Bill.DoesNotExist:
        return "Bill not found"

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    ai_analysis = bill.ai_analysis or {}
    english = ai_analysis.get('english', {})
    swahili = ai_analysis.get('swahili', {})
    sheng = ai_analysis.get('sheng', {})

    timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S %Z")
    total_votes = bill.verified_support_count + bill.verified_oppose_count
    support_pct = round((bill.verified_support_count / total_votes) * 100, 1) if total_votes > 0 else 0
    oppose_pct = round((bill.verified_oppose_count / total_votes) * 100, 1) if total_votes > 0 else 0

    y = height - 60
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(40, y, "VOICED: National Pulse Report")
    y -= 20
    pdf.setFont("Helvetica", 10)
    pdf.drawString(40, y, f"Generated: {timestamp}")

    y -= 30
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(40, y, "Bill Title")
    y -= 16
    pdf.setFont("Helvetica", 11)
    pdf.drawString(40, y, bill.title[:110])

    y -= 30
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(40, y, "AI Summaries")
    y -= 18

    sections = [
        ("English", english.get('short_summary', 'Not available.')),
        ("Kiswahili", swahili.get('short_summary', 'Not available.')),
        ("Sheng", sheng.get('short_summary', 'Not available.')),
    ]

    for label, text in sections:
        if y < 120:
            pdf.showPage()
            y = height - 60
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(40, y, label)
        y -= 14
        pdf.setFont("Helvetica", 10)
        for line in str(text)[:600].split('\n'):
            if y < 80:
                pdf.showPage()
                y = height - 60
            pdf.drawString(40, y, line[:115])
            y -= 12
        y -= 8

    if y < 140:
        pdf.showPage()
        y = height - 60
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(40, y, "Vote Breakdown")
    y -= 16
    pdf.setFont("Helvetica", 11)
    pdf.drawString(40, y, f"Support: {bill.verified_support_count} ({support_pct}%)")
    y -= 14
    pdf.drawString(40, y, f"Oppose: {bill.verified_oppose_count} ({oppose_pct}%)")

    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawString(40, 30, "Generated by Voiced AI - Transparency for every Kenyan.")
    pdf.save()
    buffer.seek(0)

    try:
        if bill.pdf_report:
            bill.pdf_report.delete(save=False)

        filename = f"national_pulse_{bill.id}.pdf"
        bill.pdf_report.save(filename, ContentFile(buffer.getvalue()), save=False)
        bill.report_generation_in_progress = False
        bill.save(update_fields=['pdf_report', 'report_generation_in_progress'])
        return f"Report generated for {bill.id}"
    except Exception:
        bill.report_generation_in_progress = False
        bill.save(update_fields=['report_generation_in_progress'])
        return "Report generation failed"


@shared_task
def notify_bill_outcome_participants_task(outcome_id):
    """Send bill outcome impact updates to all participants."""
    return notify_bill_outcome_participants(outcome_id)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5)
def generate_bill_audio_task(bill_id):
    try:
        bill = Bill.objects.get(id=bill_id)
    except Bill.DoesNotExist:
        return "Bill not found"
    generated = generate_bill_audio(bill)
    return {"generated": generated}


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5)
def send_vote_feedback_sms_task(user_id, bill_id, receipt_id):
    from django.contrib.auth.models import User

    try:
        user = User.objects.get(id=user_id)
        bill = Bill.objects.get(id=bill_id)
    except (User.DoesNotExist, Bill.DoesNotExist):
        return "Missing user or bill"
    send_vote_feedback_sms(user=user, bill=bill, receipt_id=str(receipt_id or ""))
    return "sent"


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5)
def place_voice_summary_call_task(user_id, bill_id):
    from django.contrib.auth.models import User

    try:
        user = User.objects.get(id=user_id)
        bill = Bill.objects.get(id=bill_id)
    except (User.DoesNotExist, Bill.DoesNotExist):
        return "Missing user or bill"
    profile = getattr(user, "profile", None)
    phone = getattr(profile, "phone_number", "") if profile else ""
    if not phone:
        return "No phone"
    base = getattr(settings, "BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    callback_url = f"{base}/engagement/voice/callback/?bill_id={bill.id}&user_id={user.id}"
    return place_voice_summary_call(to_phone=phone, callback_url=callback_url)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5)
def send_voice_listen_charge_sms_task(user_id, new_balance):
    from django.contrib.auth.models import User

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return "Missing user"
    phone = getattr(getattr(user, "profile", None), "phone_number", "") or ""
    if not phone:
        return "No phone"
    msg = f"Voiced: KES 5.00 deducted for Voice Summary. Your new balance is KES {new_balance}."
    send_sms_via_africastalking(phone, msg[:160])
    return "sent"