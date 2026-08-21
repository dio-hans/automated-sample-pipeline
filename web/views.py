from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, TemplateView, CreateView, UpdateView, DeleteView, DetailView, View

from .models import Company, CoffeeStock, CoffeeVariety, Sample, StockMovement, Followup, Contract
from .forms import (
    CompanyForm, CoffeeStockForm, CoffeeStockIntakeForm, SampleForm,
    ProcessingForm, ContractForm,
)
from .services.intake import record_intake
from django.db.models import F, DecimalField, ExpressionWrapper, Case, When, Value, IntegerField
from django.shortcuts import render
from .services.inventory import get_stage_inventory
from .services.processing import process_stock, PROCESS_STEPS
from .services.followup import (
    create_followup_for_sample, mark_guide_sent, mark_contract_sent, convert_to_contract,
)
from .utils.util import apply_date_filters


def current_user(request):
    return request.user if request.user.is_authenticated else None


# ===================== COMPANIES =====================

class CompanyListView(ListView):
    model = Company
    template_name = "pipeline/company_list.html"
    context_object_name = "companies"

    def get_queryset(self):
        qs = Company.objects.order_by("-created_at")
        qs, preset, today, start, end = apply_date_filters(self.request, qs, "created_at")
        self._preset = preset
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["preset"] = getattr(self, "_preset", "this_month")
        return ctx


class CompanyDetailView(DetailView):
    model = Company
    template_name = "pipeline/company_detail.html"
    context_object_name = "company"


class CompanyCreateView(CreateView):
    model = Company
    form_class = CompanyForm
    template_name = "pipeline/company_form.html"
    success_url = reverse_lazy("company_list")


class CompanyUpdateView(UpdateView):
    model = Company
    form_class = CompanyForm
    template_name = "pipeline/company_form.html"
    success_url = reverse_lazy("company_list")


class CompanyDeleteView(DeleteView):
    model = Company
    template_name = "pipeline/company_confirm_delete.html"
    success_url = reverse_lazy("company_list")


# ===================== COFFEE STOCK =====================

class CoffeeStockListViews(ListView):
    model = CoffeeStock
    template_name = "pipeline/coffee_stock_list.html"
    context_object_name = "stocks"

    def get_queryset(self):
        qs = (CoffeeStock.objects
              .select_related("variety")
              .prefetch_related("movements")
              .order_by("-received_date", "-created_at"))
        qs, preset, today, start, end = apply_date_filters(self.request, qs, "received_date")
        self._preset = preset
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["preset"] = getattr(self, "_preset", "this_month")
        ctx["total_available"] = sum(s.quantity_available for s in ctx["stocks"])
        ctx["low_stock_count"] = sum(1 for s in ctx["stocks"] if 0 < s.quantity_available <= s.reorder_level)
        ctx["out_of_stock_count"] = sum(1 for s in ctx["stocks"] if s.quantity_available <= 0)
        return ctx


class CoffeeStockDetailView(DetailView):
    model = CoffeeStock
    template_name = "pipeline/stock_detail.html"
    context_object_name = "stock"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["movements"] = self.object.movements.select_related("created_by").order_by("-created_at")
        ctx["processing_form"] = ProcessingForm()
        ctx["available_green"] = get_stage_inventory(self.object, "green_received")
        ctx["available_roasted"] = get_stage_inventory(self.object, "roasted")
        ctx["available_ground"] = get_stage_inventory(self.object, "ground")
        return ctx


class VarietyDatalistMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["existing_varieties"] = CoffeeVariety.objects.filter(is_active=True).order_by("name")
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


# ===================== PROCESSING (roast / grind / package) =====================

class ProcessStockView(View):
    """Post a processing step with loss accounting from the stock detail page."""

    def post(self, request, pk):
        stock = get_object_or_404(CoffeeStock, pk=pk)
        form = ProcessingForm(request.POST)
        if form.is_valid():
            try:
                result = process_stock(
                    stock=stock,
                    step=form.cleaned_data["step"],
                    input_quantity=form.cleaned_data["input_quantity"],
                    output_quantity=form.cleaned_data["output_quantity"],
                    user=current_user(request),
                    notes=form.cleaned_data["notes"],
                )
                messages.success(
                    request,
                    f"Processed {result.input_quantity} kg → {result.output_quantity} kg "
                    f"(loss {result.loss_quantity} kg).",
                )
            except ValueError as e:
                messages.error(request, str(e))
        else:
            for errors in form.errors.values():
                for err in errors:
                    messages.error(request, err)
        return redirect("stock_detail", pk=stock.pk)


