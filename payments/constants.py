from decimal import Decimal

# KES pricing for premium services
REPORT_PRICE = Decimal("20.00")
LEGAL_DRAFT_PRICE = Decimal("10.00")
VERIFICATION_FEE = Decimal("60.00")

# Reliability windows for pending financial states
TOPUP_PENDING_TTL_MINUTES = 10
DEDUCTION_PENDING_TTL_MINUTES = 15
