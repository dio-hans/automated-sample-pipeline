"""Core stock-arithmetic tests. Run via python manage.py test web.tests.test_consignments."""
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from web.services.consignments import calculate_variance


class ConsignmentVarianceTests(SimpleTestCase):
    def test_normal_sale_and_location_specific_shrinkage(self):
        result = calculate_variance(50, 30, 37, 23, 2, 1)
        self.assertEqual(result, {
            'sold_shelf': 11, 'sold_backroom': 6,
            'sold': 17, 'shrinkage': 3,
        })

    def test_no_change_is_not_a_sale(self):
        self.assertEqual(calculate_variance(10, 4, 10, 4)['sold'], 0)

    def test_shrinkage_is_not_a_sale(self):
        self.assertEqual(calculate_variance(8, 3, 5, 1, 3, 2)['sold'], 0)

    def test_unrecorded_transfer_must_fail(self):
        with self.assertRaises(ValidationError):
            calculate_variance(10, 20, 12, 18)

    def test_impossible_shrinkage_must_fail(self):
        with self.assertRaises(ValidationError):
            calculate_variance(10, 0, 9, 0, 2, 0)

    def test_negative_and_fractional_counts_fail(self):
        for counts in ((-1, 0, 0, 0), (3, 0, 1.5, 0)):
            with self.subTest(counts=counts), self.assertRaises(ValidationError):
                calculate_variance(*counts)