# ===================== SAMPLES =====================

class SampleListView(ListView):
    model = Sample
    template_name = "pipeline/sample_list.html"
    context_object_name = "samples"

    def get_queryset(self):
        qs = Sample.objects.select_related("company", "coffee_stock__variety").order_by("-date_sent")
        qs, preset, today, start, end = apply_date_filters(self.request, qs, "date_sent")
        self._preset = preset
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["preset"] = getattr(self, "_preset", "this_month")
        return ctx


class SampleDetailView(DetailView):
    model = Sample
    template_name = "pipeline/sample_detail.html"
    context_object_name = "sample"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["followup"], _ = Followup.objects.get_or_create(sample=self.object)
        return ctx


class SampleCreateView(CreateView):
    model = Sample
    form_class = SampleForm
    template_name = "pipeline/sample_form.html"
    success_url = reverse_lazy("sample_list")

    def form_valid(self, form):
        sample_weight = form.instance.sample_weight
        with transaction.atomic():
            stock = CoffeeStock.objects.select_for_update().get(pk=form.instance.coffee_stock_id)
            roasted_available = get_stage_inventory(stock, "roasted")
            if roasted_available < sample_weight:
                form.add_error("sample_weight", "Not enough roasted coffee available.")
                return self.form_invalid(form)

            response = super().form_valid(form)

            StockMovement.objects.create(
                stock=stock,
                movement_type="sample",
                from_stage="roasted",
                quantity=sample_weight,
                reference=str(self.object.id),
                created_by=current_user(self.request),
            )

            company = self.object.company
            company.pipeline_stage = "sample_sent"
            company.save(update_fields=["pipeline_stage", "updated_at"])

            create_followup_for_sample(self.object)

        return response


class SampleUpdateView(UpdateView):
    model = Sample
    form_class = SampleForm
    template_name = "pipeline/sample_form.html"
    success_url = reverse_lazy("sample_list")


class SampleDeleteView(DeleteView):
    model = Sample
    template_name = "pipeline/sample_confirm_delete.html"
    success_url = reverse_lazy("sample_list")


# ===================== FOLLOW-UPS =====================

class FollowupListView(ListView):
    model = Followup
    template_name = "pipeline/followup_list.html"
    context_object_name = "followups"

    def get_queryset(self):
        return (Followup.objects
                .select_related("sample__company", "sample__coffee_stock__variety")
                .order_by("-created_at"))


class FollowupDetailView(DetailView):
    model = Followup
    template_name = "pipeline/followup_detail.html"
    context_object_name = "followup"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if not self.object.is_converted_to_contract:
            ctx["contract_form"] = ContractForm()
        return ctx


class MarkGuideSentView(View):
    def post(self, request, pk):
        followup = get_object_or_404(Followup, pk=pk)
        mark_guide_sent(followup)
        messages.success(request, "Day-3 guide marked as sent.")
        return redirect("followup_detail", pk=followup.pk)


class MarkContractSentView(View):
    def post(self, request, pk):
        followup = get_object_or_404(Followup, pk=pk)
        mark_contract_sent(followup)
        messages.success(request, "Day-7 contract prompt marked as sent.")
        return redirect("followup_detail", pk=followup.pk)


class ConvertToContractView(View):
    def post(self, request, pk):
        followup = get_object_or_404(Followup, pk=pk)
        form = ContractForm(request.POST)
        if form.is_valid():
            contract = convert_to_contract(
                followup=followup,
                volume_per_cycle_kg=form.cleaned_data["volume_per_cycle_kg"],
                price_per_kg=form.cleaned_data["price_per_kg"],
                delivery_frequency=form.cleaned_data["delivery_frequency"],
                signed_name=form.cleaned_data["signed_name"],
                user=current_user(request),
            )
            messages.success(request, f"Contract {contract.id} signed with {contract.company.name}.")
            return redirect("contract_detail", pk=contract.pk)
        for errors in form.errors.values():
            for err in errors:
                messages.error(request, err)
        return redirect("followup_detail", pk=followup.pk)


