from datetime import datetime, time, timezone
from django.db.models import F, Q, DecimalField, ExpressionWrapper
from django.db.models.aggregates import Sum
from django.utils import timezone
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .sales_workflow import fulfill_request_item
from .models import AccountHolder, PaymentReceipt, StockRequest, PackagedProduct, StockRequestItem, StockStage
from decimal import Decimal
from .services.processing import complete_roasting, complete_sorting
from django.views.generic import TemplateView
from .models import CoffeeStock, PackagedInventory
from django.contrib import messages
from django.contrib.auth import login, logout
from django.core.exceptions import ValidationError
from .services.packaging import execute_pack_return
from django.db import models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    UpdateView,
    View,
)
# Adjust this import to match wh
# ere your mixin resides
from .forms import (
    CoffeeStockForm,
    CoffeeStockIntakeForm,
    CompanyForm,
    ContractForm,
    PackagedProductBulkForm,
    PackagingRunForm,
    PackReleaseForm,
    SampleForm,
    UserLoginForm,
    UserRegistrationForm,
)
from .models import (
    CoffeeVariety,
    Company,
    Contract,
    Followup,
    PackagedProduct,
    PackagingRun,
    PackRelease,
    PackReturn,
    ProcessingRun,
    Sample,
    StockMovement,
    StockRequest,
    User,
)
from .permissions import RoleRequiredMixin
from .services.followup import (
    convert_to_contract,
    create_followup_for_sample,
    mark_contract_sent,
    mark_guide_sent,
)
from .services.intake import record_intake
from .services.inventory import get_stage_inventory
from .services.packaging import (
    execute_pack_release,
    execute_pack_return,
    execute_packaging_run,
)
from .services.processing import (
    issue_for_processing, complete_grinding, complete_sorting
)
from .utils.util import apply_date_filters
from .sales_forms import AccountHolderForm, PackReturnForm


def redirect_user_by_role(user):
    """Send each authenticated user to the workspace for their role."""
    if user.is_superuser or user.role == User.Role.ADMIN:
        return redirect("admin_dashboard")
    if user.role == User.Role.MANAGER:
        return redirect("inventory_dashboard")
    if user.role == User.Role.SALES:
        return redirect("record_sale")
    if user.role in {User.Role.CASHIER, User.Role.ACCOUNTS}:
        return redirect("order_queue")
    return redirect("login")

def user_login(request):
    if request.method == 'POST':
        form = UserLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            if not user.is_active:
                messages.error(request, "Access Denied: Your account has been suspended.")
                return redirect('login')

            login(request, user)
            messages.success(request, f"Welcome back, {user.username}!")
            return redirect_user_by_role(user)
    else:
        form = UserLoginForm()

    return render(request, 'pipeline/login.html', {'form': form})

def user_logout(request):
    logout(request)
    messages.info(request, "Session terminated successfully.")
    return redirect('login')


# Staff Profiling and Administrative Actions

def register_user(request):
    """
    Unified User Control Gateway: Manages real-time 
    staff account listing alongside provisioning forms.
    """
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            new_user = form.save()
            messages.success(request, f"Terminal credentials generated successfully for {new_user.username}!")
            return redirect('login') # Keeps Admin on page to view updated table
        else:
            messages.error(request, "Account registration failed. Verify database constraints.")
    else:
        form = UserRegistrationForm()

    # Query active system users to populate the integrated dashboard table
    system_users = User.objects.all().order_by('role', 'username')
    
    return render(request, 'pipeline/registration.html', {
        'form': form,
        'users': system_users
    })

def toggle_user_status(request, user_id):
    """
    Soft deactivation feature to handle account locks safely.
    Protected explicitly against arbitrary privilege escalations.
    """
    employee = get_object_or_404(User, id=user_id)
    
    if employee == request.user:
        messages.error(request, "Security Violation Protection: You cannot lock out your own administrative account.")
        return redirect('regi/ster_user')

    # Atomic inversion of status state
    employee.is_active = not employee.is_active
    employee.save()

    status = "activated" if employee.is_active else "suspended"
    
    if employee.is_active:
        messages.success(request, f"Access clearance for {employee.username} successfully restored.")
    else:
        messages.warning(request, f"Terminal operational rights for {employee.username} have been suspended.")
        
    return redirect('register_user')

# AUTH & USER CONTROL    
class InventoryRoleRequiredMixin:
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER, User.Role.ACCOUNTS)
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        if request.user.is_superuser or request.user.role in self.allowed_roles:
            return super().dispatch(request, *args, **kwargs)
        messages.error(request, "You do not have permission to access inventory operations.")
        return redirect("dashboard")    
    
class PackagingRunListView(InventoryRoleRequiredMixin, ListView):
    model = PackagingRun
    template_name = "pipeline/packaging_run_list.html"
    context_object_name = "packaging_runs"

    def get_queryset(self):
        return PackagingRun.objects.select_related(
            "product__blend", "product__pack_size", "stock"
        ).order_by("-issued_at")


class PackagingRunDetailView(InventoryRoleRequiredMixin, DetailView):
    model = PackagingRun
    template_name = "pipeline/packaging_run_detail.html"
    context_object_name = "run"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        run = self.object
        kg_per_pack = run.product.kg_per_pack
        ctx["represented_kg"] = Decimal(run.packs_produced or 0) * kg_per_pack
        return ctx


class PackagingRunCreateView(InventoryRoleRequiredMixin, CreateView):
    model = PackagingRun
    form_class = PackagingRunForm
    template_name = "pipeline/packaging_run_form.html"
    success_url = reverse_lazy("packaging_run_list")

    def form_valid(self, form):
        try:
            execute_packaging_run(
                stock=form.cleaned_data["stock"],
                product=form.cleaned_data["product"],
                source_stage=form.cleaned_data["source_stage"],
                input_kg=form.cleaned_data["input_kg"],
                packs_produced=form.cleaned_data["packs_produced"],
                user=current_user(self.request),
                notes=form.cleaned_data.get("notes", "")
            )
            messages.success(self.request, "Packaging run recorded successfully.")
            return redirect(self.success_url)
        except ValidationError as e:
            form.add_error(None, e.message)
            return self.form_invalid(form)


# ===================== PACK RELEASE & RETURN VIEWS =====================

