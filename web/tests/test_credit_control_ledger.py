from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from web.models import (
    AccountHolder,
    Blend,
    BulkStockRequestItem,
    CoffeeBatchSale,
    CoffeeStock,
    CoffeeVariety,
    PackRelease,
    PackSize,
    PackagedProduct,
    StockRequest,
    StockStage,
)


class CreditControlLedgerTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.cashier = user_model.objects.create_user(
            username="unified-ledger-cashier",
            password="test-password",
            role="CASHIER",
        )
        self.client.force_login(self.cashier)
        self.account = AccountHolder.objects.create(
            name="Unified Ledger Customer",
            account_type="customer",
        )
        blend = Blend.objects.create(name="Unified Ledger Blend")
        pack_size = PackSize.objects.create(grams=1000, label="1kg")
        product = PackagedProduct.objects.create(
            blend=blend,
            pack_size=pack_size,
            form="ground",
        )
        self.release = PackRelease.objects.create(
            product=product,
            released_to=self.account,
            packs_out=2,
            selling_price=Decimal("60000.00"),
            purpose="sale",
        )

        variety = CoffeeVariety.objects.create(
            name="Unified Ledger Arabica",
            default_coffee_type="arabica",
        )
        stock = CoffeeStock.objects.create(
            batch_number="UNIFIED-LEDGER-001",
            variety=variety,
            coffee_type="arabica",
        )
        request = StockRequest.objects.create(
            requested_by=self.cashier,
            purpose="sale",
            account_holder=self.account,
        )
        bulk_item = BulkStockRequestItem.objects.create(
            request=request,
            stock=stock,
            stage=StockStage.GREEN,
            quantity_requested=Decimal("5.00"),
            price_per_kg=Decimal("13000.00"),
        )
        self.bulk_sale = CoffeeBatchSale.objects.create(
            bulk_request_item=bulk_item,
            stock=stock,
            stage=StockStage.GREEN,
            kg_sold=Decimal("5.00"),
            price_per_kg=Decimal("13000.00"),
            account_holder=self.account,
        )

        now = timezone.now()
        PackRelease.objects.filter(pk=self.release.pk).update(
            released_at=now - timedelta(minutes=10),
        )
        CoffeeBatchSale.objects.filter(pk=self.bulk_sale.pk).update(
            sold_at=now,
        )

    def test_packaged_and_bulk_sales_share_one_date_sorted_ledger(self):
        response = self.client.get(reverse("credit_control_ledger"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["kind"] for row in response.context["ledger_rows"]],
            ["bulk", "packaged"],
        )
        self.assertEqual(response.content.decode().count("<table"), 1)
        self.assertContains(response, "UNIFIED-LEDGER-001")
        self.assertContains(response, "Unified Ledger Blend")
        self.assertContains(response, "Green Coffee")
        self.assertContains(response, reverse("bulk_sale_payment", args=[self.bulk_sale.pk]))
        self.assertContains(response, reverse("record_installment", args=[self.release.pk]))
