from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from django.test import SimpleTestCase
from django.utils import timezone
from web.services.reporting.executive import _comparison, _previous_period, get_executive_report
from web.services.reporting.periods import ReportPeriod

class ExecutiveReportingTests(SimpleTestCase):
    def test_zero_comparison_has_no_misleading_percentage(self):
        self.assertIsNone(_comparison(Decimal('10'), Decimal('0'))['percent'])

    def test_previous_period_same_length(self):
        p = ReportPeriod('custom', date(2026,9,10), date(2026,9,12), None, None, 'Test')
        old = _previous_period(p)
        self.assertEqual((old.start_date, old.end_date), (date(2026,9,7), date(2026,9,9)))

    @patch('web.services.reporting.executive.get_operations_report')
    @patch('web.services.reporting.executive.get_finance_report')
    @patch('web.services.reporting.executive.get_sales_report')
    @patch('web.services.reporting.executive.get_stock_consumption_report')
    def test_composes_without_guessing_sales_key(self, consume, sales, finance, operations):
        consume.return_value = {'total_consumed_kg': Decimal('12')}
        sales.return_value = {'unknown_r3_key': Decimal('300')}
        finance.return_value = {'collections_total':Decimal('200'), 'expense_total':Decimal('50'), 'outstanding_receivables':Decimal('0'), 'unallocated_credit':Decimal('0'), 'bulk_receivables_pending':True, 'collection_days':[]}
        operations.return_value = {'exception_count':0,'open_processing_count':0,'open_packaging_count':0,'trend_labels':[],'trend_processing_inputs':[],'trend_processing_losses':[]}
        p = ReportPeriod('overall', None, None, None, None, 'Overall')
        result = get_executive_report(p)
        self.assertIsNone(result['sales_comparison']['current'])
        self.assertEqual(result['collections_comparison']['current'], Decimal('200'))