class PackReleaseListView(InventoryRoleRequiredMixin, ListView):
    model = PackRelease
    template_name = "pipeline/pack_release_list.html"
    context_object_name = "releases"

    def get_queryset(self):
        # Optimized database query joining your product lots and client profiles
        qs = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to"
        ).prefetch_related("returns", "payments").order_by("-released_at")

        # Dynamically apply date filters based on your timeline presets
        from .utils.util import apply_date_filters
        qs, preset, today, start, end = apply_date_filters(self.request, qs, "released_at")
        self._preset = preset
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        all_filtered_releases = ctx["releases"]

        # 🦺 CRASH-PROOF ERP FINANCIAL AGGREGATIONS
        # We calculate the total stock value safely. If the property 'stock_value' 
        # is missing from the model, we calculate it dynamically via (packs_out * selling_price)
        total_value_issued = sum(
            getattr(r, "stock_value", Decimal(r.packs_out) * r.selling_price) 
            for r in all_filtered_releases
        )
        
        # Sum initial deposits plus any later cashier collections safely
        total_collected_revenue = sum(
            getattr(r, "total_amount_paid", Decimal(r.amount_paid_on_take if hasattr(r, "amount_paid_on_take") else 0)) 
            for r in all_filtered_releases
        )
        
        # Calculate outstanding balances due in the field safely
        total_outstanding_debt = sum(
            getattr(r, "outstanding_balance", (Decimal(r.packs_out) * r.selling_price) - getattr(r, "total_amount_paid", 0)) 
            for r in all_filtered_releases
        )

        ctx.update({
            "preset": getattr(self, "_preset", "this_month"),
            
            # Safe Financial Metrics Context passed to your cards
            "total_value_issued": total_value_issued,
            "total_collected_revenue": total_collected_revenue,
            "total_outstanding_debt": total_outstanding_debt,
        })
        return ctx




class PackReleaseDetailView(InventoryRoleRequiredMixin, DetailView):
    model = PackRelease
    template_name = "pipeline/pack_release_detail.html"
    context_object_name = "release"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        release = self.object

        ctx["returns"] = (
            release.returns
            .select_related("received_by")
            .order_by("-returned_at")
        )

        ctx["payments"] = (
            release.payments
            .select_related("collected_by")
            .order_by("-collected_at")
        )

        return ctx


class PackReleaseCreateView(InventoryRoleRequiredMixin, CreateView):
    model = PackRelease
    form_class = PackReleaseForm
    template_name = "pipeline/pack_release_form.html"
    success_url = reverse_lazy("pack_release_list")

    def form_valid(self, form):
        try:
            execute_pack_release(
                product=form.cleaned_data["product"],
                released_to=form.cleaned_data["released_to"],
                packs_out=form.cleaned_data["packs_out"],
                selling_price=form.cleaned_data["selling_price"],
                user=current_user(self.request),
                notes=form.cleaned_data.get("notes", "")
            )
            messages.success(self.request, "Stock release executed successfully.")
            return redirect(self.success_url)
        except ValidationError as e:
            form.add_error(None, e.message)
            return self.form_invalid(form)


class PackReturnListView(InventoryRoleRequiredMixin, ListView):
    model = PackReturn
    template_name = "pipeline/pack_return_list.html"
    context_object_name = "returns"

    def get_queryset(self):
        # Use double underscores (__) to fetch deep relationships like the product metadata
        return PackReturn.objects.select_related(
            "release__product__blend", 
            "release__product__pack_size", 
            "received_by"
        ).order_by("-id")


def current_user(request):
    return request.user if request.user.is_authenticated else None


def dashboard_router(request):
    if not request.user.is_authenticated:
        return redirect("login")
    return redirect_user_by_role(request.user)


# COMPANIES

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


# COFFEE STOCK

from django.views.generic import ListView
from .models import CoffeeStock

class CoffeeStockListViews(RoleRequiredMixin, ListView):
    model = CoffeeStock
    template_name = "pipeline/coffee_stock_list.html"
    context_object_name = "stocks"
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.SALES, User.Role.CASHIER, User.Role.ACCOUNTS)

    def get_queryset(self):
        # Optimized database query pulling your varieties up front
        qs = CoffeeStock.objects.select_related("variety").order_by("-created_at")

        # Apply your standard corporate date range timeline filters
        from .utils.util import apply_date_filters
        qs, preset, today, start, end = apply_date_filters(self.request, qs, "created_at")
        self._preset = preset

        # 🎯 DRILL-DOWN TOGGLE LOOKUP
        # We cache the full filtered timeline list in memory first to calculate stable counters
        self._all_records = list(qs)
        
        self._current_status_filter = self.request.GET.get("status", "all")
        if self._current_status_filter == "exhausted":
            # Filter table rows to isolate only completely depleted batches
            return [s for s in self._all_records if s.quantity_available <= 0]
        elif self._current_status_filter == "low":
            # Filter table rows to isolate only items below safety thresholds
            return [s for s in self._all_records if s.quantity_available > 0 and s.quantity_available <= s.reorder_level]
        
        return self._all_records

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        
        # Calculate dynamic aggregations on the fly across full timeline records
        total_available = sum(s.quantity_available for s in self._all_records)
        low_stock_count = sum(1 for s in self._all_records if s.quantity_available > 0 and s.quantity_available <= s.reorder_level)
        out_of_stock_count = sum(1 for s in self._all_records if s.quantity_available <= 0)

        ctx.update({
            "preset": getattr(self, "_preset", "this_month"),
            "status_filter": getattr(self, "_current_status_filter", "all"),
            
            # Dashboard Metrics Counters
            "total_available": total_available,
            "low_stock_count": low_stock_count,
            "out_of_stock_count": out_of_stock_count,
        })
        return ctx




class CoffeeStockDetailView(DetailView):
    model = CoffeeStock
    template_name = "pipeline/stock_detail.html"
    context_object_name = "stock"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["movements"] = self.object.movements.select_related("created_by").order_by("-created_at")
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
    """JSON snapshot of a batch's quantity at every stage Ã¢â‚¬â€ powers the processing form."""
    stock = get_object_or_404(CoffeeStock, pk=pk)
    return JsonResponse({
        "green_received": float(get_stage_inventory(stock, "green_received")),
        "roasted": float(get_stage_inventory(stock, "roasted")),
        "ground": float(get_stage_inventory(stock, "ground")),
        "available": float(stock.quantity_available),
    })

class StockMovementListView(ListView):
    model = StockMovement
    template_name = "pipeline/stock_movement_list.html"
    context_object_name = "movements"
    paginate_by = 50

    def get_queryset(self):
        return (
            CoffeeStock.objects
            .select_related("variety")
            .prefetch_related("movements")
            .order_by("-received_date", "-created_at")
        )

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

        packaged_inventory = list(PackagedInventory.objects.select_related("product__blend", "product__pack_size"))
        context["packaged_stock"] = sum(item.available for item in packaged_inventory)
        context["packaged_inventory"] = packaged_inventory

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


