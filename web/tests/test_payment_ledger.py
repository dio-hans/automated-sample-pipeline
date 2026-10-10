from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from web.models import (
    AccountHolder,
    Blend,
    PackRelease,
    PackSettlement,
    PackSize,
    PackagedProduct,
    PaymentReceipt,
    StockRequest,
    StockRequestItem,
)
from web.sales_workflow import record_payment, settle_release
from web.services.reporting.finance import get_finance_report
from web.services.reporting.periods import ReportPeriod


class PaymentReceiptLedgerTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.cashier = user_model.objects.create_user(
            username="payment-ledger-cashier",
            password="test-password",
        )
        self.account = AccountHolder.objects.create(
            name="Payment Ledger Customer",
            account_type="customer",
        )
        blend = Blend.objects.create(name="Payment Ledger Blend")
        size = PackSize.objects.create(grams=250, label="250g")
        self.product = PackagedProduct.objects.create(
            blend=blend,
            pack_size=size,
            form="ground",
        )

    def make_release(self, *, request_item=None):
        return PackRelease.objects.create(
            product=self.product,
            request_item=request_item,
            released_to=self.account,
            packs_out=5,
            selling_price=Decimal("10000.00"),
            purpose="sale",
        )

    def test_deprecated_settlement_amount_is_ignored_by_release_financials(self):
        release = self.make_release()
        PackSettlement.objects.create(
            release=release,
            packs_sold=0,
            amount_paid=Decimal("50000.00"),
            cleared_by=self.cashier,
        )
        PaymentReceipt.objects.create(
            release=release,
            amount=Decimal("12000.00"),
            payment_method="cash",
            collected_by=self.cashier,
        )

        self.assertEqual(release.total_amount_paid, Decimal("12000.00"))
        self.assertEqual(release.outstanding_balance, Decimal("38000.00"))

    def test_finance_collections_count_receipts_not_settlement_amounts(self):
        release = self.make_release()
        PackSettlement.objects.create(
            release=release,
            packs_sold=0,
            amount_paid=Decimal("50000.00"),
            cleared_by=self.cashier,
        )
        PaymentReceipt.objects.create(
            release=release,
            amount=Decimal("12000.00"),
            payment_method="cash",
            collected_by=self.cashier,
        )

        report = get_finance_report(
            ReportPeriod("overall", None, None, None, None, "Overall")
        )

        self.assertEqual(report["collections_total"], Decimal("12000.00"))
        self.assertEqual(report["payment_count"], 1)

    def test_payment_compatibility_service_writes_only_a_receipt(self):
        release = self.make_release()

        receipt = record_payment(
            release=release,
            amount=Decimal("12000.00"),
            method="cash",
            collected_by=self.cashier,
        )

        self.assertIsInstance(receipt, PaymentReceipt)
        self.assertEqual(receipt.payment_method, "cash")
        self.assertEqual(release.settlements.count(), 0)
        self.assertEqual(release.payments.count(), 1)

    def test_sale_quantity_stays_on_settlement_and_cash_goes_to_receipt(self):
        stock_request = StockRequest.objects.create(
            requested_by=self.cashier,
            purpose="sale",
            account_holder=self.account,
        )
        request_item = StockRequestItem.objects.create(
            request=stock_request,
            product=self.product,
            quantity_requested=5,
        )
        release = self.make_release(request_item=request_item)

        settlement = settle_release(
            release=release,
            packs_sold=2,
            amount_paid=Decimal("20000.00"),
            payment_method="cash",
            cashier=self.cashier,
        )

        self.assertEqual(settlement.packs_sold, 2)
        self.assertEqual(settlement.amount_paid, Decimal("0.00"))
        self.assertEqual(release.total_amount_paid, Decimal("20000.00"))
        self.assertEqual(release.payments.count(), 1)
