"""Income tax and employee National Insurance for England, tax year 2026/27, in pence."""

PERSONAL_ALLOWANCE = 1_257_000
BASIC_RATE_LIMIT = 5_027_000
HIGHER_RATE_LIMIT = 12_514_000
NI_PRIMARY_THRESHOLD = 1_257_000
NI_UPPER_LIMIT = 5_027_000


def income_tax(annual_pence: int) -> int:
    taxable = max(0, annual_pence - PERSONAL_ALLOWANCE)
    basic = min(taxable, BASIC_RATE_LIMIT - PERSONAL_ALLOWANCE)
    higher = min(max(0, taxable - basic), HIGHER_RATE_LIMIT - BASIC_RATE_LIMIT)
    additional = max(0, taxable - basic - higher)
    return round(basic * 0.20 + higher * 0.40 + additional * 0.45)


def national_insurance(annual_pence: int) -> int:
    main = max(0, min(annual_pence, NI_UPPER_LIMIT) - NI_PRIMARY_THRESHOLD)
    upper = max(0, annual_pence - NI_UPPER_LIMIT)
    return round(main * 0.08 + upper * 0.02)


def monthly_net(annual_pence: int) -> int:
    yearly = annual_pence - income_tax(annual_pence) - national_insurance(annual_pence)
    return int(yearly / 12)
