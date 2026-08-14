from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView
from django.urls import reverse_lazy
from django.db import transaction
from .models import Company, CoffeeStock, Sample
from .forms import CompanyForm, CoffeeStockForm, SampleForm

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
        sample_weight = form.instance.sample_weight

        with transaction.atomic():
            # select_for_update locks this stock row until the transaction
            # commits, so two samples submitted at the same instant can't
            # both read the same quantity_available and both pass the check
            # below. (SQLite ignores the lock silently — this only takes
            # effect once you're on Postgres/MySQL — but it's harmless
            # either way and costs nothing to have in now.)
            stock = CoffeeStock.objects.select_for_update().get(
                pk=form.instance.coffee_stock_id
            )

            if stock.quantity_available < sample_weight:
                form.add_error(
                    'sample_weight',
                    'Not enough coffee stock available.'
                )
                return self.form_invalid(form)

            # Only save the sample once we know the stock actually covers
            # it — this is what stops a rejected sample from leaving an
            # orphan row behind.
            response = super().form_valid(form)

            # Deduct stock
            stock.quantity_available -= sample_weight
            stock.save()

            # Progress the company pipeline stage
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