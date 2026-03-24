from celery import shared_task
from django.db import transaction
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
        bill.status = Bill.Status.REVIEW
        bill.save()
        return f"Awaiting Human Approval: {bill.title}"
    return "Analysis Failed"

@shared_task
def run_all_scrapers():
    scrapers = [ParliamentScraper(), MyGovScraper(), GazetteScraper()]
    total_added = 0

    for scraper in scrapers:
        result = scraper.scrape()
        log = ScrapeLog.objects.create(
            source_name=scraper.SOURCE_NAME,
            was_successful=result["success"],
            error_message=result["error"],
            bills_found=len(result["data"])
        )

        if not result["success"]:
            continue

        added_count = 0
        with transaction.atomic():
            for item in result["data"]:
                if Bill.objects.filter(source_url=item["source_url"]).exists():
                    continue

                new_bill = Bill.objects.create(
                    title=item["title"],
                    source_url=item["source_url"],
                    document_hash=item["document_hash"],
                )
                added_count += 1
                total_added += 1
                process_bill_with_ai.delay(new_bill.id)

        log.bills_added = added_count
        log.save()
    return f"Done: {total_added} bills added"