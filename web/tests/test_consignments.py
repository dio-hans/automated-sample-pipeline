"""Core stock-arithmetic tests. Run via python manage.py test web.tests.test_consignments."""
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from web.models import (
    Blend,
    BranchStockLedger,
    Company,
    CompanyBranch,
    ConsignmentInventory,
    PackReturn,
    PackRelease,
    PackSize,
    PackagedProduct,
    User,
)
from web.sales_workflow import approve_existing_pack_return as approve_pack_return
from web.services.consignments import calculate_variance, record_good_branch_return


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


class BranchReturnApprovalTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username='consignment-manager',
            password='test-password',
            role=User.Role.MANAGER,
        )
        company = Company.objects.create(
            name='Branch Return Test Company',
            country='Uganda',
            city='Kampala',
            address='Kampala',
        )
        self.branch = CompanyBranch.objects.create(
            company=company,
            branch_name='Return Test Branch',
        )
        blend = Blend.objects.create(name='Branch Return Test Blend')
        size = PackSize.objects.create(grams=250, label='Branch Return Test 250g')
        self.product = PackagedProduct.objects.create(
            blend=blend,
            pack_size=size,
            form='ground',
        )
        self.release = PackRelease.objects.create(
            product=self.product,
            branch=self.branch,
            company=company,
            purpose='display',
            packs_out=5,
        )
        self.inventory = ConsignmentInventory.objects.create(
            branch=self.branch,
            product=self.product,
            current_shelf_quantity=2,
            current_backroom_quantity=3,
        )
        for location, quantity in (('shelf', 2), ('backroom', 3)):
            BranchStockLedger.objects.create(
                branch=self.branch,
                product=self.product,
                stock_location=location,
                transaction_type='delivery',
                quantity=quantity,
                release=self.release,
                created_by=self.manager,
            )

    def test_pending_branch_return_approval_decrements_location_and_ledger(self):
        record_good_branch_return(
            branch=self.branch,
            release=self.release,
            location='shelf',
            quantity=1,
            manager=self.manager,
        )
        pending = PackReturn.objects.get(release=self.release)

        approved = approve_pack_return(return_item=pending, user=self.manager)

        self.assertEqual(approved.status, 'approved')
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.current_shelf_quantity, 1)
        self.assertEqual(self.inventory.current_backroom_quantity, 3)
        ledger_entry = BranchStockLedger.objects.get(
            release=self.release,
            transaction_type='return',
        )
        self.assertEqual(ledger_entry.quantity, -1)
        self.assertEqual(ledger_entry.stock_location, 'shelf')

    def test_branch_return_submission_rejects_invalid_location(self):
        with self.assertRaises(ValidationError):
            record_good_branch_return(
                branch=self.branch,
                release=self.release,
                location='warehouse',
                quantity=1,
                manager=self.manager,
            )
