from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView
from django.urls import reverse_lazy
from django.shortcuts import render
from django.db import transaction
from .models import Company, CoffeeStock, Sample,  CoffeeVariety
from .forms import CompanyForm, CoffeeStockForm, SampleForm
from django.http import JsonResponse


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

def get_variety_details(request):

    variety_id = request.GET.get('id')

    if not variety_id:
        return JsonResponse({
            'success': False
        })

    try:
        variety = CoffeeVariety.objects.get(
            pk=variety_id,
            is_active=True
        )
    except CoffeeVariety.DoesNotExist:
        return JsonResponse({
            'success': False
        })

    return JsonResponse({
        'success': True,
        'defaults': {
            'coffee_type': variety.default_coffee_type,
            'grade': variety.default_grade,
            'source': variety.default_source,
            'process': variety.default_process,
            'foreign_smell': variety.default_foreign_smell,
        }
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
def product_list(request):
    products = CoffeeStock.objects.all().order_by('current_stock')
    context = {'products': products}
    return render(request, 'coffee_stock_list.html', context)

class CoffeeStockListViews(ListView):
    model = CoffeeStock
    template_name = 'pipeline/coffee_stock_list.html'
    context_object_name = "stocks"

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
    template_name = 'pipeline/coffee_stock_detail.html'
    context_object_name = "stock"
class CoffeeStockCreateView(CreateView):

    model = CoffeeStock
    form_class = CoffeeStockForm
    template_name = "pipeline/stock_form.html"
    success_url = reverse_lazy("stock_list")

    @transaction.atomic
    def form_valid(self, form):

        stock = form.save()

        quantity_received = form.cleaned_data["quantity_received"]

        StockMovement.objects.create(
            stock=stock,
            movement_type="receipt",
            to_stage="green_received",
            quantity=quantity_received,
            reference=stock.batch_number,
            created_by=self.request.user,
        )

        self.object = stock

        return super().form_valid(form)

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        context["existing_varieties"] = CoffeeVariety.objects.filter(
            is_active=True
        ).order_by("name")

        return context

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
                created_by=self.request.user,
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