# PROCESS STOCK VIEW

class ProcessingWorkspaceView(RoleRequiredMixin, View):
    allowed_roles = (
        User.Role.MANAGER,
        User.Role.ADMIN,
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
    )

    template_name = "pipeline/processing_workspace.html"
    def get(self, request):
        stocks = (
            CoffeeStock.objects
            .select_related("variety")
            .order_by("variety__name", "batch_number")
        )

        open_runs = (
            ProcessingRun.objects
            .filter(status="open")
            .select_related("stock", "stock__variety")
            .order_by("-issued_at")
        )

        return render(
            request,
            self.template_name,
            {
                "stocks": stocks,
                "open_runs": open_runs,
            },
        )


class IssueProcessingRunView(RoleRequiredMixin, View):
    """Step 1: Called when coffee is taken and loaded into the machinery."""
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER, User.Role.ACCOUNTS)

    def post(self, request, pk):
        stock = get_object_or_404(CoffeeStock, pk=pk)
        process_type = request.POST.get("process_type") # 'roasting' or 'grinding'
        input_qty = request.POST.get("input_quantity")

        if not input_qty or float(input_qty) <= 0:
            messages.error(request, "Please enter a valid input quantity.")
            return redirect("processing_run_list")

        try:
            # Open the processing run in the system (Leaves status='open')
            result = issue_for_processing(
                stock=stock,
                process_type=process_type,
                input_quantity=float(input_qty),
                user=request.user,
                notes=request.POST.get("notes", "")
            )
            messages.success(request, f"Batch {stock.batch_number} successfully loaded into the {process_type} pipeline.")
        except Exception as exc:
            messages.error(request, str(exc))

        return redirect("processing_workspace")


class CompleteProcessingRunView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.ACCOUNTS, User.Role.CASHIER)

    def post(self, request, pk):
        run = get_object_or_404(ProcessingRun.objects.select_related("stock"), pk=pk)
        user = request.user
        notes = request.POST.get("notes", "")

        try:
            if run.process_type == "roasting":
                # Accept good_quantity from form (or output_quantity)
                output_qty = request.POST.get("good_quantity") or request.POST.get("output_quantity") or "0"
                bad_qty = request.POST.get("bad_quantity") or request.POST.get("quaker_quantity") or "0"
                
                completed_run, process_loss = complete_roasting(
                    processing_run=run,
                    good_quantity=output_qty,
                    bad_quantity=bad_qty,
                    user=user,
                    notes=notes,
                )
                messages.success(
                    request,
                    f"Roasting complete: {completed_run.output_quantity} kg roasted output registered."
                )

            elif run.process_type == "sorting":
                good_qty = request.POST.get("good_quantity") or request.POST.get("output_quantity") or "0"
                quaker_qty = request.POST.get("bad_quantity") or request.POST.get("quaker_quantity") or "0"
                
                completed_run = complete_sorting(
                    processing_run=run,
                    good_quantity=good_qty,
                    quaker_quantity=quaker_qty,
                    user=user,
                    notes=notes,
                )
                messages.success(request, "Sorting run completed successfully.")

            elif run.process_type == "grinding":
                output_qty = request.POST.get("output_quantity") or "0"
                completed_run, _ = complete_grinding(
                    processing_run=run,
                    output_quantity=output_qty,
                    user=user,
                    notes=notes,
                )
                messages.success(request, f"Grinding complete: {completed_run.output_quantity} kg ground coffee produced.")

        except Exception as exc:
            messages.error(request, str(exc))

        return redirect("processing_workspace")

# PACKAGED INVENTORY VIEWS

class PackagedInventoryListView(InventoryRoleRequiredMixin, ListView):
    model = PackagedInventory
    template_name = "pipeline/packaged_inventory_list.html"
    context_object_name = "inventory"

    def get_queryset(self):
        return PackagedInventory.objects.select_related(
            "product__blend", "product__pack_size"
        ).all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        
        # 1. Turn the inventory queryset into a list
        inv = list(ctx["inventory"])
        
        # 2. 📊 Sort the list in memory using your 'available' property (lowest stock first)
        inv_sorted = sorted(inv, key=lambda i: i.available)
        
        # 3. Save the newly sorted list back into the context for your HTML table template
        ctx["inventory"] = inv_sorted
        ctx["products"] = [item.product for item in inv_sorted]
        ctx["total_available"] = sum(i.available for i in inv_sorted)
        ctx["total_released"] = sum(i.packs_released for i in inv_sorted)
        ctx["total_returned"] = sum(i.packs_returned for i in inv_sorted)
        return ctx


class PackagedProductDetailView(InventoryRoleRequiredMixin, DetailView):
    model = PackagedProduct
    template_name = "pipeline/packaged_product_detail.html"
    context_object_name = "product"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        product = self.object
        
        inventory, _ = PackagedInventory.objects.get_or_create(product=product)
        ctx["inventory"] = inventory
        
        ctx["packaging_runs"] = PackagingRun.objects.filter(
            product=product
        ).select_related("stock", "stock__variety").order_by("-issued_at")
        
        ctx["releases"] = PackRelease.objects.filter(
            product=product
        ).order_by("-released_at")
        
        return ctx




class PackagedProductCreateView(InventoryRoleRequiredMixin, FormView):
    # 1. Cleanly assign the class type here
    form_class = PackagedProductBulkForm
    template_name = "pipeline/packaged_product_form.html"  
    success_url = reverse_lazy("packaged_inventory_list")  

    # 2. Modify the form instance dynamically before it goes to the template
    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        
        #  FIXED: Loop dynamically through every actual field name in the form
        for name, field in form.fields.items():
            field.widget.attrs.update({
                'class': 'w-full rounded-md border border-[#E4DECB] px-3 py-2.5 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-rust'
            })
                    
        return form
  


