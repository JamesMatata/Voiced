from celery import shared_task
from django.db import transaction
from .models import Bill, ScrapeLog
from .services.scraper import ParliamentScraper, MyGovScraper, GazetteScraper
from .services.ai_engine import BillAnalyzer


@shared_task
def process_bill_with_ai(bill_id):
    """Downloads PDF, passes to Gemini, and updates the database record."""
    try:
        bill = Bill.objects.get(id=bill_id)
    except Bill.DoesNotExist:
        return "Bill not found."

    if bill.is_processed_by_ai:
        return "Already processed."

    analyzer = BillAnalyzer()
    pdf_text = analyzer.extract_text_from_pdf(bill.source_url)

    if not pdf_text:
        return "Failed to extract PDF."

    analysis_data = analyzer.generate_comprehensive_analysis(pdf_text)
    if analysis_data:
        bill.ai_analysis = analysis_data
        bill.is_processed_by_ai = True
        bill.status = Bill.Status.ACTIVE
        bill.save()
        return f"Processed: {bill.title}"
    return "AI Analysis failed."


@shared_task
def run_all_scrapers():
    """Loops through all sources, deduplicates against DB, and queues AI processing."""
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
                # Deduplication: Check if exact URL or matching Title exists
                if Bill.objects.filter(source_url=item["source_url"]).exists() or \
                        Bill.objects.filter(title__icontains=item["title"][:15]).exists():
                    continue

                new_bill = Bill.objects.create(
                    title=item["title"],
                    source_url=item["source_url"],
                    document_hash=item["document_hash"],
                )
                added_count += 1
                total_added += 1

                # Hand off to AI immediately
                process_bill_with_ai.delay(new_bill.id)

        log.bills_added = added_count
        log.save()

    return f"Scrape complete. {total_added} new bills added."