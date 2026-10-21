"""Basic validation tests. Run only after Django migrations have been applied.

These smoke tests are not a substitute for testing with the project's fixtures
and a restored copy of its real warehouse/payment history.
"""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from web.sales_workflow import _money, _positive_int, _valid_method
from web.sales_forms import BulkSalePaymentForm
from web.models import PackSettlement
from web.models import PAYMENT_METHOD_CHOICES


class SalesWorkflowValidationTests(SimpleTestCase):
    def test_money_accepts_ugx_amounts(self):
        self.assertEqual(_money("25000.00"), Decimal("25000.00"))
        self.assertEqual(_money("25000"), Decimal("25000"))

    def test_money_rejects_fractional_shillings_and_non_finite_values(self):
        for bad in ("12.01", "12.5", "NaN", "Infinity", "oops", None):
            with self.subTest(value=bad), self.assertRaises(ValidationError):
                _money(bad)

    def test_bulk_payment_form_accepts_whole_shillings_only(self):
        self.assertTrue(
            BulkSalePaymentForm(
                data={"amount": "12500.00", "payment_method": "cash"}
            ).is_valid()
        )
        form = BulkSalePaymentForm(
            data={"amount": "12500.50", "payment_method": "cash"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("whole UGX shillings", form.errors["amount"][0])

    def test_issued_quantities_are_strictly_positive_integers(self):
        self.assertEqual(_positive_int("5"), 5)
        for bad in (-1, 0, "1.5", "not a number", True):
            with self.subTest(value=bad), self.assertRaises(ValidationError):
                _positive_int(bad)

    def test_unknown_payment_methods_rejected(self):
        self.assertEqual(_valid_method("cash", PackSettlement.PAYMENT_METHOD_CHOICES), "cash")
        with self.assertRaises(ValidationError):
            _valid_method("bitcoin", PackSettlement.PAYMENT_METHOD_CHOICES)
