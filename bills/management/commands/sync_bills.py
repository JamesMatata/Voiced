from django.core.management.base import BaseCommand
from bills.models import Bill, ScrapeLog
from bills.services.scraper import ParliamentScraper
from bills.services.ai_engine import BillAnalyzer
from datetime import datetime


class Command(BaseCommand):
    help = 'Scrapes new bills from official sources and processes them via AI.'

    def handle(self, *args, **options):
        self.stdout.write("Running scraper...")
        self.scrape_and_process_new_bills()

    def scrape_and_process_new_bills(self):
        scraper = ParliamentScraper()
        result = scraper.scrape()

        if not result['success']:
            ScrapeLog.objects.create(
                source_name=scraper.SOURCE_NAME,
                was_successful=False,
                error_message=result['error']
            )
            self.stdout.write(self.style.ERROR(f"Scrape failed: {result['error']}"))
            return

        added_count = 0
        analyzer = BillAnalyzer()

        for item in result['data']:
            if not Bill.objects.filter(source_url=item['source_url']).exists():
                bill = Bill.objects.create(
                    title=item['title'],
                    source_url=item['source_url'],
                    document_hash=item['document_hash'],
                    status=Bill.Status.DRAFT
                )

                self.stdout.write(f"Processing new bill: {bill.title}")
                pdf_text = analyzer.extract_text_from_pdf(bill.source_url)

                if pdf_text:
                    analysis = analyzer.generate_comprehensive_analysis(pdf_text)
                    if analysis:
                        bill.ai_analysis = analysis
                        bill.is_processed_by_ai = True
                        bill.status = Bill.Status.ACTIVE

                        closing_date_str = analysis.get('closing_date')
                        if closing_date_str:
                            try:
                                bill.closing_date = datetime.strptime(closing_date_str, '%Y-%m-%d').date()
                            except ValueError:
                                pass

                        bill.save()
                        added_count += 1
                    else:
                        self.stdout.write(self.style.WARNING("AI Analysis failed. Kept as DRAFT."))

        ScrapeLog.objects.create(
            source_name=scraper.SOURCE_NAME,
            bills_found=len(result['data']),
            bills_added=added_count
        )
        self.stdout.write(self.style.SUCCESS(f"Complete. Added {added_count} new bills."))