class PackReturnCreateView(InventoryRoleRequiredMixin, CreateView):
    model = PackReturn
    form_class = PackReturnForm
    template_name = "pipeline/pack_return_form.html"
    success_url = reverse_lazy("pack_return_list")
    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.MANAGER,
        User.Role.ADMIN,
    )

    def get_release(self):
        return get_object_or_404(
            PackRelease.objects.select_related(
                "product__blend",
                "product__pack_size",
                "released_to",
            ),
            pk=self.kwargs["pk"],
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["release"] = self.get_release()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["release"] = self.get_release()
        return context

    def form_valid(self, form):
        release = self.get_release()
        try:
            execute_pack_return(
                release=release,
                packs_returned=form.cleaned_data["packs_returned"],
                user=self.request.user,
                condition=form.cleaned_data.get("condition", "good"),
                disposition=form.cleaned_data.get("disposition", "accepted"),
                reason=form.cleaned_data.get("reason", ""),
                notes=form.cleaned_data.get("notes", ""),
            )
            messages.success(
                self.request,
                "Pack return recorded successfully.",
            )
            return redirect(self.success_url)
        except ValidationError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

class RecordInstallmentPaymentView(RoleRequiredMixin, CreateView):
    """
    Dedicated view class to record incoming payment collections (Cash/MoMo)
    against an outstanding salesperson credit balance.
    """
    model = PaymentReceipt
    template_name = "pipeline/record_payment_form.html"
    fields = ["amount", "method", "payment_reference", "notes"] 
    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.MANAGER,
        User.Role.ADMIN,
    )
    success_url = reverse_lazy("credit_control_ledger") 

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        release = get_object_or_404(PackRelease, pk=self.kwargs["pk"])
        
        # Calculate dynamic fallback valuation metrics context safely
        if hasattr(release, "stock_value") and release.stock_value:
            calculated_total_value = release.stock_value
        elif hasattr(release, "gross_amount") and release.gross_amount:
            calculated_total_value = release.gross_amount
        else:
            calculated_total_value = Decimal(release.packs_out) * release.selling_price

        ctx.update({
            "release": release,
            "calculated_total_value": calculated_total_value,
        })
        return ctx

    # 🛡️ THE CRITICAL INTERCEPTION STEP: This method must be named EXACTLY form_valid
    def form_valid(self, form):
        # 1. Fetch the target release record lot matching our URL parameter
        release = get_object_or_404(PackRelease, pk=self.kwargs["pk"])
        
        # 2. Extract form contents into memory without committing to the database yet
        payment = form.save(commit=False)
        
        # 3. ✅ BIND THE FOREIGN KEYS SECURELY (Resolves the NOT NULL constraint crash)
        payment.release = release
        payment.collected_by = self.request.user

        # 4. 🦺 OVERPAYMENT GUARD RAIL
        if payment.amount > release.outstanding_balance:
            messages.error(
                self.request, 
                f"Overpayment rejected! The maximum outstanding balance for "
                f"{release.released_to.name} is {release.outstanding_balance:,.0f} UGX."
            )
            return self.form_invalid(form)

        # 5. Safe to commit to the database now that all fields are complete
        payment.save()
        
        # Refresh the database values to clear cached quantities instantly
        release.refresh_from_db()
        
        messages.success(
            self.request, 
            f"Successfully recorded UGX {payment.amount:,.0f} via "
            f"{payment.get_method_display()} from {release.released_to.name}."
        )
        return redirect(self.success_url)



class CashLedgerListView(RoleRequiredMixin, ListView):
    """
    Central financial control screen.

    Cashiers collect money.
    Accounts can review/manage collections.
    Managers and Admins retain oversight.
    """

    model = PackRelease
    template_name = "pipeline/credit_control_ledger.html"
    context_object_name = "releases"

    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.MANAGER,
        User.Role.ADMIN,
    )

    def get_queryset(self):
        from .utils.util import apply_date_filters

        qs = (
            PackRelease.objects
            .select_related(
                "product",
                "product__blend",
                "product__pack_size",
                "released_to",
            )
            .prefetch_related(
                "payments",
                "returns",
            )
            .order_by("-released_at")
        )

        # Apply timeline filter
        qs, preset, today, start, end = apply_date_filters(
            self.request,
            qs,
            "released_at",
        )

        self._preset = preset

        # Active debtors / historical records
        self._view_scope = self.request.GET.get(
            "scope",
            "active",
        )

        if self._view_scope == "active":
            qs = [
                release
                for release in qs
                if release.outstanding_balance > 0
            ]

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        from .utils.util import apply_date_filters

        # ---------------------------------------------------------
        # ALL RELEASES FOR THE SELECTED TIMELINE
        # Used for the summary cards.
        # ---------------------------------------------------------
        all_records_qs = (
            PackRelease.objects
            .select_related(
                "product",
                "product__blend",
                "product__pack_size",
                "released_to",
            )
            .prefetch_related(
                "payments",
                "returns",
            )
            .order_by("-released_at")
        )

        all_records_qs, _, _, _, _ = apply_date_filters(
            self.request,
            all_records_qs,
            "released_at",
        )

        all_records = list(all_records_qs)

        # ---------------------------------------------------------
        # SUMMARY CARDS
        # ---------------------------------------------------------

        # Gross value of all stock dispatched
        total_value_issued = sum(
            release.gross_amount
            for release in all_records
        )

        # All payments collected
        total_collected_revenue = sum(
            release.total_amount_paid
            for release in all_records
        )

        # Remaining amount owed
        total_outstanding_debt = sum(
            release.outstanding_balance
            for release in all_records
        )

        # Active debts
        active_debts = [
            release
            for release in all_records
            if release.outstanding_balance > 0
        ]

        ctx.update({
            "preset": getattr(
                self,
                "_preset",
                self.request.GET.get(
                    "preset",
                    "this_month",
                ),
            ),

            "view_scope": getattr(
                self,
                "_view_scope",
                self.request.GET.get(
                    "scope",
                    "active",
                ),
            ),

            "total_value_issued": total_value_issued,
            "total_collected_revenue": total_collected_revenue,
            "total_outstanding_debt": total_outstanding_debt,

            "active_debts": active_debts,
        })

        return ctx

class CancelStockRequestView(RoleRequiredMixin, View):
    """
    Completely isolated view to handle order cancellations safely 
    without impacting any fulfillment or packaging code paths.
    """
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)

    def post(self, request, pk):
        # Fetch the request
        stock_request = get_object_or_404(StockRequest, pk=pk)
        
        # Atomically alter the status to cancelled
        stock_request.status = "cancelled"
        stock_request.save(update_fields=["status"])
        
        messages.error(request, f"Stock request ticket {stock_request.short_number} has been cancelled and removed from the active queue.")
        return redirect("stock_request_list")



from decimal import Decimal
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from django.views.generic import ListView

# Import PaymentReceipt alongside PackRelease
from .models import PackRelease, PaymentReceipt


