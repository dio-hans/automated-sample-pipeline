from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from django.core.exceptions import ValidationError

from .models import CoffeeStock, CoffeeVariety
from .services.intake import record_intake
from .services.processing import (
    issue_for_processing,
    complete_roasting,
    complete_sorting,
)


def intake_payload(**overrides):
    payload = {
        "variety_name": "Bugisu AA",
        "coffee_type": "arabica",
        "received_date": "2026-08-01",
        "supplier": "Darling Coffee Uganda",
        "source": "Mbale",
        "grade": "AA",
        "moisture_content": "12.5",
        "process": "Washed",
        "season_of_harvest": "2025/26",
        "foreign_smell": "None",
        "foreign_matter": "None",
        "prints": "None",
        "physical_damages": "No",
        "defects": "1.00",
        "quantity_after_sorting": "480.00",
        "checked_by": "Ali",
        "verified_by": "Grace",
        "delivered_by": "Moses",
        "car_number": "UAB 123X",
        "received_by": "Sam",
        "reorder_level": "50.00",
        "quantity_received": "500.00",
    }
    payload.update(overrides)
    return payload


class StockIntakeViewTests(TestCase):

    def post_intake(self, **overrides):
        return self.client.post(
            reverse("stock_create"),
            intake_payload(**overrides),
        )

    def test_new_name_creates_variety_and_batch(self):
        response = self.post_intake()

        self.assertRedirects(response, reverse("stock_list"))

        variety = CoffeeVariety.objects.get()
        stock = CoffeeStock.objects.get()

        self.assertEqual(variety.name, "Bugisu AA")
        self.assertEqual(variety.default_grade, "AA")
        self.assertEqual(stock.variety, variety)
        self.assertTrue(stock.batch_number)
        self.assertEqual(stock.quantity_received, Decimal("500.00"))

    def test_matching_name_restocks_same_delivery(self):
        self.post_intake()
        self.post_intake(quantity_received="120.00")

        self.assertEqual(CoffeeVariety.objects.count(), 1)
        self.assertEqual(CoffeeStock.objects.count(), 1)

        stock = CoffeeStock.objects.get()

        self.assertEqual(stock.quantity_received, Decimal("620.00"))
        self.assertEqual(stock.quantity_available, Decimal("620.00"))

    def test_matching_name_is_case_insensitive(self):
        self.post_intake()
        self.post_intake(variety_name="bugisu aa")

        self.assertEqual(CoffeeVariety.objects.count(), 1)

    def test_matching_name_new_delivery_opens_new_batch(self):
        self.post_intake()
        self.post_intake(received_date="2026-08-09")

        self.assertEqual(CoffeeVariety.objects.count(), 1)
        self.assertEqual(CoffeeStock.objects.count(), 2)

        batch_numbers = set(
            CoffeeStock.objects.values_list("batch_number", flat=True)
        )
        self.assertEqual(len(batch_numbers), 2)

    def test_editing_a_batch_repoints_it_at_the_typed_variety(self):
        self.post_intake()

        stock = CoffeeStock.objects.get()
        payload = intake_payload(variety_name="Rwenzori Robusta")
        payload.pop("quantity_received")

        response = self.client.post(
            reverse("stock_update", args=[stock.pk]),
            payload,
        )

        self.assertRedirects(response, reverse("stock_list"))

        stock.refresh_from_db()

        self.assertEqual(stock.variety.name, "Rwenzori Robusta")
        self.assertEqual(stock.quantity_received, Decimal("500.00"))

    def test_custom_name_creates_second_definition(self):
        self.post_intake()
        self.post_intake(variety_name="Rwenzori Robusta", coffee_type="robusta")

        self.assertEqual(CoffeeVariety.objects.count(), 2)
        self.assertEqual(CoffeeStock.objects.count(), 2)


