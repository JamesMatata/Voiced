import os
import django
import random
from datetime import timedelta

# 1. SETUP FIRST
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Voiced.settings')
django.setup()

# 2. IMPORTS SECOND
from django.utils import timezone
from bills.models import Bill

def seed_bills():
    print("🚀 Starting bill seeding...")

    bill_templates = [
        ("National Infrastructure Fund Bill", "Establishes a sovereign fund to mobilize KSh 5 trillion for BETA projects."),
        ("Digital Health Amendment Act", "Integrating TaifaCare platform with AI-powered fraud detection."),
        ("Miscellaneous Fees and Levies Bill", "Adjustments to export levies and import declaration fees."),
        ("Cancer Prevention and Control Bill", "Recommendations for regional oncology centers expansion."),
        ("Labour Migration Management Bill", "Regulating the recruitment of Kenyan workers for overseas jobs."),
        ("Tobacco Control Amendment Bill", "New regulations on electronic nicotine delivery systems."),
        ("Election Offences (No. 2) Bill", "Stricter penalties for digital misinformation during campaigns."),
        ("Public Finance Management Bill", "New reporting timelines for County Treasury departments."),
        ("Meteorology Bill 2026", "Restructuring the Kenya Meteorological Department into an Authority."),
        ("Virtual Asset Service Providers Bill", "Framework for licensing cryptocurrency exchanges in Kenya."),
        ("Social Protection Bill 2025", "Legal framework for the expanded Inua Jamii program."),
        ("Kenya Roads Amendment Bill", "Restructuring funding for Class B and C road maintenance."),
        ("Seeds and Plant Varieties Bill", "Protecting indigenous seed varieties from unauthorized patenting."),
        ("Autism Management Bill", "Mandatory inclusion of neurodivergence training in CBC curriculum."),
        ("Energy Amendment Bill 2026", "Incentives for private sector investment in geothermal wells."),
        ("Mining Amendment Bill", "Increasing royalty shares for local community trust funds."),
        ("Education Laws Bill", "Harmonizing university funding models with student needs."),
        ("National Cohesion Bill", "Legal measures against ethnic profiling in public service hiring."),
        ("Wildlife Conservation Bill", "New compensation rates for human-wildlife conflict victims."),
        ("National Aviation Policy Bill", "Strategy for making JKIA a regional hub for SAF.")
    ]

    today = timezone.now().date()
    created_count = 0

    for i, (title, summary) in enumerate(bill_templates):
        if i < 8:
            status = Bill.Status.ACTIVE
            closing_date = today + timedelta(days=random.randint(5, 25))
        elif i < 15:
            status = Bill.Status.CLOSED
            closing_date = today - timedelta(days=random.randint(1, 15))
        else:
            status = Bill.Status.CLOSED
            closing_date = today - timedelta(days=random.randint(35, 50))

        ai_data = {
            "english": {
                "short_summary": summary,
                "long_description": f"Detailed policy analysis for {title}.",
                "markdown_overview": f"### Overview\nThis bill seeks to improve {title.lower()}.",
                "impact": {
                    "who_is_affected": ["General Public", "Industry Stakeholders"],
                    "what_is_affected": ["Legal Framework", "Compliance"],
                    "how_they_are_affected": "Changes in operational costs.",
                    "the_bottom_line": "A shift towards modernization."
                }
            }
        }

        bill, created = Bill.objects.update_or_create(
            source_url=f"https://parliament.go.ke/bills/test-{i}",
            defaults={
                "title": title,
                "status": status,
                "closing_date": closing_date,
                "ai_analysis": ai_data,
                "is_processed_by_ai": True,
                "view_count": random.randint(50, 2500),
                "support_count": random.randint(10, 500),
                "oppose_count": random.randint(10, 500),
            }
        )

        if created:
            created_count += 1

    print(f"✅ Seeding complete. Added {created_count} bills.")

if __name__ == "__main__":
    seed_bills()