# ===================== CONTRACTS =====================

class ContractListView(ListView):
    model = Contract
    template_name = "pipeline/contract_list.html"
    context_object_name = "contracts"

    def get_queryset(self):
        qs = Contract.objects.select_related("company", "sample").order_by("-signed_at")
        qs, preset, today, start, end = apply_date_filters(self.request, qs, "signed_at")
        self._preset = preset
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["preset"] = getattr(self, "_preset", "this_month")
        return ctx


class ContractDetailView(DetailView):
    model = Contract
    template_name = "pipeline/contract_detail.html"
    context_object_name = "contract"


# ===================== API ENDPOINTS =====================

def get_variety_details(request):
    varieties = CoffeeVariety.objects.filter(is_active=True)
    variety_id = request.GET.get("id")
    name = (request.GET.get("name") or "").strip()
    if variety_id:
        variety = varieties.filter(pk=variety_id).first()
    elif name:
        variety = varieties.filter(name__iexact=name).first()
    else:
        variety = None
    if variety is None:
        return JsonResponse({"success": False})
    return JsonResponse({
        "success": True,
        "name": variety.name,
        "coffee_type": variety.default_coffee_type,
        "grade": variety.default_grade,
        "source": variety.default_source,
        "process": variety.default_process,
        "foreign_smell": variety.default_foreign_smell,
    })


def stock_stage_inventory_api(request, pk):
    """JSON snapshot of a batch's quantity at every stage — powers the processing form."""
    stock = get_object_or_404(CoffeeStock, pk=pk)
    return JsonResponse({
        "green_received": float(get_stage_inventory(stock, "green_received")),
        "roasted": float(get_stage_inventory(stock, "roasted")),
        "ground": float(get_stage_inventory(stock, "ground")),
        "packaged": float(get_stage_inventory(stock, "packaged")),
        "available": float(stock.quantity_available),
    })

class StockMovementListView(ListView):
    model = StockMovement
    template_name = "pipeline/stock_movement_list.html"
    context_object_name = "movements"
    paginate_by = 50

    def get_queryset(self):
        qs = (
            StockMovement.objects
            .select_related(
                "stock",
                "stock__variety",
                "created_by",
            )
            .order_by("-created_at")
        )

        qs, preset, today, start, end = apply_date_filters(
            self.request,
            qs,
            "created_at",
        )

        self._preset = preset
        self._start = start
        self._end = end

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["preset"] = getattr(
            self,
            "_preset",
            "this_month",
        )

        context["start_date"] = getattr(
            self,
            "_start",
            None,
        )

        context["end_date"] = getattr(
            self,
            "_end",
            None,
        )

        return context

class DashboardView(TemplateView):
    template_name = "pipeline/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        stocks = (
            CoffeeStock.objects
            .select_related("variety")
            .prefetch_related("movements")
        )

        movements = (
            StockMovement.objects
            .select_related(
                "stock",
                "stock__variety",
                "created_by",
            )
        )

        context["stocks"] = stocks
        context["recent_movements"] = movements[:10]

        context["total_stock"] = sum(
            stock.quantity_available
            for stock in stocks
        )

        context["green_stock"] = sum(
            stock.quantity_green
            for stock in stocks
        )

        context["roasted_stock"] = sum(
            stock.quantity_roasted
            for stock in stocks
        )

        context["ground_stock"] = sum(
            stock.quantity_ground
            for stock in stocks
        )

        context["packaged_stock"] = sum(
            stock.quantity_packaged
            for stock in stocks
        )

        context["low_stock"] = [
            stock
            for stock in stocks
            if stock.is_low_stock
        ]

        context["out_of_stock"] = [
            stock
            for stock in stocks
            if stock.quantity_available <= 0
        ]

        return context


def low_stock_list(request):
    stocks = (
        CoffeeStock.objects
        .select_related("variety")
        .prefetch_related("movements")
    )
    low_stocks = [s for s in stocks if s.quantity_available <= s.reorder_level]
    # Out-of-stock first, then by lowest available
    low_stocks.sort(key=lambda s: (s.quantity_available > 0, s.quantity_available))

    return render(request, "pipeline/low_stock_list.html", {
        "low_stocks": low_stocks,
        "low_stock_count": len(low_stocks),
    })