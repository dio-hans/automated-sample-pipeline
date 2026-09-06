from decimal import Decimal

from django.test import TestCase

from .models import (
    Blend,
    CoffeeStock,
    CoffeeVariety,
    PackagedInventory,
    PackagedProduct,
    PackRelease,
    PackReturn,
    PackSize,
    ProcessingRun,
    StockMovement,
    StockRequest,
    StockRequestItem,
    StockStage,
    User,
)
from .sales_workflow import create_stock_request, fulfill_request_item, return_packs
from .services.inventory import get_stage_inventory, record_receipt
from .services.packaging import execute_packaging_run
from .services.processing import issue_for_processing, complete_roasting, complete_grinding


class InventoryFlowTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username="manager", password="x", role=User.Role.MANAGER)
        self.sales = User.objects.create_user(username="sales", password="x", role=User.Role.SALES)
        self.variety = CoffeeVariety.objects.create(name="Bugisu AA", default_coffee_type="arabica")
        self.stock = CoffeeStock.objects.create(
            batch_number="BUGISU-20260904-001",
            variety=self.variety,
            coffee_type="arabica",
            received_date="2026-09-04",
        )
        record_receipt(self.stock, Decimal("100.00"), user=self.manager)
        self.blend = Blend.objects.create(name="Kitiko")
        self.pack_size = PackSize.objects.create(grams=250, label="250g")
        self.product = PackagedProduct.objects.create(
            blend=self.blend,
            pack_size=self.pack_size,
            form="beans",
            selling_price=Decimal("15000.00"),
        )

    def test_receipt_is_green_inventory(self):
        self.stock.refresh_from_db()
        self.assertEqual(get_stage_inventory(self.stock, StockStage.GREEN), Decimal("100.00"))
        self.assertEqual(self.stock.quantity_available, Decimal("100.00"))

    def test_roasting_preserves_ledger_accountability(self):
        result = issue_for_processing(
            stock=self.stock,
            process_type="roasting",
            input_quantity=Decimal("100.00"),
            user=self.manager,
        )
        complete_roasting(
            processing_run=result.processing_run,
            output_quantity=Decimal("92.00"),
            user=self.manager,
        )
        self.assertEqual(self.stock.quantity_green, Decimal("0.00"))
        self.assertEqual(self.stock.quantity_roasted, Decimal("92.00"))
        run = ProcessingRun.objects.get(pk=result.processing_run.pk)
        self.assertTrue(run.is_accounted_for)

    def test_grinding_moves_roasted_to_ground(self):
        issue = issue_for_processing(
            stock=self.stock, process_type="roasting", input_quantity=Decimal("100"), user=self.manager
        )
        complete_roasting(processing_run=issue.processing_run, output_quantity=Decimal("90"), user=self.manager)
        grind = issue_for_processing(
            stock=self.stock, process_type="grinding", input_quantity=Decimal("40"), user=self.manager
        )
        complete_grinding(processing_run=grind.processing_run, output_quantity=Decimal("39"), user=self.manager)
        self.assertEqual(self.stock.quantity_roasted, Decimal("50.00"))
        self.assertEqual(self.stock.quantity_ground, Decimal("39.00"))

    def test_packaging_converts_roasted_kg_to_packs(self):
        issue = issue_for_processing(
            stock=self.stock, process_type="roasting", input_quantity=Decimal("100"), user=self.manager
        )
        complete_roasting(processing_run=issue.processing_run, output_quantity=Decimal("100"), user=self.manager)
        run = execute_packaging_run(
            stock=self.stock,
            product=self.product,
            source_stage=StockStage.ROASTED,
            input_kg=Decimal("10.00"),
            packs_produced=40,
            user=self.manager,
        )
        inventory = PackagedInventory.objects.get(product=self.product)
        self.assertEqual(run.coffee_used_kg, Decimal("10.0000"))
        self.assertEqual(inventory.available, 40)
        self.assertEqual(self.stock.quantity_roasted, Decimal("90.00"))

    def test_request_fulfilment_release_and_return_restore_stock(self):
        issue = issue_for_processing(
            stock=self.stock, process_type="roasting", input_quantity=Decimal("100"), user=self.manager
        )
        complete_roasting(processing_run=issue.processing_run, output_quantity=Decimal("100"), user=self.manager)
        execute_packaging_run(
            stock=self.stock, product=self.product, source_stage=StockStage.ROASTED,
            input_kg=Decimal("10"), packs_produced=40, user=self.manager,
        )
        request = create_stock_request(
            user=self.sales, purpose="sale", items=[(self.product, 12)]
        )
        item = StockRequestItem.objects.get(request=request)
        release = fulfill_request_item(
            item=item, quantity=12, selling_price=self.product.selling_price, manager=self.manager
        )
        inventory = PackagedInventory.objects.get(product=self.product)
        self.assertEqual(inventory.available, 28)
        self.assertEqual(release.packs_outstanding, 12)
        return_packs(release=release, packs_returned=5, user=self.manager, notes="Unsold stock returned")
        inventory.refresh_from_db()
        self.assertEqual(inventory.available, 33)
        self.assertEqual(release.packs_outstanding, 7)
