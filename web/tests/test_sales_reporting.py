from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from web.models import (
    AccountHolder,
    Blend,
    Company,
    CompanyBranch,
    ConsignmentAuditAllocation,
    PackRelease,
    PackSettlement,
    PackSize,
    PackagedProduct,
    StockAudit,
    StockAuditItem,
)
from web.services.reporting.periods import ReportPeriod
from web.services.reporting.finance import get_finance_report
from web.services.reporting.sales import get_sales_report


class SalesReportingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="sales-reporting-user",
            password="test-password",
        )
        self.holder = AccountHolder.objects.create(
            name="Sales Reporting Agent",
            account_type="field_agent",
        )
        blend = Blend.objects.create(name="Sales Reporting Blend")
        size = PackSize.objects.create(grams=250, label="250g")
        self.product = PackagedProduct.objects.create(
            blend=blend,
            pack_size=size,
            form="ground",
        )
        now = timezone.localtime()
        self.today = now.date()
        self.period = ReportPeriod(
            "today",
            self.today,
            self.today,
            timezone.make_aware(datetime.combine(self.today, time.min)),
            timezone.make_aware(
                datetime.combine(self.today + timedelta(days=1), time.min)
            ),
            "Today",
        )

    def test_approved_supermarket_audit_counts_but_unconfirmed_issue_does_not(self):
        company = Company.objects.create(
            name="Sales Reporting Supermarket",
            country="Uganda",
            city="Kampala",
            contact_person="Store Manager",
            email="sales-reporting@example.com",
            phone_number="+256700000001",
            address="Kampala",
        )
        branch = CompanyBranch.objects.create(
            company=company,
            branch_name="Main Branch",
        )
        release = PackRelease.objects.create(
            product=self.product,
            released_to=company.account_holder,
            company=company,
            branch=branch,
            packs_out=5,
            billable_packs=2,
            selling_price=Decimal("20000.00"),
            purpose="display",
        )
        audit = StockAudit.objects.create(
            company=company,
            branch=branch,
            audited_by=self.user,
        )
        audit_item = StockAuditItem.objects.create(
            audit=audit,
            product=self.product,
            expected_shelf=5,
            actual_shelf=3,
            selling_price=Decimal("20000.00"),
        )
        audit.status = "approved"
        audit.approved_at = timezone.now()
        audit.save(update_fields=["status", "approved_at"])
        ConsignmentAuditAllocation.objects.create(
            audit_item=audit_item,
            release=release,
            quantity=2,
            unit_price=Decimal("20000.00"),
        )

        agent_issue = PackRelease.objects.create(
            product=self.product,
            released_to=self.holder,
            packs_out=2,
            selling_price=Decimal("40000.00"),
            purpose="sale",
        )

        report = get_sales_report(self.period)

        self.assertEqual(report["packaged_packs_sold"], 2)
        self.assertEqual(report["packaged_sales_value"], Decimal("40000.00"))
        self.assertEqual(report["packaged_kg_sold"], Decimal("0.50"))
        self.assertFalse(agent_issue.settlements.exists())

        finance_report = get_finance_report(self.period)
        self.assertEqual(
            finance_report["outstanding_receivables"],
            Decimal("40000.00"),
        )

    def test_cashier_confirmed_sale_remains_in_sales_report(self):
        release = PackRelease.objects.create(
            product=self.product,
            released_to=self.holder,
            packs_out=3,
            selling_price=Decimal("10000.00"),
            purpose="sale",
        )
        PackSettlement.objects.create(
            release=release,
            packs_sold=1,
            cleared_by=self.user,
        )

        report = get_sales_report(self.period)

        self.assertEqual(report["packaged_packs_sold"], 1)
        self.assertEqual(report["packaged_sales_value"], Decimal("10000.00"))