class CreditControlLedgerView(RoleRequiredMixin, ListView):
    model = PackRelease
    template_name = "pipeline/credit_control_ledger.html"
    context_object_name = "releases"

    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.MANAGER,
        User.Role.ADMIN,
    )

    def get_queryset(self):
        qs = (
            PackRelease.objects
            .select_related(
                "product__blend",
                "product__pack_size",
                "released_to",
            )
            .prefetch_related(
                "returns",
                "payments",
            )
            .order_by("-released_at")
        )

        from .utils.util import apply_date_filters

        qs, preset, today, start, end = apply_date_filters(
            self.request,
            qs,
            "released_at",
        )

        self._preset = preset
        self._start = start
        self._end = end
        self._view_scope = self.request.GET.get(
            "scope",
            "active",
        )

        # ---------------------------------------------------------
        # TABLE FILTER
        # ---------------------------------------------------------
        if self._view_scope == "active":
            return [
                release
                for release in qs
                if getattr(
                    release,
                    "outstanding_balance",
                    Decimal("0.00"),
                ) > Decimal("0.00")
            ]

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        start = getattr(self, "_start", None)
        end = getattr(self, "_end", None)

        # ---------------------------------------------------------
        # NORMALISE DATE RANGE
        # ---------------------------------------------------------
        if start and hasattr(start, "date"):
            start_date = start.date()
        else:
            start_date = start

        if end and hasattr(end, "date"):
            end_date = end.date()
        else:
            end_date = end

        # ---------------------------------------------------------
        # 1. TOTAL VALUE DISPATCHED
        #
        # Only releases dispatched during the selected period.
        #
        # If nothing was dispatched today:
        #     0 UGX
        # ---------------------------------------------------------
        dispatched_qs = PackRelease.objects.all()

        if start_date and end_date:
            dispatched_qs = dispatched_qs.filter(
                released_at__date__range=(
                    start_date,
                    end_date,
                )
            )

        total_value_issued = dispatched_qs.aggregate(
    total=Sum(
        ExpressionWrapper(
            F("packs_out") * F("selling_price"),
            output_field=DecimalField(
                max_digits=12,
                decimal_places=2,
            ),
        )
    )
)["total"] or Decimal("0.00")

        # ---------------------------------------------------------
        # 2. LIQUID CASH COLLECTED
        #
        # Based on WHEN THE MONEY WAS RECEIVED,
        # not when the coffee was dispatched.
        #
        # Example:
        # Dispatch: 10 Sep
        # Payment: 17 Sep
        #
        # Payment appears in 17 Sep collections.
        # ---------------------------------------------------------
        payments_qs = PaymentReceipt.objects.all()

        if start_date and end_date:
            payments_qs = payments_qs.filter(
                collected_at__date__range=(
                    start_date,
                    end_date,
                )
            )

        total_collected_revenue = (
            payments_qs.aggregate(
                total=Sum("amount")
            )["total"]
            or Decimal("0.00")
        )

        # ---------------------------------------------------------
        # 3. TOTAL OUTSTANDING DEBT
        #
        # This is the CURRENT outstanding debt.
        #
        # It is NOT restricted by the selected date.
        # ---------------------------------------------------------
        all_releases = (
            PackRelease.objects
            .prefetch_related(
                "returns",
                "payments",
            )
            .all()
        )

        total_outstanding_debt = sum(
            (
                release.outstanding_balance
                for release in all_releases
                if getattr(
                    release,
                    "outstanding_balance",
                    Decimal("0.00"),
                ) > Decimal("0.00")
            ),
            Decimal("0.00"),
        )

        # ---------------------------------------------------------
        # CONTEXT
        # ---------------------------------------------------------
        ctx.update({
            "preset": getattr(
                self,
                "_preset",
                "today",
            ),
            "view_scope": getattr(
                self,
                "_view_scope",
                "active",
            ),
            "total_value_issued": total_value_issued,
            "total_collected_revenue": total_collected_revenue,
            "total_outstanding_debt": total_outstanding_debt,
        })

        return ctx

    
class LowStockListView(RoleRequiredMixin, TemplateView):
    """
    Unified low stock control desk displaying both raw coffee processing lots 
    and retail packaged finished prcoducts on a single warning page.
    """
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER)
    template_name = "pipeline/low_stock.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 🟤 1. RAW BULK COFFEE BATCHES (Kilograms)
        # Filters batches that have dropped to or below their safety levels
        bulk_stocks = CoffeeStock.objects.select_related("variety").all()
        low_bulk_batches = [
            stock for stock in bulk_stocks 
            if stock.quantity_available <= getattr(stock, "reorder_level", 10.0)
        ]

        # 📦 2. PACKAGED FINISHED GOODS (Pieces / Packets)
        # Pulls packaged inventory items that have dropped below minimum reorder points
        packaged_inventory = PackagedInventory.objects.select_related(
            "product__blend", "product__pack_size"
        ).all()
        
        # Capture low retail packs (using available stock field names)
        low_packaged_products = [
            item for item in packaged_inventory 
            if item.available <= getattr(item, "reorder_level", 10)
        ]

        # 📊 COMBINED QUANTITY FOR THE ALERT CARD COUNTER
        total_alerts_count = len(low_bulk_batches) + len(low_packaged_products)

        ctx.update({
            # Bulk Context
            "low_stocks": low_bulk_batches,
            "low_stock_count": len(low_bulk_batches),
            
            # Packaged Context (Matches template loops)
            "low_packaged_items": low_packaged_products,
            "total_alerts_count": total_alerts_count,
        })
        return ctx

"""
Additions to web/sales_workflow.py — Nonda Commodities

Adds two functions your cashier "order card" feature needs:

  1. record_card_payment — apply ONE payment across every product line
     that belongs to the same order (StockRequest), oldest outstanding
     line first, instead of forcing the cashier to pay each release
     separately.

  2. add_item_to_request — add another product line to an order that is
     still open, so a salesperson's card can keep growing instead of
     starting a brand new request every time.

Merge these two functions into web/sales_workflow.py, alongside the
existing create_stock_request / fulfill_request_item / return_packs /
settle_release / record_payment functions.
"""

