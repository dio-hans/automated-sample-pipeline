from django.urls import path
from .views import *
from . import views
urlpatterns = [
    # company urls here
    path('companies/', CompanyListView.as_view(), name='company_list'),
    path('companies/<int:pk>/', CompanyDetailView.as_view(), name='company_detail'),
    path('companies/create/', CompanyCreateView.as_view(), name='company_create'),

    path('companies/<int:pk>/update/', CompanyUpdateView.as_view(), name='company_update'),
    path('companies/<int:pk>/delete/', CompanyDeleteView.as_view(), name='company_delete'),
    # coffee stock urls here
    path('stocks/', CoffeeStockListViews.as_view(), name='stock_list'),
    path('stocks/<int:pk>/', CoffeeStockDetailView.as_view(), name='stock_detail'),
    path('stocks/create/', CoffeeStockCreateView.as_view(), name='stock_create'),
    path('stocks/<int:pk>/update/', CoffeeStockUpdateView.as_view(), name='stock_update'),
    # sample urls here
    path('samples/', SampleListView.as_view(), name='sample_list'),
    path('samples/<uuid:pk>/', SampleDetailView.as_view(), name='sample_detail'),
    path('samples/create/', SampleCreateView.as_view(), name='sample_create'),
    path('samples/<uuid:pk>/update/', SampleUpdateView.as_view(), name='sample_update'),
    path('samples/<uuid:pk>/delete/', SampleDeleteView.as_view(), name='sample_delete'),

    # api endpoint
    path('api/get-variety-details/', views.get_variety_details, name='get_variety_details'),


]