from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from web.models import (
    Blend,
    CoffeeBatchSale,
    CoffeeStock,
    CoffeeVariety,
    PackRelease,
    PackSettlement,
    PackSize,
    PackagedProduct,
    PaymentReceipt,
    StockMovement,
    StockRequest,
    StockStage,
)
from web.sales_workflow import (
    create_stock_request,
    fulfill_bulk_request_item,
    record_bulk_sale_payment,
)
from web.services.inventory import get_stage_inventory


class BulkSalesWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="bulk-workflow-manager",
            password="irrelevant-test-password",
            role="MANAGER",
        )
        variety = CoffeeVariety.objects.create(
            name="Bulk Workflow Arabica",
            default_coffee_type="arabica",
        )
        self.stock = CoffeeStock.objects.create(
            batch_number="BULK-WORKFLOW-001",
            variety=variety,
            coffee_type="arabica",
        )
        StockMovement.objects.create(
            stock=self.stock,
            movement_type="receipt",
            to_stage=StockStage.GREEN,
            quantity=Decimal("20.00"),
            reference="bulk workflow test opening stock",
        )

    def make_request(self, *, stage=StockStage.GREEN, quantity="10.00"):
        return create_stock_request(
            user=self.user,
            purpose="sale",
            destination_type="direct_restaurant",
            bulk_items=[
                (
                    self.stock,
                    stage,
                    Decimal(quantity),
                    Decimal("12500.00"),
                    "Restaurant Walk-in",
                )
            ],
        )

    def test_fulfilment_deducts_the_selected_stage_and_preserves_price_and_buyer(self):
        request = self.make_request()
        line = request.bulk_items.get()

        sale = fulfill_bulk_request_item(
            item=line,
            quantity=Decimal("6.50"),
            price_per_kg=Decimal("11800.00"),
            user=self.user,
        )

        self.assertEqual(sale.stock, self.stock)
        self.assertEqual(sale.stage, StockStage.GREEN)
        self.assertEqual(sale.kg_sold, Decimal("6.50"))
        self.assertEqual(sale.price_per_kg, Decimal("11800.00"))
        self.assertEqual(sale.buyer_name, "Restaurant Walk-in")
        self.assertEqual(sale.total_amount_due, Decimal("76700.00"))
        self.assertEqual(get_stage_inventory(self.stock, StockStage.GREEN), Decimal("13.50"))
        self.assertEqual(request.bulk_items.get().outstanding_quantity, Decimal("3.50"))
        self.assertEqual(StockMovement.objects.filter(reference=f"Bulk sale #{sale.pk}").count(), 1)
        self.assertTrue(PackSettlement.objects.filter(bulk_sale=sale, release__isnull=True).exists())
        request.refresh_from_db()
        self.assertEqual(request.status, "partially_fulfilled")

    def test_payment_receipt_is_linked_to_bulk_sale_and_updates_balance(self):
        request = self.make_request(quantity="5.00")
        sale = fulfill_bulk_request_item(
            item=request.bulk_items.get(),
            quantity=Decimal("5.00"),
            price_per_kg=Decimal("10000.00"),
            user=self.user,
        )

        receipt = record_bulk_sale_payment(
            bulk_sale=sale,
            amount=Decimal("15000.00"),
            method="cash",
            collected_by=self.user,
        )

        self.assertEqual(receipt.bulk_sale, sale)
        self.assertIsNone(receipt.release)
        self.assertEqual(sale.outstanding_balance, Decimal("35000.00"))
        self.assertEqual(PaymentReceipt.objects.filter(bulk_sale=sale).count(), 1)

    def test_over_issue_rolls_back_without_sale_or_stock_movement(self):
        request = self.make_request(quantity="10.00")
        line = request.bulk_items.get()
        before = StockMovement.objects.count()

        with self.assertRaises(ValidationError):
            fulfill_bulk_request_item(
                item=line,
                quantity=Decimal("21.00"),
                price_per_kg=Decimal("10000.00"),
                user=self.user,
            )

        self.assertEqual(CoffeeBatchSale.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(get_stage_inventory(self.stock, StockStage.GREEN), Decimal("20.00"))

    def test_quakers_are_rejected_from_bulk_sale_requests(self):
        with self.assertRaises(ValidationError):
            self.make_request(stage=StockStage.QUAKERS)

    def test_display_requests_cannot_contain_bulk_lines(self):
        with self.assertRaises(ValidationError):
            create_stock_request(
                user=self.user,
                purpose="display",
                destination_type="event_display",
                bulk_items=[
                    (
                        self.stock,
                        StockStage.GREEN,
                        Decimal("1.00"),
                        Decimal("10000.00"),
                        "",
                    )
                ],
            )

    def test_packaged_and_bulk_lines_share_one_stock_request(self):
        blend = Blend.objects.create(name="Mixed Bulk Workflow Blend")
        size = PackSize.objects.create(grams=250, label="Mixed Workflow 250g")
        product = PackagedProduct.objects.create(
            blend=blend,
            pack_size=size,
            form="ground",
        )

        request = create_stock_request(
            user=self.user,
            purpose="sale",
            destination_type="direct_restaurant",
            items=[(product, 20)],
            bulk_items=[
                (
                    self.stock,
                    StockStage.ROASTED,
                    Decimal("15.00"),
                    Decimal("20000.00"),
                    "Cafe Walk-in",
                )
            ],
        )

        self.assertEqual(request.items.get().quantity_requested, 20)
        self.assertEqual(request.bulk_items.get().quantity_requested, Decimal("15.00"))

    def test_my_issued_stock_groups_packaged_and_bulk_fulfilments(self):
        blend = Blend.objects.create(name="Issued Stock Mixed Blend")
        size = PackSize.objects.create(grams=1000, label="1kg")
        product = PackagedProduct.objects.create(
            blend=blend,
            pack_size=size,
            form="beans",
        )
        request = create_stock_request(
            user=self.user,
            purpose="sale",
            destination_type="direct_restaurant",
            items=[(product, 1)],
            bulk_items=[
                (
                    self.stock,
                    StockStage.GREEN,
                    Decimal("5.00"),
                    Decimal("13000.00"),
                    "Mixed Order Cafe",
                )
            ],
        )
        PackRelease.objects.create(
            product=product,
            request_item=request.items.get(),
            packs_out=1,
            selling_price=Decimal("60000.00"),
            purpose="sale",
        )
        fulfill_bulk_request_item(
            item=request.bulk_items.get(),
            quantity=Decimal("5.00"),
            price_per_kg=Decimal("13000.00"),
            user=self.user,
        )
        self.client.force_login(self.user)

        for url_name in ("my_stock", "stock_request_list"):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.context["orders"]), 1)
                self.assertContains(response, "Issued Stock Mixed Blend")
                self.assertContains(response, "5.00 kg")
                self.assertContains(response, "Green Coffee")

    def test_stock_request_form_fulfillment_order_payment_and_history(self):
        self.client.force_login(self.user)
        create_url = reverse("stock_request_create")
        form_response = self.client.get(create_url)
        self.assertEqual(form_response.status_code, 200)
        self.assertNotIn("notes", form_response.context["form"].fields)
        self.assertContains(form_response, "Bulk Stock Request")
        self.assertContains(form_response, "Add Another Item")
        self.assertContains(form_response, "id_bulk-0-stock")
        self.assertContains(form_response, "id_bulk-__prefix__-stock")
        self.assertContains(form_response, "fixed inset-x-0 bottom-0")
        self.assertContains(form_response, "Select Store Products")
        self.assertContains(form_response, "px-2.5 py-1.5")

        response = self.client.post(
            create_url,
            {
                "purpose": "sale",
                "destination_type": "direct_restaurant",
                "company": "",
                "account_holder": "",
                "notes": "",
                "items-TOTAL_FORMS": "0",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "20",
                "bulk-TOTAL_FORMS": "5",
                "bulk-INITIAL_FORMS": "0",
                "bulk-MIN_NUM_FORMS": "0",
                "bulk-MAX_NUM_FORMS": "20",
                "bulk-0-stock": str(self.stock.pk),
                "bulk-0-stage": StockStage.GREEN,
                "bulk-0-quantity_requested": "5.00",
                "bulk-0-price_per_kg": "12500.00",
                "bulk-0-buyer_name": "Restaurant Walk-in",
            },
        )
        self.assertEqual(response.status_code, 302)
        request = StockRequest.objects.get()
        self.assertEqual(request.bulk_items.count(), 1)

        fulfil_url = reverse("stock_request_fulfill", args=[request.pk])
        self.assertEqual(self.client.get(fulfil_url).status_code, 200)
        response = self.client.post(
            fulfil_url,
            {
                "fulfill-TOTAL_FORMS": "0",
                "fulfill-INITIAL_FORMS": "0",
                "bulk_fulfill-TOTAL_FORMS": "1",
                "bulk_fulfill-INITIAL_FORMS": "1",
                "bulk_fulfill-0-item_id": str(request.bulk_items.get().pk),
                "bulk_fulfill-0-issue_quantity": "5.00",
                "bulk_fulfill-0-price_per_kg": "12500.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        sale = CoffeeBatchSale.objects.get()
        self.assertEqual(sale.total_amount_due, Decimal("62500.00"))
        self.assertEqual(request.__class__.objects.get(pk=request.pk).status, "fulfilled")

        response = self.client.post(
            reverse("record_order_payment", args=[request.pk]),
            {"amount": "10000.00", "method": "cash"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PaymentReceipt.objects.get().bulk_sale, sale)
        self.assertEqual(self.client.get(reverse("bulk_sales")).status_code, 200)
