from django.core.management.base import BaseCommand
from bills.models import ScrapeLog, Bill


class Command(BaseCommand):
    help = "Prints latest successful scrape status per source and pending review count."

    def handle(self, *args, **options):
        sources = ["Parliament Tracker", "MyGov Portal", "Kenya Gazette"]
        self.stdout.write("Last successful scrape by source:")

        for source in sources:
            log = ScrapeLog.objects.filter(source_name=source, was_successful=True).order_by('-created_at').first()
            if not log:
                self.stdout.write(f"- {source}: no successful scrape recorded")
                continue
            self.stdout.write(
                f"- {source}: {log.created_at:%Y-%m-%d %H:%M}, found={log.bills_found}, added={log.bills_added}"
            )

        pending_count = Bill.objects.filter(status=Bill.Status.PENDING_REVIEW).count()
        self.stdout.write(f"Awaiting human review: {pending_count}")
