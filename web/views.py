from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView
from django.urls import reverse_lazy
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect

from .models import Company, CoffeeStock, Sample, StockMovement, CoffeeVariety
from .forms import (
    CompanyForm,
    CoffeeStockForm,
    CoffeeStockIntakeForm,
    SampleForm,
)
from .services.intake import record_intake
from .services.inventory import get_stage_inventory


def current_user(request):
    """The acting user, or None while the app runs without authentication."""

    return request.user if request.user.is_authenticated else None


# company views here
class CompanyListView(ListView):
    model = Company
    template_name = 'pipeline/company_list.html'
    context_object_name = "companies"

class CompanyDetailView(DetailView):
    model = Company
    template_name = 'pipeline/company_detail.html'
    context_object_name = "company"

class CompanyCreateView(CreateView):
    model = Company
    form_class = CompanyForm
    template_name = 'pipeline/company_form.html'
    success_url = reverse_lazy('company_list')

def get_variety_details(request):
    """
    Master defaults for a coffee variety, looked up by name (what the
    intake form types) or by id.
    """

    varieties = CoffeeVariety.objects.filter(is_active=True)

    variety_id = request.GET.get('id')
    name = (request.GET.get('name') or '').strip()

    if variety_id:
        variety = varieties.filter(pk=variety_id).first()
    elif name:
        variety = varieties.filter(name__iexact=name).first()
    else:
        variety = None

    if variety is None:
        return JsonResponse({'success': False})

    return JsonResponse({
        'success': True,
        'name': variety.name,
        'coffee_type': variety.default_coffee_type,
        'grade': variety.default_grade,
        'source': variety.default_source,
        'process': variety.default_process,
        'foreign_smell': variety.default_foreign_smell,
    })

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

    def get_queryset(self):
        return (
            CoffeeStock.objects
            .select_related('variety')
            .prefetch_related('movements')
            .order_by('-received_date', '-created_at')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx['total_available'] = sum(
            s.quantity_available for s in ctx['stocks']
        )

        ctx['low_stock_count'] = sum(
            1
            for s in ctx['stocks']
            if 0 < s.quantity_available <= s.reorder_level
        )

        ctx['out_of_stock_count'] = sum(
            1
            for s in ctx['stocks']
            if s.quantity_available <= 0
        )

        return ctx

class CoffeeStockDetailView(DetailView):
    model = CoffeeStock
    template_name = 'pipeline/stock_detail.html'
    context_object_name = "stock"


class VarietyDatalistMixin:
    """Feeds the 'name of material' datalist with its master defaults."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["existing_varieties"] = CoffeeVariety.objects.filter(
            is_active=True
        ).order_by("name")

        return context


class CoffeeStockCreateView(VarietyDatalistMixin, CreateView):

    model = CoffeeStock
    form_class = CoffeeStockIntakeForm
    template_name = "pipeline/stock_form.html"
    success_url = reverse_lazy("stock_list")

    def form_valid(self, form):

        result = record_intake(
            variety_name=form.cleaned_data["variety_name"],
            batch_data=form.batch_data(),
            quantity_received=form.cleaned_data["quantity_received"],
            user=current_user(self.request),
        )

        self.object = result.stock

        messages.success(self.request, result.message)

        return redirect(self.get_success_url())


class CoffeeStockUpdateView(VarietyDatalistMixin, UpdateView):
    model = CoffeeStock
    form_class = CoffeeStockForm
    template_name = "pipeline/stock_form.html"
    success_url = reverse_lazy("stock_list")

# sample views here
class SampleListView(ListView):
    model = Sample
    template_name = 'pipeline/sample_list.html'
    context_object_name = "samples"

    def get_queryset(self):
        return Sample.objects.select_related(
            'company',
            'coffee_stock__variety',
        ).order_by('-date_sent')

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

            stock = (
                CoffeeStock.objects
                .select_for_update()
                .get(pk=form.instance.coffee_stock_id)
            )

            roasted_available = get_stage_inventory(
                stock,
                'roasted'
            )

            if roasted_available < sample_weight:

                form.add_error(
                    'sample_weight',
                    'Not enough roasted coffee available.'
                )

                return self.form_invalid(form)

            response = super().form_valid(form)

            StockMovement.objects.create(
                stock=stock,
                movement_type='sample',
                from_stage='roasted',
                quantity=sample_weight,
                reference=str(self.object.id),
                created_by=current_user(self.request),
            )

            company = self.object.company

            company.pipeline_stage = 'sample_sent'

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

