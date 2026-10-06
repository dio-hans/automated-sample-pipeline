from datetime import date
from decimal import Decimal
from django.test import SimpleTestCase
from web.services.reporting.finance import ageing_bucket, money


class FinanceHelpersTests(SimpleTestCase):
    def test_ageing_boundaries(self):
        self.assertEqual([ageing_bucket(n) for n in (0,30,31,60,61,90,91)], ['0–30 days','0–30 days','31–60 days','31–60 days','61–90 days','61–90 days','91+ days'])
    def test_decimal_is_exact(self):
        self.assertEqual(money('100.10') + money('0.20'), Decimal('100.30'))