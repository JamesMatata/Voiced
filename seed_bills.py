import os
import django
from datetime import timedelta

# 1. SETUP
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Voiced.settings')
django.setup()

# 2. IMPORTS
from django.utils import timezone
from bills.models import Bill

def seed_bills():
    print("🚀 Seeding two high-impact bills for Voiced...")

    today = timezone.now().date()

    # BILL 1: FINANCE BILL 2026
    finance_bill_data = {
        "english": {
            "short_summary": "Proposed tax adjustments on digital services and essential imports to fund the 2026/27 budget.",
            "markdown_overview": "### Overview\nThis bill introduces a 15% Digital Service Tax and modifies the VAT status on several essential commodities to broaden the tax base.",
            "impact": {
                "who_is_affected": ["Content Creators", "Importers", "General Consumers"],
                "the_bottom_line": "Expect a rise in the cost of digital subscriptions and imported electronics."
            }
        },
        "swahili": {
            "short_summary": "Marekebisho ya kodi kwenye huduma za kidijitali na bidhaa muhimu ili kufadhili bajeti ya 2026/27.",
            "markdown_overview": "### Maelezo ya Jumla\nMswada huu unaleta ushuru wa 15% kwa huduma za kidijitali na kubadilisha hali ya VAT kwa bidhaa kadhaa muhimu."
        },
        "sheng": {
            "short_summary": "Rada ya ushuru mpya kwa vako za digital na vitu za majuu kusort budget ya 2026/27.",
            "markdown_overview": "### Story Yenyewe\nHii bill inaleta tax ya 15% kwa machuom za digital na kubadilisha bei za vitu basic mtaani."
        }
    }

    # BILL 2: DIGITAL HEALTH ACT 2026
    health_bill_data = {
        "english": {
            "short_summary": "A framework for the mandatory digitization of health records and the integration of AI diagnostics in public hospitals.",
            "markdown_overview": "### Overview\nThis Act establishes the National Health Data Authority to manage patient records securely and promote AI-driven medical research.",
            "impact": {
                "who_is_affected": ["Medical Practitioners", "Patients", "Tech Providers"],
                "the_bottom_line": "Streamlined hospital visits and faster diagnosis through centralized data."
            }
        },
        "swahili": {
            "short_summary": "Mfumo wa uwekaji rekodi za afya kidijitali na ujumuishaji wa utambuzi wa AI katika hospitali za umma.",
            "markdown_overview": "### Maelezo ya Jumla\nSheria hii inaanzisha Mamlaka ya Kitaifa ya Data ya Afya kusimamia rekodi za wagonjwa kwa usalama."
        },
        "sheng": {
            "short_summary": "Mpango wa kuweka ma-record za hospitali kwa system na kutumia AI kutibu watu.",
            "markdown_overview": "### Story Yenyewe\nHii Act inaleta mamlaka mpya ya kusimamia data za wagonjwa na kuhakikisha kila msee anapata matibabu haraka."
        }
    }

    bills_to_create = [
        {
            "title": "Finance Bill 2026",
            "source_url": "https://parliament.go.ke/bills/finance-2026",
            "status": Bill.Status.ACTIVE,
            "closing_date": today + timedelta(days=14),
            "ai_analysis": finance_bill_data,
            "view_count": 1240,
            "support_count": 150,
            "oppose_count": 890
        },
        {
            "title": "Digital Health Act 2026",
            "source_url": "https://parliament.go.ke/bills/health-2026",
            "status": Bill.Status.ACTIVE,
            "closing_date": today + timedelta(days=21),
            "ai_analysis": health_bill_data,
            "view_count": 850,
            "support_count": 620,
            "oppose_count": 45
        }
    ]

    for data in bills_to_create:
        Bill.objects.update_or_create(
            source_url=data["source_url"],
            defaults={
                "title": data["title"],
                "status": data["status"],
                "closing_date": data["closing_date"],
                "ai_analysis": data["ai_analysis"],
                "is_processed_by_ai": True,
                "view_count": data["view_count"],
                "support_count": data["support_count"],
                "oppose_count": data["oppose_count"],
            }
        )

    print("✅ Seeding complete. Two rich bills populated.")

if __name__ == "__main__":
    seed_bills()