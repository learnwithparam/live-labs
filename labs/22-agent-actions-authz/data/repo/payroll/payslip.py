"""A payslip for one employee and one month, in pence."""

from payroll.tax import income_tax, monthly_net, national_insurance


def payslip(employee: str, annual_pence: int) -> dict:
    return {
        "employee": employee,
        "gross": annual_pence // 12,
        "tax": income_tax(annual_pence) // 12,
        "national_insurance": national_insurance(annual_pence) // 12,
        "net": monthly_net(annual_pence),
    }