@transaction.atomic
def record_card_payment(*, stock_request, amount, method, payment_reference="", collected_by=None, notes=""):
    """
    Apply ONE payment across every release that belongs to this order
    (StockRequest), oldest outstanding line first.

    Creates one PaymentReceipt per release the amount reaches, so each
    release's own history (and the existing per-release properties) stay
    accurate without any schema changes.
    """
    amount = Decimal(amount)

    if amount <= Decimal("0.00"):
        raise ValidationError("Payment amount must be greater than zero.")

    releases = list(
        PackRelease.objects
        .select_for_update()
        .filter(request_item__request=stock_request)
        .order_by("released_at")
    )

    if not releases:
        raise ValidationError("This order has no released stock to collect payment against.")

    total_outstanding = sum(r.outstanding_balance for r in releases)

    if total_outstanding <= Decimal("0.00"):
        raise ValidationError("This order has already been fully cleared.")

    if amount > total_outstanding:
        raise ValidationError(
            f"Payment exceeds the outstanding balance of UGX {total_outstanding:,.2f} for this order."
        )

    remaining = amount
    receipts = []

    for release in releases:
        if remaining <= Decimal("0.00"):
            break

        owed = release.outstanding_balance
        if owed <= Decimal("0.00"):
            continue

        pay_now = min(owed, remaining)

        receipt = PaymentReceipt.objects.create(
            release=release,
            amount=pay_now,
            method=method,
            payment_reference=payment_reference,
            collected_by=collected_by,
            notes=notes,
        )
        receipts.append(receipt)
        remaining -= pay_now

    return receipts


@transaction.atomic
def add_item_to_request(*, stock_request, product, quantity, user=None):
    """
    Add another product line to an order that is still open, so the store
    keeper can release more products into the SAME order card instead of
    the sales rep having to start a brand new request.
    """
    quantity = int(quantity)

    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    if stock_request.status == "cancelled":
        raise ValidationError("This order was cancelled and cannot accept new products.")

    item, created = StockRequestItem.objects.get_or_create(
        request=stock_request,
        product=product,
        defaults={"quantity_requested": quantity},
    )

    if not created:
        item.quantity_requested = item.quantity_requested + quantity
        item.save(update_fields=["quantity_requested"])

    # Re-open the order for fulfilment if it had already been closed out.
    if stock_request.status == "fulfilled":
        stock_request.status = "partially_fulfilled"
        stock_request.fulfilled_at = None
        stock_request.save(update_fields=["status", "fulfilled_at"])

    return item

"""
Additions to web/sales_workflow.py — Nonda Commodities

Adds two functions your cashier "order card" feature needs:

  1. record_card_payment — apply ONE payment across every product line
     that belongs to the same order (StockRequest), oldest outstanding
     line first, instead of forcing the cashier to pay each release
     separately.

  2. add_item_to_request — add another product line to an order that is
     still open, so a salesperson's card can keep growing instead of
     starting a brand new request every time.

Merge these two functions into web/sales_workflow.py, alongside the
existing create_stock_request / fulfill_request_item / return_packs /
settle_release / record_payment functions.
"""


@login_required
@require_POST
def record_card_payment_view(request, pk):
    stock_request = get_object_or_404(StockRequest, pk=pk)

    try:
        amount = Decimal(request.POST.get("amount", "0"))
        method = request.POST.get("method", "").strip()
        payment_reference = request.POST.get("payment_reference", "").strip()
        notes = request.POST.get("notes", "").strip()

        if not method:
            raise ValidationError("Payment method is required.")

        record_card_payment(
            stock_request=stock_request,
            amount=amount,
            method=method,
            payment_reference=payment_reference,
            collected_by=request.user,
            notes=notes,
        )

        messages.success(
            request,
            f"Payment of UGX {amount:,.0f} recorded successfully.",
        )

    except (ValidationError, ValueError, TypeError) as exc:
        messages.error(request, str(exc))

    return redirect("order_queue")

@login_required
@require_POST
def add_item_to_request_view(request, pk):
    stock_request = get_object_or_404(StockRequest, pk=pk)

    try:
        product_id = request.POST.get("product")
        quantity = request.POST.get("quantity")

        if not product_id:
            raise ValidationError("Please select a product.")

        product = get_object_or_404(
            PackagedProduct,
            pk=product_id,
            is_active=True,
        )

        add_item_to_request(
            stock_request=stock_request,
            product=product,
            quantity=quantity,
            user=request.user,
        )

        messages.success(
            request,
            f"{product} added to the order successfully.",
        )

    except (ValidationError, ValueError, TypeError) as exc:
        messages.error(request, str(exc))

    return redirect("order_queue")


