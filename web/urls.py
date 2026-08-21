from django.urls import path
from . import views

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
    path("stocks/<int:pk>/process/", views.ProcessStockView.as_view(), name="stock_process"),

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
    path("",views.DashboardView.as_view(),name="dashboard",),
     # low stock 
    path("stock/low/", views.low_stock_list, name="stock_low"),

]