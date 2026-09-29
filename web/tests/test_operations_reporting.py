from decimal import Decimal
from django.test import SimpleTestCase
from web.services.reporting.operations import dec, pct

class OperationsArithmeticTests(SimpleTestCase):
    def test_yield_percentage(self):
        self.assertEqual(pct(Decimal('90'), Decimal('100')), Decimal('90.0'))

    def test_zero_input(self):
        self.assertEqual(pct(Decimal('0'), Decimal('0')), Decimal('0.00'))

    def test_null_quantities(self):
        self.assertEqual(dec(None), Decimal('0.00'))
