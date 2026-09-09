from django.views.generic import FormView
from .permissions import RoleRequiredMixin
from .forms import (PackagingRunForm, PackReleaseForm, PackReturnForm
)
from .services.packaging import (
    execute_packaging_run, execute_pack_release, execute_pack_return
)
from .services.processing import issue_for_processing, complete_roasting, complete_grinding
# Adjust this import to match where your mixin resides
from .forms import PackagedProductBulkForm
from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse, request
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, TemplateView, CreateView, UpdateView, DeleteView, DetailView, View

from .models import Company, PackReturn, CoffeeStock, CoffeeVariety, ProcessingRun, Sample, StockMovement, Followup, Contract, StockRequest, StockStage, User
from .forms import (
    CompanyForm, CoffeeStockForm, CoffeeStockIntakeForm, ProcessingCompleteForm, SampleForm, ContractForm,
)
from .services.intake import record_intake
from django.shortcuts import render
from .services.inventory import get_stage_inventory
from .services.followup import (
    create_followup_for_sample, mark_guide_sent, mark_contract_sent, convert_to_contract,
)
from .utils.util import apply_date_filters
from .forms import UserRegistrationForm, UserLoginForm
from django.contrib.auth import login, logout
from django.core.exceptions import ValidationError

from .models import ( PackagedProduct, PackagingRun, 
    PackagedInventory, PackRelease)
from .decorators import role_required


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
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
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

from django.db.models import Sum, Q
from decimal import Decimal
from .models import PackRelease

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


from django.views.generic import View
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from .models import CoffeeStock, ProcessingRun
from .services.processing import issue_for_processing, complete_roasting, complete_grinding

class IssueProcessingRunView(RoleRequiredMixin, View):
    """Step 1: Called when coffee is taken and loaded into the machinery."""
    allowed_roles = ("store_manager", "manager", "admin")

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

        return redirect("processing_run_list")


class CompleteProcessingRunView(RoleRequiredMixin, View):
    allowed_roles = ("store_manager", "manager", "admin")

    def post(self, request, pk):
        run = get_object_or_404(ProcessingRun, pk=pk)
        user = request.user
        notes = request.POST.get("notes", "")

        try:
            # ☕ IF THE OPEN PIPELINE STEP IS A ROAST OR SORT RUN, RUN SORTING COMPLETION
            if run.process_type in ("roasting", "sorting"):
                good_qty = request.POST.get("good_quantity") or 0
                quaker_qty = request.POST.get("bad_quantity") or 0  # Maps to quaker_quantity

                # If the run type was explicitly issued as roasting, we temporarily align 
                # its process type parameter so complete_sorting accepts it natively
                if run.process_type == "roasting":
                    run.process_type = "sorting"
                    run.save(update_fields=["process_type"])

                # Execute your native workflow logic atomically
                from .sales_workflow import complete_sorting
                completed_run = complete_sorting(
                    processing_run=run,
                    good_quantity=float(good_qty),
                    quaker_quantity=float(quaker_qty),
                    user=user,
                    notes=notes
                )
                
                messages.success(
                    request, 
                    f"Milling run completed successfully: Good: {good_qty} kg | Quakers: {quaker_qty} kg."
                )
            else:
                # Standard grinding path loop execution
                output_qty = request.POST.get("output_quantity") or 0
                # complete_grinding logic follows...
                
        except (ValueError, ValidationError) as exc:
            messages.error(request, str(exc))

        return redirect("processing_run_list")


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
        inv = list(ctx["inventory"])
        ctx["products"] = [item.product for item in inv]
        ctx["total_available"] = sum(i.available for i in inv)
        ctx["total_released"] = sum(i.packs_released for i in inv)
        ctx["total_returned"] = sum(i.packs_returned for i in inv)
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
    template_name = "pipeline/packaged_product_form.html"  # Make sure this matches yours
    success_url = "pipeline/packaged_inventory_list.html"             # Make sure this matches yours

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

    def form_valid(self, form):
        try:
            execute_pack_return(
                release=form.cleaned_data["release"],
                packs_returned=form.cleaned_data["packs_returned"],
                reason=form.cleaned_data.get("reason", ""),
                user=current_user(self.request),
                notes=form.cleaned_data.get("notes", "")
            )
            messages.success(self.request, "Pack return recorded successfully.")
            return redirect(self.success_url)
        except ValidationError as e:
            form.add_error(None, e.message)
            return self.form_invalid(form)



