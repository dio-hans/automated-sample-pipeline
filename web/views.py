from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView
from django.urls import reverse_lazy
from .models import Company, CoffeeStock, Sample
from .forms import CompanyForm, CoffeeStockForm, SampleForm
from django.http import HttpResponseRedirect, response

# company views here
class CompanyListView(ListView):
    model = Company
    template_name = 'pipeline/company_list.html'
    context_object_name = "companies"\

class CompanyDetailView(DetailView):
    model = Company
    template_name = 'pipeline/company_detail.html'
    context_object_name = "company"

class CompanyCreateView(CreateView):
    model = Company
    form_class = CompanyForm
    template_name = 'pipeline/company_form.html'
    success_url = reverse_lazy('company_list')

class CompanyUpdateView(UpdateView):
    model = Company
    form_class = CompanyForm
    template_name = 'pipeline/company_form.html'
    success_url = reverse_lazy('company_list')

class CompanyDeleteView(DeleteView):
    model = Company
    template_name = 'pipeline/company_confirm_delete.html'
    success_url = reverse_lazy('company_list')

# coffee stock views here
class CoffeeStockListViews(ListView):
    model = CoffeeStock
    template_name = 'pipeline/coffee_stock_list.html'
    context_object_name = "stocks"

class CoffeeStockDetailView(DetailView):
    model = CoffeeStock
    template_name = 'pipeline/coffee_stock_detail.html'
    context_object_name = "stock"

class CoffeeStockCreateView(CreateView):
    model = CoffeeStock
    form_class = CoffeeStockForm
    template_name = "pipeline/stock_form.html"
    success_url = reverse_lazy("stock_list")

class CoffeeStockUpdateView(UpdateView):
    model = CoffeeStock
    form_class = CoffeeStockForm
    template_name = "pipeline/stock_form.html"
    success_url = reverse_lazy("stock_list")


# sample views here
class SampleListView(ListView):
    model = Sample
    template_name = 'pipeline/sample_list.html'
    context_object_name = "samples"

class SampleDetailView(DetailView):
    model = Sample
    template_name = 'pipeline/sample_detail.html'
    context_object_name = "sample"  

class SampleCreateView(CreateView):
    model = Sample
    form_class = SampleForm
    template_name = 'pipeline/sample_form.html'
    success_url = reverse_lazy('sample_list')

    def form_valid(self, form):
        stock = self.object.coffee_stock
        stock.quantity_available -= self.object.sample_weight
        stock.save()
        company = self.object.company
        company.pipeline_stage = "sample_sent"

        company.save()

        return response

class SampleUpdateView(UpdateView):
    model = Sample
    form_class = SampleForm
    template_name = "pipeline/sample_form.html"
    success_url = reverse_lazy("sample_list")


class SampleDeleteView(DeleteView):
    model = Sample
    template_name = 'pipeline/sample_confirm_delete.html'
    success_url = reverse_lazy('sample_list')

    