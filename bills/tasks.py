from celery import shared_task
from django.db import transaction
from django.core.files.base import ContentFile
from django.utils import timezone
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import io
from .models import Bill, ScrapeLog
from .services.ai_engine import BillAnalyzer
from .services.scraper import ParliamentScraper, MyGovScraper, GazetteScraper

@shared_task
def process_bill_with_ai(bill_id):
    try:
        bill = Bill.objects.get(id=bill_id)
    except Bill.DoesNotExist:
        return "Not found"

    if bill.is_processed_by_ai:
        return "Skip"

    analyzer = BillAnalyzer()
    pdf_text = analyzer.extract_text_from_pdf(bill.source_url)

    if not pdf_text:
        return "PDF Error"

    analysis_data = analyzer.generate_comprehensive_analysis(pdf_text)
    if analysis_data:
        bill.ai_analysis = analysis_data
        bill.is_processed_by_ai = True
        bill.status = Bill.Status.PENDING_REVIEW
        bill.save()
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

                new_bill, created = Bill.objects.get_or_create(
                    source_url=source_url,
                    defaults={
                        'title': item.get("title", "Untitled Bill"),
                        'document_hash': item.get("document_hash", ""),
                        'status': Bill.Status.DRAFT,
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
    total_votes = bill.support_count + bill.oppose_count
    support_pct = round((bill.support_count / total_votes) * 100, 1) if total_votes > 0 else 0
    oppose_pct = round((bill.oppose_count / total_votes) * 100, 1) if total_votes > 0 else 0

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
    pdf.drawString(40, y, f"Support: {bill.support_count} ({support_pct}%)")
    y -= 14
    pdf.drawString(40, y, f"Oppose: {bill.oppose_count} ({oppose_pct}%)")

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