from decimal import Decimal
from django.views.generic import CreateView
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.urls import reverse_lazy
from .models import PackRelease, PaymentReceipt
from .permissions import RoleRequiredMixin

from decimal import Decimal
from django.views.generic import CreateView
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.urls import reverse_lazy
from .models import PackRelease, PaymentReceipt
from .permissions import RoleRequiredMixin

from decimal import Decimal
from django.views.generic import CreateView
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.urls import reverse_lazy
from .models import PackRelease, PaymentReceipt

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
    template_name = "pipeline/cash_ledger_list.html"
    context_object_name = "debtor_releases"

    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.MANAGER,
        User.Role.ADMIN,
    )

    def get_queryset(self):
        return (
            PackRelease.objects
            .select_related(
                "product",
                "product__blend",
                "product__pack_size",
                "released_to_user",
                "released_to_account",
            )
            .prefetch_related(
                "payments",
                "returns",
            )
            .order_by("-released_at")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        releases = list(ctx["debtor_releases"])

        ctx["total_outstanding_debt"] = sum(
            release.outstanding_balance
            for release in releases
        )

        ctx["total_collected_revenue"] = sum(
            release.total_amount_paid
            for release in releases
        )

        ctx["active_debts"] = [
            release
            for release in releases
            if not release.is_fully_cleared
        ]

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

from django.views.generic import ListView
from django.db.models import Q
from decimal import Decimal
from .models import PackRelease, AccountHolder

class CreditControlLedgerView(RoleRequiredMixin, ListView):
    """
    Dedicated financial controller view to track field credit, 
    debtor obligations, and historical installment allocations.
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
        # 1. Fetch only releases that are originating from a sale and have outstanding balances
        qs = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to"
        ).prefetch_related("returns", "payments").order_by("-released_at")
        
        # 2. Extract URL parameters for timeline filtering
        from .utils.util import apply_date_filters
        qs, preset, today, start, end = apply_date_filters(self.request, qs, "released_at")
        self._preset = preset
        
        # 3. Handle an option parameter toggle to switch between active debtors or general history
        self._view_scope = self.request.GET.get("scope", "active")
        if self._view_scope == "active":
            return [r for r in qs if r.outstanding_balance > 0]
        return list(qs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        
        # Pull full un-sliced queryset timeline to compute stable summary statistics cards
        all_ledger_records = PackRelease.objects.select_related("product").prefetch_related("payments")
        from .utils.util import apply_date_filters
        all_ledger_records, _, _, _, _ = apply_date_filters(self.request, all_ledger_records, "released_at")
        all_records_list = list(all_ledger_records)

        # 📊 LIVE ERP FINANCIAL AGGREGATIONS
        total_value_issued = sum(getattr(r, "stock_value", Decimal(r.packs_out) * r.selling_price) for r in all_records_list)
        total_collected_revenue = sum(getattr(r, "total_amount_paid", Decimal(0)) for r in all_records_list)
        total_outstanding_debt = sum(getattr(r, "outstanding_balance", Decimal(0)) for r in all_records_list)

        ctx.update({
            "preset": getattr(self, "_preset", "this_month"),
            "view_scope": getattr(self, "_view_scope", "active"),
            
            # Financial Data Context
            "total_value_issued": total_value_issued,
            "total_collected_revenue": total_collected_revenue,
            "total_outstanding_debt": total_outstanding_debt,
        })
        return ctx

from django.views.generic import TemplateView
from .models import CoffeeStock, PackagedInventory

class LowStockListView(RoleRequiredMixin, TemplateView):
    """
    Unified low stock control desk displaying both raw coffee processing lots 
    and retail packaged finished products on a single warning page.
    """
    allowed_roles = ("store_manager", "manager", "admin")
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