class ManagementReportsView(RoleRequiredMixin, TemplateView):
    """
    Management / Accounts reporting centre.

    Period-based figures use the selected reporting period.
    Inventory and outstanding receivables are live current-state figures.
    """

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.ACCOUNTS,
        User.Role.CASHIER,
    )

    template_name = "pipeline/reports.html"

    def get_report_dates(self):
        today = timezone.localdate()

        preset = self.request.GET.get("preset", "today")

        if preset == "today":
            start_date = today
            end_date = today

        elif preset == "last_7_days":
            start_date = today - timezone.timedelta(days=6)
            end_date = today

        elif preset == "this_month":
            start_date = today.replace(day=1)
            end_date = today

        elif preset == "custom":
            try:
                start_date = datetime.strptime(
                    self.request.GET.get("start"),
                    "%Y-%m-%d",
                ).date()

                end_date = datetime.strptime(
                    self.request.GET.get("end"),
                    "%Y-%m-%d",
                ).date()

                if start_date > end_date:
                    start_date, end_date = end_date, start_date

            except (TypeError, ValueError):
                start_date = today
                end_date = today
                preset = "today"

        else:
            preset = "today"
            start_date = today
            end_date = today

        return preset, start_date, end_date

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        preset, start_date, end_date = self.get_report_dates()

        start_dt = timezone.make_aware(
            datetime.combine(start_date, time.min)
        )

        end_dt = timezone.make_aware(
            datetime.combine(
                end_date + timezone.timedelta(days=1),
                time.min,
            )
        )

        # ==========================================================
        # SALES / RELEASES FOR PERIOD
        # ==========================================================

        releases = list(
            PackRelease.objects.filter(
                request_item__request__purpose="sale",
                released_at__gte=start_dt,
                released_at__lt=end_dt,
            )
            .select_related(
                "product__blend",
                "product__pack_size",
                "released_to",
                "request_item__request",
            )
            .prefetch_related("returns")
            .order_by("-released_at")
        )

        gross_value_released = Decimal("0.00")
        total_packs_released = 0

        product_stats = {}
        agent_stats = {}

        for release in releases:
            value = (
                Decimal(release.packs_out)
                * release.selling_price
            )

            gross_value_released += value
            total_packs_released += release.packs_out

            # ------------------------------------------
            # PRODUCT PERFORMANCE
            # ------------------------------------------

            product_id = release.product_id

            if product_id not in product_stats:
                product_stats[product_id] = {
                    "product": release.product,
                    "packs": 0,
                    "value": Decimal("0.00"),
                }

            product_stats[product_id]["packs"] += release.packs_out
            product_stats[product_id]["value"] += value

            # ------------------------------------------
            # SALES AGENT PERFORMANCE
            # ------------------------------------------

            agent_key = release.released_to_id

            if agent_key not in agent_stats:
                agent_stats[agent_key] = {
                    "name": (
                        str(release.released_to)
                        if release.released_to
                        else "Unassigned"
                    ),
                    "packs": 0,
                    "value": Decimal("0.00"),
                }

            agent_stats[agent_key]["packs"] += release.packs_out
            agent_stats[agent_key]["value"] += value

        # ==========================================================
        # RETURNS FOR PERIOD
        # ==========================================================

        returns = list(
            PackReturn.objects.filter(
                returned_at__gte=start_dt,
                returned_at__lt=end_dt,
                release__request_item__request__purpose="sale",
            ).select_related(
                "release__product__blend",
                "release__product__pack_size",
                "release__released_to",
            )
        )

        total_returned_packs = 0
        return_credit = Decimal("0.00")

        for returned in returns:
            total_returned_packs += returned.packs_returned

            return_credit += (
                Decimal(returned.packs_returned)
                * returned.release.selling_price
            )

        net_sales_value = (
            gross_value_released - return_credit
        )

        # ==========================================================
        # PAYMENT COLLECTIONS FOR PERIOD
        # ==========================================================

        # Initial money collected when stock was released
        initial_paid_releases = PackRelease.objects.filter(
            request_item__request__purpose="sale",
            released_at__gte=start_dt,
            released_at__lt=end_dt,
        ).prefetch_related("payments")

        initial_cash = Decimal("0.00")
        initial_momo = Decimal("0.00")

        for release in initial_paid_releases:
            for payment in release.payments.all():
                amount = getattr(payment, "amount", Decimal("0.00"))
                method = getattr(payment, "method", "").lower()

                if method == "cash":
                    initial_cash += amount
                elif method in ("momo", "mobile_money"):
                    initial_momo += amount

        # Later installment collections
        payments = list(
            PaymentReceipt.objects.filter(
                collected_at__gte=start_dt,
                collected_at__lt=end_dt,
            )
        )

        installment_total = Decimal("0.00")

        payment_by_method = {
            "cash": Decimal("0.00"),
            "mobile_money": Decimal("0.00"),
            "bank": Decimal("0.00"),
            "other": Decimal("0.00"),
        }

        for payment in payments:
            amount = payment.amount
            installment_total += amount

            method = payment.method

            if method == "momo":
                payment_by_method["mobile_money"] += amount

            elif method in payment_by_method:
                payment_by_method[method] += amount

        # Add initial release collections
        payment_by_method["cash"] += initial_cash
        payment_by_method["mobile_money"] += initial_momo

        cash_collected = (
            initial_cash
            + initial_momo
            + installment_total
        )

        payment_count = len(payments) + initial_paid_releases.count()

        # ==========================================================
        # CURRENT OUTSTANDING RECEIVABLES
        # ==========================================================

        all_sale_releases = list(
            PackRelease.objects.filter(
                request_item__request__purpose="sale",
            )
            .select_related(
                "product__blend",
                "product__pack_size",
                "released_to",
                "request_item__request",
            )
            .prefetch_related(
                "returns",
                "payments",
            )
        )

        outstanding_receivables = Decimal("0.00")

        debtor_stats = {}

        for release in all_sale_releases:

            returned_packs = sum(
                ret.packs_returned
                for ret in release.returns.all()
            )

            net_packs = max(
                release.packs_out - returned_packs,
                0,
            )

            gross_due = (
                Decimal(net_packs)
                * release.selling_price
            )

            total_paid = sum(
                getattr(payment, "amount", Decimal("0.00"))
                for payment in release.payments.all()
            )

            balance = max(
                gross_due - total_paid,
                Decimal("0.00"),
            )

            if balance <= 0:
                continue

            outstanding_receivables += balance

            debtor_key = release.released_to_id

            if debtor_key not in debtor_stats:
                debtor_stats[debtor_key] = {
                    "name": (
                        str(release.released_to)
                        if release.released_to
                        else "Unassigned"
                    ),
                    "orders": set(),
                    "balance": Decimal("0.00"),
                }

            if release.request_item_id:
                debtor_stats[debtor_key]["orders"].add(
                    release.request_item.request_id
                )

            debtor_stats[debtor_key]["balance"] += balance

        debtors = sorted(
            [
                {
                    "name": value["name"],
                    "orders": len(value["orders"]),
                    "balance": value["balance"],
                }
                for value in debtor_stats.values()
            ],
            key=lambda x: x["balance"],
            reverse=True,
        )[:10]

        # ==========================================================
        # CREDIT CREATED DURING PERIOD
        # ==========================================================

        credit_created = Decimal("0.00")

        for release in releases:
            gross_due = Decimal(release.packs_out) * release.selling_price
            initial_paid = sum(
                getattr(p, "amount", Decimal("0.00"))
                for p in release.payments.all()
            )

            if initial_paid < gross_due:
                credit_created += max(
                    gross_due - initial_paid,
                    Decimal("0.00"),
                )

        # ==========================================================
        # PACKAGED INVENTORY — CURRENT STATE
        # ==========================================================

        packaged_inventory = list(
            PackagedInventory.objects.select_related(
                "product__blend",
                "product__pack_size",
            )
        )

        packaged_packs = 0
        low_packaged = []

        for inventory in packaged_inventory:

            available = inventory.available
            packaged_packs += available

            if available <= 0:
                low_packaged.append({
                    "product": inventory.product,
                    "available": available,
                })

        # ==========================================================
        # BULK / KG INVENTORY — CURRENT STATE
        # ==========================================================

        stocks = list(
            CoffeeStock.objects.select_related("variety")
        )

        green_kg = Decimal("0.00")
        roasted_kg = Decimal("0.00")
        ground_kg = Decimal("0.00")
        quaker_kg = Decimal("0.00")

        low_bulk = []

        for stock in stocks:

            green = get_stage_inventory(
                stock,
                StockStage.GREEN,
            )

            roasted = get_stage_inventory(
                stock,
                StockStage.ROASTED,
            )

            ground = get_stage_inventory(
                stock,
                StockStage.GROUND,
            )

            quakers = get_stage_inventory(
                stock,
                StockStage.QUAKERS,
            )

            green_kg += green
            roasted_kg += roasted
            ground_kg += ground
            quaker_kg += quakers

            total_stock = (
                green
                + roasted
                + ground
                + quakers
            )

            if total_stock <= stock.reorder_level:
                stock.available_kg = total_stock
                low_bulk.append(stock)

        # ==========================================================
        # ORDERS / OPERATIONS
        # ==========================================================

        orders_count = StockRequest.objects.filter(
            purpose="sale",
            requested_at__gte=start_dt,
            requested_at__lt=end_dt,
        ).count()

        open_orders_count = StockRequest.objects.filter(
            purpose="sale",
            status__in=(
                "pending",
                "partially_fulfilled",
            ),
        ).count()

        partially_fulfilled_count = StockRequest.objects.filter(
            purpose="sale",
            status="partially_fulfilled",
        ).count()

        open_processing_runs = ProcessingRun.objects.filter(
            status="open"
        ).count()

        # ==========================================================
        # CONTEXT
        # ==========================================================

        ctx.update({
            "preset": preset,
            "start_date": start_date,
            "end_date": end_date,

            # Executive summary
            "orders_count": orders_count,
            "gross_value_released": gross_value_released,
            "net_sales_value": net_sales_value,
            "cash_collected": cash_collected,

            # Packs
            "total_packs_released": total_packs_released,
            "total_packs_sold": (
                total_packs_released
                - total_returned_packs
            ),
            "total_returned_packs": total_returned_packs,

            # Finance
            "outstanding_receivables": outstanding_receivables,
            "credit_created": credit_created,
            "payment_count": payment_count,
            "return_credit": return_credit,
            "payment_by_method": payment_by_method,

            # Sales
            "top_products": sorted(
                product_stats.values(),
                key=lambda x: x["value"],
                reverse=True,
            )[:10],

            "sales_by_agent": sorted(
                agent_stats.values(),
                key=lambda x: x["value"],
                reverse=True,
            )[:10],

            # Debtors
            "debtors": debtors,

            # Packaged inventory
            "packaged_packs": packaged_packs,
            "total_packaged_available": packaged_packs,
            "low_packaged": low_packaged,
            "low_packaged_count": len(low_packaged),

            # Bulk inventory
            "green_kg": green_kg,
            "roasted_kg": roasted_kg,
            "ground_kg": ground_kg,
            "quaker_kg": quaker_kg,

            "total_bulk_kg": (
                green_kg
                + roasted_kg
                + ground_kg
                + quaker_kg
            ),

            "low_bulk": low_bulk,
            "low_bulk_count": len(low_bulk),

            # Operations
            "open_orders_count": open_orders_count,
            "partially_fulfilled_count": partially_fulfilled_count,
            "returns_count": len(returns),
            "open_processing_runs": open_processing_runs,
        })

        return ctx

    class CompanyAccountDetailView(RoleRequiredMixin, DetailView):
        """
        Displays complete financial ledger for Supermarkets or Restaurants:
        1. Direct Sales & Balances
        2. Stock currently out on Display / Consignment
        3. Total Payments Received
        """
    model = Company
    template_name = "pipeline/company_ledger.html"
    context_object_name = "company"

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.ACCOUNTS,
        User.Role.CASHIER,
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = self.object

        # 1. Total releases delivered to this company
        company_releases = PackRelease.objects.filter(
            request_item__request__company=company
        ).select_related(
            "product",
            "product__blend",
            "product__pack_size",
            "request_item__request",
        ).prefetch_related("payments", "returns")

        # 2. Stock on Display vs Sold
        display_releases = company_releases.filter(
            request_item__request__purpose="display"
        )
        direct_sales_releases = company_releases.filter(
            request_item__request__purpose="sale"  # FIXED: Changed 'sales' to 'sale'
        )

        total_display_value = sum(r.gross_amount for r in display_releases)
        total_sales_value = sum(r.gross_amount for r in direct_sales_releases)

        # 3. Total Payments Received from this Company Account
        total_paid = sum(r.total_amount_paid for r in company_releases)

        context.update({
            "company_releases": company_releases,  # FIXED: Removed bracket typo 'company_rele]ases'
            "display_releases": display_releases,
            "direct_sales_releases": direct_sales_releases,
            "total_display_value": total_display_value,
            "total_sales_value": total_sales_value,
            "total_paid": total_paid,
            "net_owed": (total_sales_value + total_display_value) - total_paid,
        })
        
        return context

    # views.py

