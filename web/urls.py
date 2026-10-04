from . import consignment_views
from .views import ManagementReportsView
from django.urls import path
from . import sales_views, views, sales_workflow
from django.urls import path

urlpatterns = [
# companies
path("companies/", views.CompanyListView.as_view(), name="company_list"),
path("companies/<int:pk>/", views.CompanyDetailView.as_view(), name="company_detail"),
path("companies/create/", views.CompanyCreateView.as_view(), name="company_create"),
path("companies/<int:pk>/update/", views.CompanyUpdateView.as_view(), name="company_update"),
path("companies/<int:pk>/delete/", views.CompanyDeleteView.as_view(), name="company_delete"),

# coffee stock
path("stocks/", views.CoffeeStockListViews.as_view(), name="stock_list"),
path("stocks/<int:pk>/", views.CoffeeStockDetailView.as_view(), name="stock_detail"),
path("stocks/create/", views.CoffeeStockCreateView.as_view(), name="stock_create"),
path("stocks/<int:pk>/update/", views.CoffeeStockUpdateView.as_view(), name="stock_update"),


# samples
path("samples/", views.SampleListView.as_view(), name="sample_list"),
path("samples/<uuid:pk>/", views.SampleDetailView.as_view(), name="sample_detail"),
path("samples/create/", views.SampleCreateView.as_view(), name="sample_create"),
path("samples/<uuid:pk>/update/", views.SampleUpdateView.as_view(), name="sample_update"),
path("samples/<uuid:pk>/delete/", views.SampleDeleteView.as_view(), name="sample_delete"),

# follow-ups
path("followups/", views.FollowupListView.as_view(), name="followup_list"),
path("followups/<int:pk>/", views.FollowupDetailView.as_view(), name="followup_detail"),
path("followups/<int:pk>/mark-guide/", views.MarkGuideSentView.as_view(), name="followup_mark_guide"),
path("followups/<int:pk>/mark-contract/", views.MarkContractSentView.as_view(), name="followup_mark_contract"),
path("followups/<int:pk>/convert/", views.ConvertToContractView.as_view(), name="followup_convert"),

# contracts
path("contracts/", views.ContractListView.as_view(), name="contract_list"),
path("contracts/<uuid:pk>/", views.ContractDetailView.as_view(), name="contract_detail"),

# api
path("api/get-variety-details/", views.get_variety_details, name="get_variety_details"),
path("api/stocks/<int:pk>/inventory/", views.stock_stage_inventory_api, name="stock_inventory_api"),
path("inventory/ledger/",views.StockMovementListView.as_view(),name="stock_ledger",),
path("dashboard/", views.dashboard_router, name="dashboard"),
    # low stock 
path("stock/low/", views.low_stock_list, name="stock_low"),

#login
path('', views.user_login, name='login'),
path('register/', views.register_user, name='register'),
path("users/<int:user_id>/toggle-status/",views.toggle_user_status,name="toggle_user_status",),# more inventory urls
# --- Auth ---
path('logout/', views.user_logout, name='logout'),

# --- Processing ---
# --- Packaged Inventory & Products ---
path('packaged-inventory/', views.PackagedInventoryListView.as_view(), name='packaged_inventory_list'),
path('packaged-product/add/', views.PackagedProductCreateView.as_view(), name='packaged_product_form'),
path('packaged-product/<int:pk>/', views.PackagedProductDetailView.as_view(), name='packaged_product_detail'),

# --- Packaging Runs ---
path('packaging-runs/', views.PackagingRunListView.as_view(), name='packaging_run_list'),
path('packaging-runs/add/', views.PackagingRunCreateView.as_view(), name='packaging_run_create'),
path('packaging-runs/<int:pk>/', views.PackagingRunDetailView.as_view(), name='packaging_run_detail'),

# --- Pack Releases ---
path('pack-releases/', views.PackReleaseListView.as_view(), name='pack_release_list'),
path('pack-releases/add/', views.PackReleaseCreateView.as_view(), name='pack_release_create'),
path('pack-releases/<int:pk>/', views.PackReleaseDetailView.as_view(), name='pack_release_detail'),

# --- Pack Returns ---
# 1. The Form Page to Record/Add a New Pack Return Transaction
path("returns/<int:pk>/add/", views.PackReturnCreateView.as_view(), name="pack_return_form"),

# 2. The History Ledger Page Listing All Logged Coffee Pack Returns
path("returns/list/", views.PackReturnListView.as_view(), name="pack_return_list"),



# --- Sales / store stock workflow ---
path("sales/", sales_views.SalesWorkspaceView.as_view(), name="record_sale"),
path("stock-requests/", sales_views.MyStockRequestListView.as_view(), name="stock_request_list"),
path("stock-requests/create/", sales_views.StockRequestCreateView.as_view(), name="stock_request_create"),
path("stock-requests/<int:pk>/", sales_views.StockRequestDetailView.as_view(), name="stock_request_detail"),
path("stock-requests/<int:pk>/fulfil/", sales_views.StockRequestFulfillView.as_view(), name="stock_request_fulfill"),
path("sales/stock/", sales_views.SalesStockView.as_view(), name="my_stock"),
path("stock-requests/<int:pk>/cancel/",views.CancelStockRequestView.as_view(),name="stock_request_cancel"),


# --- Cashier ---
path("cashier/queue/", sales_views.CashierQueueView.as_view(), name="order_queue"),
path("cashier/releases/<int:pk>/clear/", sales_views.CashierClearanceView.as_view(), name="cashier_clearance"),

# --- Role-specific dashboards ---
path("admin-dashboard/", sales_views.AdminDashboardView.as_view(), name="admin_dashboard"),
path("inventory-dashboard/", sales_views.InventoryDashboardView.as_view(), name="inventory_dashboard"),

# --- Return from a specific physical release ---
path("pack-releases/<int:release_pk>/return/", sales_views.PackReturnFromReleaseView.as_view(), name="pack_return_create"),

#PROCESSING
path("processing/issue/<int:pk>/", views.IssueProcessingRunView.as_view(), name="issue_processing"),
path("processing/complete/<int:pk>/", views.CompleteProcessingRunView.as_view(), name="complete_processing"),
path("releases/<int:pk>/collect-payment/",views.RecordInstallmentPaymentView.as_view(),name="record_installment"),
path("cash-book/", views.CashLedgerListView.as_view(), name="cash_ledger_list"),
path("processing-workspace/", views.ProcessingWorkspaceView.as_view(), name="processing_workspace"),
path("accounting/credit-ledger/",views.CreditControlLedgerView.as_view(),name="credit_control_ledger"),

    # new urls for the new features/ the new cards for sales and the reports

path("stock-requests/<int:pk>/add-item/",views.add_item_to_request_view,name="stock_request_add_item",),

#account holder urls
path('accounts/', views.account_holder_list, name='account_holder_list'),
path('accounts/create/', views.account_holder_create, name='account_holder_create'),
path('accounts/<int:pk>/', views.account_holder_detail, name='account_holder_detail'),


# urls.py additions
path("returns/order/<int:pk>/", views.PackReturnCreateView.as_view(), name="pack_return_form"),
path("returns/confirm/<str:token>/", views.PackReturnConfirmationView.as_view(), name="pack_return_confirm"),
path("returns/pending/", views.PendingReturnApprovalListView.as_view(), name="pending_return_approvals"),
path("returns/<int:pk>/approve/", views.PackReturnApproveView.as_view(), name="pack_return_approve"),
    #  expense tracker
path('expenses/', views.ExpenseTrackerView.as_view(), name='expense_tracker'),
path("expenses/",views.ExpenseTrackerView.as_view(),name="expense_tracker",),

path("expenses/history/",views.ExpenseListView.as_view(),name="expense_list",),

### INTERNAL ACCOUNTS
path("internal/accounts/", views.InternalAccountListView.as_view(), name="internal_account_list"),


# Issues
path("internal-accounts/",views.InternalAccountListView.as_view(),name="internal_account_list",),

path("internal-accounts/create/",views.InternalAccountCreateView.as_view(),name="internal_account_create",),

path("internal-stock/",views.InternalStockIssueListView.as_view(),name="internal_stock_issue_list",),

path("internal-stock/create/",views.InternalStockIssueCreateView.as_view(),name="internal_stock_issue_create",),

path("internal-stock/<int:pk>/",views.InternalStockIssueDetailView.as_view(),name="internal_stock_issue_detail",),
path("internal-accounts/<int:pk>/",views.InternalAccountDetailView.as_view(),name="internal_account_detail",),

# reports
# ============================================================
# REPORTING CENTRE
# ============================================================

path("reports/",views.ReportOverviewView.as_view(),name="reports_overview",),

path("reports/stock/",views.StockConsumptionReportView.as_view(),name="reports_stock",),
path("reports/sales/",views.SalesReportView.as_view(),name="reports_sales",),
path('reports/finance/', views.FinanceReportView.as_view(), name='reports_finance'),
path('reports/operations/', views.OperationsReportView.as_view(), name='reports_operations'),
path("reports/executive/", views.ExecutiveReportView.as_view(), name="reports_executive"),
path('reports/profitability/', views.ProfitabilityPlaceholderView.as_view(), name='reports_profitability'),
path('reports/executive/print/', views.ExecutivePrintView.as_view(), name='reports_executive_print'),
path('reports/executive/pdf/', views.ExecutivePDFView.as_view(), name='reports_executive_pdf'),
# ============================================================
# REPORTS
# ============================================================

path(
    "reports/",
    views.ReportOverviewView.as_view(),
    name="reports_overview",
),

path(
    "reports/stock/",
    views.StockConsumptionReportView.as_view(),
    name="reports_stock",
),

path(
    "reports/sales/",
    views.SalesReportView.as_view(),
    name="reports_sales",
),

path(
    "reports/finance/",
    views.FinanceReportView.as_view(),
    name="reports_finance",
),

path(
    "reports/operations/",
    views.OperationsReportView.as_view(),
    name="reports_operations",
),

path(
    "reports/executive/",
    views.ExecutiveReportView.as_view(),
    name="reports_executive",
),

path(
    "reports/profitability/",
    views.ProfitabilityPlaceholderView.as_view(),
    name="reports_profitability",
),

path(
    "reports/executive/print/",
    views.ExecutivePrintView.as_view(),
    name="reports_executive_print",
),

path(
    "reports/executive/pdf/",
    views.ExecutivePDFView.as_view(),
    name="reports_executive_pdf",
),


# ============================================================
# REPORT DRILL-DOWNS
# ============================================================

path(
    "reports/stock/internal-usage/",
    views.InternalUsageReportDetailView.as_view(),
    name="reports_internal_usage",
),

path(
    "reports/stock/production-loss/",
    views.ProductionLossReportDetailView.as_view(),
    name="reports_production_loss",
),

path(
    "reports/sales/details/",
    views.SalesBreakdownReportView.as_view(),
    name="reports_sales_details",
),

path(
    "reports/stock/print/",
    views.StockConsumptionPrintView.as_view(),
    name="reports_stock_print",
),

path(
    "reports/sales/print/",
    views.SalesPrintView.as_view(),
    name="reports_sales_print",
),

path(
    "reports/finance/print/",
    views.FinancePrintView.as_view(),
    name="reports_finance_print",
),

path(
    "reports/operations/print/",
    views.OperationsPrintView.as_view(),
    name="reports_operations_print",
),

path(
    "reports/operations/",
    views.OperationsReportView.as_view(),
    name="reports_operations",
),

path(
    "reports/operations/details/",
    views.OperationsDetailReportView.as_view(),
    name="reports_operations_detail",
),


# CONSIGNMENTS — branch-first workflow
path('sales/consignments/',
     consignment_views.ConsignmentListView.as_view(), name='consignment_list'),
path('sales/consignments/<int:pk>/',
     consignment_views.ConsignmentDetailView.as_view(), name='consignment_detail'),
path('sales/consignments/<int:pk>/branches/new/',
     consignment_views.ConsignmentBranchCreateView.as_view(), name='consignment_branch_create'),
path('sales/consignments/<int:pk>/branches/<int:branch_pk>/',
     consignment_views.ConsignmentBranchDetailView.as_view(), name='consignment_branch_detail'),
path('sales/consignments/<int:pk>/branches/<int:branch_pk>/delivery/',
     consignment_views.ConsignmentDeliveryView.as_view(), name='consignment_delivery'),
path('sales/consignments/<int:pk>/branches/<int:branch_pk>/transfer/',
     consignment_views.ConsignmentShelfTransferView.as_view(), name='consignment_shelf_transfer'),
path('sales/consignments/<int:pk>/branches/<int:branch_pk>/return/',
     consignment_views.ConsignmentBranchReturnView.as_view(), name='consignment_branch_return'),
path('sales/consignments/<int:pk>/branches/<int:branch_pk>/audit/',
     consignment_views.ConsignmentAuditView.as_view(), name='consignment_branch_audit'),
path('sales/consignments/<int:pk>/branches/<int:branch_pk>/audits/<int:audit_pk>/',
     consignment_views.ConsignmentAuditDetailView.as_view(), name='consignment_audit_detail'),
path('sales/consignments/<int:pk>/branches/<int:branch_pk>/audits/<int:audit_pk>/decision/',
     consignment_views.ConsignmentAuditDecisionView.as_view(), name='consignment_audit_decision'),
path('sales/consignments/<int:pk>/branches/<int:branch_pk>/invoices/<int:invoice_pk>/',
     consignment_views.ConsignmentInvoiceView.as_view(), name='consignment_invoice'),
# An older link used company-only audit/<pk>/; route it to branch selection.
path('sales/consignments/<int:pk>/audit/',
     consignment_views.ConsignmentLegacyAuditRedirectView.as_view(), name='consignment_audit'),
path("sales/consignments/new/",consignment_views.ConsigneeCreateView.as_view(),name="consignee_create",),
path(
    "sales/consignments/audits/",
    sales_views.ConsignmentAuditListView.as_view(),
    name="consignment_audit_list",
),

]
