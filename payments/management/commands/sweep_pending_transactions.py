from django.core.management.base import BaseCommand

from payments.tasks import sweep_stale_pending_transactions


class Command(BaseCommand):
    help = "Expire stale pending top-ups and refund stale pending service reservations."

    def handle(self, *args, **options):
        result = sweep_stale_pending_transactions()
        self.stdout.write(
            self.style.SUCCESS(
                "Sweep complete: "
                f"topup_expired={result.get('topup_expired', 0)}, "
                f"deduction_released={result.get('deduction_released', 0)}"
            )
        )