class IntakeServiceTests(TestCase):

    def batch_data(self, **overrides):
        data = {
            "coffee_type": "arabica",
            "received_date": date(2026, 8, 1),
            "supplier": "Darling Coffee Uganda",
            "source": "Mbale",
            "grade": "AA",
            "process": "Washed",
            "foreign_smell": "None",
            "reorder_level": Decimal("50.00"),
        }
        data.update(overrides)
        return data

    def test_existing_variety_fills_only_missing_defaults(self):
        CoffeeVariety.objects.create(
            name="Bugisu AA",
            default_coffee_type="arabica",
            default_grade="AB",
        )

        record_intake(
            variety_name="Bugisu AA",
            batch_data=self.batch_data(),
            quantity_received=Decimal("100"),
        )

        variety = CoffeeVariety.objects.get()

        self.assertEqual(variety.default_grade, "AB")
        self.assertEqual(variety.default_source, "Mbale")

    def test_result_reports_what_happened(self):
        first = record_intake(
            variety_name="Bugisu AA",
            batch_data=self.batch_data(),
            quantity_received=Decimal("100"),
        )
        second = record_intake(
            variety_name="Bugisu AA",
            batch_data=self.batch_data(),
            quantity_received=Decimal("50"),
        )

        self.assertTrue(first.variety_created)
        self.assertTrue(first.batch_created)
        self.assertFalse(second.variety_created)
        self.assertFalse(second.batch_created)


class VarietyLookupTests(TestCase):

    def test_lookup_by_name_returns_defaults(self):
        CoffeeVariety.objects.create(
            name="Bugisu AA",
            default_coffee_type="arabica",
            default_grade="AA",
        )

        response = self.client.get(
            reverse("get_variety_details"),
            {"name": "bugisu aa"},
        )

        self.assertJSONEqual(
            response.content,
            {
                "success": True,
                "name": "Bugisu AA",
                "coffee_type": "arabica",
                "grade": "AA",
                "source": "",
                "process": "",
                "foreign_smell": "None",
            },
        )

    def test_unknown_name_is_not_found(self):
        response = self.client.get(
            reverse("get_variety_details"),
            {"name": "Unknown"},
        )

        self.assertJSONEqual(response.content, {"success": False})

from django.test import TestCase
from django.urls import reverse

from .models import (
    Blend,
    PackSize,
    PackagedProduct,
    PackagedInventory,
    StockMovement,
    ProcessingRun,
    User,
)
from .services.packaging import execute_packaging_run
from .sales_workflow import create_stock_request, fulfill_request_item, return_packs


class InventoryAccessAndWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin",
            password="StrongPass123!",
            role=User.Role.ADMIN,
            email="admin@nonda.test",
        )
        self.manager = User.objects.create_user(
            username="manager",
            password="StrongPass123!",
            role=User.Role.MANAGER,
            email="manager@nonda.test",
        )
        self.sales = User.objects.create_user(
            username="sales",
            password="StrongPass123!",
            role=User.Role.SALES,
            email="sales@nonda.test",
        )

    def make_stock(self, quantity="500.00"):
        result = record_intake(
            variety_name="Bugisu AA",
            batch_data={
                "coffee_type": "arabica",
                "received_date": date(2026, 9, 2),
                "supplier": "Nonda Supplier",
                "source": "Mbale",
                "grade": "AA",
                "process": "Washed",
                "foreign_smell": "None",
                "reorder_level": Decimal("50.00"),
            },
            quantity_received=Decimal(quantity),
            user=self.manager,
        )
        return result.stock

    def make_product(self, grams=500, form="beans"):
        size = PackSize.objects.create(
            grams=grams,
            label=f"{grams}g",
        )
        blend = Blend.objects.create(name="Kitiko & Mulondo")
        return PackagedProduct.objects.create(
            blend=blend,
            pack_size=size,
            form=form,
        )

    def test_anonymous_user_is_redirected_from_inventory(self):
        response = self.client.get(reverse("stock_list"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('stock_list')}",
        )

    def test_sales_user_cannot_access_inventory(self):
        self.client.force_login(self.sales)
        response = self.client.get(reverse("stock_list"))
        self.assertRedirects(response, reverse("record_sale"))

    def test_manager_can_access_inventory(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("stock_list"))
        self.assertEqual(response.status_code, 200)

    def test_sales_user_cannot_create_stock(self):
        self.client.force_login(self.sales)
        response = self.client.get(reverse("stock_create"))
        self.assertRedirects(response, reverse("record_sale"))

    def test_manager_can_start_and_complete_roasting(self):
        self.client.force_login(self.manager)
        stock = self.make_stock()

        response = self.client.post(
            reverse("process_stock", args=[stock.pk]),
            {
                "process_type": "roasting",
                "input_quantity": "400.00",
                "notes": "Morning roast",
            },
        )
        self.assertRedirects(response, reverse("stock_detail", args=[stock.pk]))

        stock.refresh_from_db()
        run = ProcessingRun.objects.get(stock=stock)
        self.assertEqual(run.status, "open")
        self.assertEqual(stock.quantity_green, Decimal("100.00"))

        response = self.client.post(
            reverse("processing_complete", args=[run.pk]),
            {
                "output_quantity": "360.00",
                "quaker_quantity": "0",
                "notes": "Roast returned",
            },
        )
        self.assertRedirects(response, reverse("stock_detail", args=[stock.pk]))

        run.refresh_from_db()
        stock.refresh_from_db()
        self.assertEqual(run.status, "completed")
        self.assertEqual(stock.quantity_green, Decimal("100.00"))
        self.assertEqual(stock.quantity_roasted, Decimal("360.00"))
        self.assertEqual(run.loss_quantity, Decimal("40.00"))

        movements = list(
            StockMovement.objects.filter(stock=stock)
            .values_list("movement_type", "quantity")
        )
        self.assertIn(("roast_issue", Decimal("400.00")), movements)
        self.assertIn(("roast_return", Decimal("360.00")), movements)
        self.assertIn(("loss", Decimal("40.00")), movements)

    def test_sorting_preserves_quakers_as_inventory(self):
        self.client.force_login(self.manager)
        stock = self.make_stock()

        run = issue_for_processing(
            stock=stock,
            process_type="sorting",
            input_quantity=Decimal("100"),
            user=self.manager,
        ).processing_run

        complete_sorting(
            processing_run=run,
            good_quantity=Decimal("92"),
            quaker_quantity=Decimal("5"),
            user=self.manager,
        )

        stock.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(stock.quantity_roasted, Decimal("92"))
        self.assertEqual(stock.quantity_quakers, Decimal("5"))
        self.assertEqual(run.loss_quantity, Decimal("3"))
        self.assertTrue(run.is_accounted_for)

    def test_packaging_creates_packaged_inventory_from_roasted_coffee(self):
        stock = self.make_stock()
        issue = issue_for_processing(
            stock=stock,
            process_type="roasting",
            input_quantity=Decimal("100"),
            user=self.manager,
        ).processing_run
        complete_roasting(
            processing_run=issue,
            output_quantity=Decimal("100"),
            user=self.manager,
        )

        product = self.make_product(500, "beans")
        run = execute_packaging_run(
            stock=stock,
            product=product,
            source_stage="roasted",
            input_kg=Decimal("50"),
            packs_produced=100,
            user=self.manager,
        )

        inventory = PackagedInventory.objects.get(product=product)
        self.assertEqual(run.packs_produced, 100)
        self.assertEqual(run.coffee_used_kg, Decimal("50.00"))
        self.assertEqual(run.loss_kg, Decimal("0.00"))
        self.assertEqual(inventory.available, 100)
        self.assertEqual(stock.quantity_roasted, Decimal("50.00"))

    def test_packaging_rejects_wrong_source_stage(self):
        stock = self.make_stock()
        product = self.make_product(500, "beans")

        with self.assertRaises(ValidationError):
            execute_packaging_run(
                stock=stock,
                product=product,
                source_stage="ground",
                input_kg=Decimal("1"),
                packs_produced=2,
                user=self.manager,
            )

    def test_sales_request_and_return_restore_packaged_stock(self):
        stock = self.make_stock()
        issue = issue_for_processing(
            stock=stock,
            process_type="roasting",
            input_quantity=Decimal("100"),
            user=self.manager,
        ).processing_run
        complete_roasting(
            processing_run=issue,
            output_quantity=Decimal("100"),
            user=self.manager,
        )
        product = self.make_product(500, "beans")
        execute_packaging_run(
            stock=stock,
            product=product,
            source_stage="roasted",
            input_kg=Decimal("50"),
            packs_produced=100,
            user=self.manager,
        )

        request = create_stock_request(
            user=self.sales,
            purpose="sale",
            items=[(product, 20)],
        )
        item = request.items.get()

        release = fulfill_request_item(
            item=item,
            quantity=20,
            selling_price=Decimal("15000"),
            manager=self.manager,
        )
        self.assertEqual(PackagedInventory.objects.get(product=product).available, 80)

        return_packs(
            release=release,
            packs_returned=5,
            reason="Unsold",
            user=self.manager,
        )
        self.assertEqual(PackagedInventory.objects.get(product=product).available, 85)


class AuthenticationAndAuthorizationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin",
            password="StrongPass123!",
            role=User.Role.ADMIN,
            email="admin2@nonda.test",
        )
        self.manager = User.objects.create_user(
            username="manager",
            password="StrongPass123!",
            role=User.Role.MANAGER,
            email="manager2@nonda.test",
        )

    def test_login_routes_manager_to_inventory(self):
        response = self.client.post(
            reverse("login"),
            {"username": "manager", "password": "StrongPass123!"},
        )
        self.assertRedirects(response, reverse("inventory_dashboard"))

    def test_suspended_user_cannot_login(self):
        self.manager.is_active = False
        self.manager.save(update_fields=["is_active"])

        response = self.client.post(
            reverse("login"),
            {"username": "manager", "password": "StrongPass123!"},
        )
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_only_admin_can_manage_users(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("register"))
        self.assertRedirects(response, reverse("inventory_dashboard"))

        self.client.force_login(self.admin)
        response = self.client.get(reverse("register"))
        self.assertEqual(response.status_code, 200)

    def test_admin_can_create_and_suspend_user(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("register"),
            {
                "username": "newmanager",
                "email": "newmanager@nonda.test",
                "contact": "+256700000099",
                "employee_id": "EMP-099",
                "role": "MANAGER",
                "password1": "AnotherStrongPass123!",
                "password2": "AnotherStrongPass123!",
            },
        )
        self.assertRedirects(response, reverse("register"))

        employee = User.objects.get(username="newmanager")
        self.assertTrue(employee.is_active)

        response = self.client.post(
            reverse("toggle_user_status", args=[employee.pk]),
        )
        self.assertRedirects(response, reverse("register"))

        employee.refresh_from_db()
        self.assertFalse(employee.is_active)

    def test_any_authenticated_user_can_logout(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_last_active_admin_cannot_be_suspended(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("toggle_user_status", args=[self.admin.pk]),
        )
        self.assertRedirects(response, reverse("register"))

        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

# Recommended tests

# 1. 3 issued, 0 sold -> packs_returnable == 3
# 2. 3 issued, 2 sold -> packs_returnable == 1
# 3. 3 issued, 3 sold -> packs_returnable == 0
# 4. 3 issued, 2 sold, 1 pending return -> packs_returnable == 0
# 5. pending return does not increase inventory or reduce balance
# 6. approved return increases returned quantity exactly once
# 7. approving the same return twice is rejected
# 8. rejected return makes no stock or ledger change
# 9. two pending returns cannot reserve more units than remain unsold

