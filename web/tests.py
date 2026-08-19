from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import CoffeeStock, CoffeeVariety
from .services.intake import record_intake


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
