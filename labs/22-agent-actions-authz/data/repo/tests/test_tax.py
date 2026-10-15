import unittest

from payroll.tax import income_tax, monthly_net, national_insurance


class TaxTest(unittest.TestCase):
    def test_no_tax_under_the_allowance(self):
        self.assertEqual(income_tax(1_200_000), 0)

    def test_basic_rate(self):
        self.assertEqual(income_tax(3_000_000), 348_600)

    def test_national_insurance(self):
        self.assertEqual(national_insurance(3_000_000), 139_440)

    def test_monthly_net_on_a_round_salary(self):
        # 30,000 a year: 25,119.60 net, so 2,093.30 a month exactly
        self.assertEqual(monthly_net(3_000_000), 209_330)

    def test_monthly_net_rounds_to_the_nearest_penny(self):
        # 28,000.12 a year: 23,679.69 net, 1,973.3075 a month, so 1,973.31 on the payslip
        self.assertEqual(monthly_net(2_800_012), 197_331)
