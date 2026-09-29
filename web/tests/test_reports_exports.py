from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from django.test import SimpleTestCase
from web.services.reporting.periods import ReportPeriod
from web.services.reporting.export import pdf_bytes

class ExecutivePDFTests(SimpleTestCase):
    def test_pdf_contains_valid_pdf_header(self):
        p = ReportPeriod('today',date(2026,9,29),date(2026,9,29),None,None,'Today')
        d = Decimal('0')
        report = {
            'executive_finance': {'collections_total':d,'expense_total':d,'outstanding_receivables':d,
                'payment_methods':[], 'expense_ranking':[], 'ageing':[], 'debtors':[]},
            'executive_operations': {'processing':{'yield_pct':d},'packaging':{'packs_produced':0},
                'open_processing_count':0,'open_packaging_count':0,'exception_count':0,'process_rows':[]},
            'executive_consumption': {'total_consumed_kg':d,'breakdown':[]},
            'sales_comparison':{'current':None,'previous':None,'percent':None},
            'collections_comparison':{'current':d,'previous':None,'percent':None},
            'expenses_comparison':{'current':d,'previous':None,'percent':None},
            'consumption_comparison':{'current':d,'previous':None,'percent':None},
            'executive_alerts':[],
        }
        output = pdf_bytes(p,report)
        self.assertTrue(output.startswith(b'%PDF-'))
        self.assertGreater(len(output),1000)
