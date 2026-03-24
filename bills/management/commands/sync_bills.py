from django.core.management.base import BaseCommand
from bills.tasks import run_all_scrapers_sync


class Command(BaseCommand):
    help = 'Scrapes all configured sources and queues HITL pipeline.'

    def handle(self, *args, **options):
        self.stdout.write("Running scraper...")
        result = run_all_scrapers_sync()
        self.stdout.write(self.style.SUCCESS(result))