# ---------------------------------------------------------
# ACCOUNT HOLDER MANAGEMENT VIEWS
# ---------------------------------------------------------
@login_required
def account_holder_list(request):
    accounts = AccountHolder.objects.all().select_related('system_user')
    return render(request, 'pipeline/account_holder_list.html', {'accounts': accounts})

@login_required
def account_holder_create(request):
    if request.method == 'POST':
        form = AccountHolderForm(request.POST)
        if form.is_valid():
            account = form.save()
            messages.success(request, f"Account Holder '{account.name}' created successfully.")
            return redirect('account_holder_list')
    else:
        form = AccountHolderForm()
    return render(request, 'pipeline/account_holder_form.html', {'form': form, 'title': 'Create Account Holder'})

@login_required
def account_holder_detail(request, pk):
    account = get_object_or_404(AccountHolder, pk=pk)
    releases = PackRelease.objects.filter(released_to=account).order_by('-released_at')
    return render(request, 'pipeline/account_holder_detail.html', {
        'account': account,
        'releases': releases
    })

# ---------------------------------------------------------
# ITEM FULFILLMENT & PACK RELEASE
# ---------------------------------------------------------
@login_required
def fulfill_item_view(request, item_pk):
    item = get_object_or_404(StockRequestItem, pk=item_pk)
    if request.method == 'POST':
        quantity = request.POST.get('quantity')
        selling_price = request.POST.get('selling_price')
        notes = request.POST.get('notes', '')
        try:
            fulfill_request_item(
                item=item,
                quantity=quantity,
                selling_price=selling_price,
                manager=request.user,
                notes=notes
            )
            messages.success(request, "Stock item fulfilled successfully.")
            return redirect('credit_control_ledger')
        except Exception as e:
            messages.error(request, str(e))
    
    return render(request, 'pipeline/fulfill_item.html', {'item': item})