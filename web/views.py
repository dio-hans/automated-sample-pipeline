from platform import release

from django.utils.decorators import method_decorator
from datetime import datetime, time, timezone
from django import forms
from django.db.models import F, Q, DecimalField, ExpressionWrapper
from django.db.models.aggregates import Sum
from django.forms.formsets import formset_factory
from django.utils import timezone
from django.contrib import messages

from .services.reporting.export import pdf_bytes

from .services.reporting.finance import get_finance_report
from .services.reporting.executive import get_executive_report
from .services.reporting.operations import get_operations_report
from .services.reporting.sales import get_sales_report
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .services.reporting.consumption import _decimal, get_stock_consumption_report
from .services.reporting.periods import resolve_report_period

from .services.internal_stock import ZERO, create_internal_stock_issue, get_packaged_internal_available

from .sales_workflow import approve_existing_pack_return, fulfill_request_item
from .models import AccountHolder, InternalAccount, InternalStockIssue, PaymentReceipt, StockRequest, PackagedProduct, StockRequestItem, StockStage
from decimal import Decimal
from .services.processing import complete_roasting, complete_sorting
from django.views.generic import TemplateView
from .models import CoffeeStock, PackagedInventory, RoastedSackSale
from django.contrib import messages
from django.contrib.auth import login, logout
from django.core.exceptions import ValidationError
from .services.packaging import execute_pack_return
from django.db import models, transaction
from django.http import HttpResponse, JsonResponse
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
    InternalAccountForm,
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
            username = request.POST.get('username')
            if username:
                try:
                    existing_user = User.objects.get(username=username)
                    if not existing_user.is_active or getattr(existing_user, 'is_suspended', False):
                        messages.error(request, "Access Denied: Your account has been suspended.")
                        return redirect('login')
                except User.DoesNotExist:
                    pass

            form.errors.clear()
            form.add_error(None, "Invalid login credentials.")
    else:
        form = UserLoginForm()
    return render(request, 'pipeline/login.html', {'form': form})

def user_logout(request):
    logout(request)
    messages.info(request, "Session terminated successfully.")
    return redirect('login')


# Staff Profiling and Administrative Actions
def register_user(request):
    if not (request.user.is_superuser or getattr(request.user, "role", None) == User.Role.ADMIN):
        messages.error(request, "You do not have permission to manage users.")
        return redirect("inventory_dashboard")
    
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            new_user = form.save()
            messages.success(request, f"Terminal credentials generated successfully for {new_user.username}!")
            return redirect('register')
        else:
            messages.error(request, "Account registration failed. Verify database constraints.")
    else:
        form = UserRegistrationForm()

    system_users = User.objects.all().order_by('role', 'username')
    return render(request, 'pipeline/registration.html', {'form': form, 'users': system_users})

def toggle_user_status(request, user_id):
    """
    Soft deactivation feature to handle account locks safely.
    Protected explicitly against arbitrary privilege- escalations.
    """
    employee = get_object_or_404(User, id=user_id)
    
    if employee == request.user:
        messages.error(request, "Security Violation Protection: You cannot lock out your own administrative account.")
        return redirect('register')

    # Atomic inversion of status state
    employee.is_active = not employee.is_active
    employee.save()

    status = "activated" if employee.is_active else "suspended"
    
    if employee.is_active:
        messages.success(request, f"Access clearance for {employee.username} successfully restored.")
    else:
        messages.warning(request, f"Terminal operational rights for {employee.username} have been suspended.")
        
    return redirect('register')

# AUTH & USER CONTROL 
@method_decorator(login_required, name='dispatch') 
class InventoryRoleRequiredMixin:
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER, User.Role.ACCOUNTS)
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        if request.user.is_superuser or request.user.role in self.allowed_roles:
            return super().dispatch(request, *args, **kwargs)
        messages.error(request, "You do not have permission to access inventory operations.")
        return redirect("record_sale")  
          
@method_decorator(login_required, name='dispatch')  
class PackagingRunListView(InventoryRoleRequiredMixin, ListView):
    allowed_roles = (User.Role.MANAGER, User.Role.ACCOUNTS)
    model = PackagingRun
    template_name = "pipeline/packaging_run_list.html"
    context_object_name = "packaging_runs"

    def get_queryset(self):
        return PackagingRun.objects.select_related(
            "product__blend", "product__pack_size", "stock"
        ).order_by("-issued_at")
    
@method_decorator(login_required, name='dispatch')
class PackagingRunDetailView(InventoryRoleRequiredMixin, DetailView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.ACCOUNTS)
    model = PackagingRun
    template_name = "pipeline/packaging_run_detail.html"
    context_object_name = "run"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        run = self.object
        kg_per_pack = run.product.kg_per_pack
        ctx["represented_kg"] = Decimal(run.packs_produced or 0) * kg_per_pack
        return ctx

@method_decorator(login_required, name='dispatch')
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
@method_decorator(login_required, name='dispatch')
class PackReleaseListView(InventoryRoleRequiredMixin, ListView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER, User.Role.ACCOUNTS)
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



@method_decorator(login_required, name='dispatch')
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

@method_decorator(login_required, name='dispatch')
class PackReleaseCreateView(InventoryRoleRequiredMixin, CreateView):
    allowed_roles = ( User.Role.ACCOUNTS)
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


from datetime import datetime, time
from django.utils import timezone

from datetime import datetime, time
from django.db.models import Q
from django.utils import timezone

from django.db.models import F, Sum, DecimalField, ExpressionWrapper
from django.utils import timezone
from datetime import datetime, time
# ... your other imports ...

from django.db.models import F, Sum, DecimalField, ExpressionWrapper
from django.utils import timezone
from datetime import datetime, time

from datetime import datetime, time
from django.db.models import F, Q, Sum, DecimalField, ExpressionWrapper
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import ListView

@method_decorator(login_required, name='dispatch')
class PackReturnListView(InventoryRoleRequiredMixin, ListView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER, User.Role.ACCOUNTS)
    model = PackReturn
    template_name = "pipeline/pack_return_list.html"
    context_object_name = "returns"

    def get_queryset(self):
        qs = PackReturn.objects.select_related(
            "release__product__blend",
            "release__product__pack_size",
            "release__released_to",  # <-- Added: Fetches person assigned to the release
            "received_by"
        )
        today = timezone.localdate()

        # Safely extract preset or range from GET query params
        selected_range = (
            self.request.GET.get("preset") or 
            self.request.GET.get("range") or 
            "today"
        ).strip()

        # Default fallbacks to avoid UnboundLocalError
        start_date, end_date = None, None

        if selected_range == "today":
            start_date, end_date = today, today

        elif selected_range == "yesterday":
            yesterday = today - timezone.timedelta(days=1)
            start_date, end_date = yesterday, yesterday

        elif selected_range == "this_week":
            # Monday of the current week through today
            start_date = today - timezone.timedelta(days=today.weekday())
            end_date = today

        elif selected_range in ["last_7_days", "last_7"]:
            start_date = today - timezone.timedelta(days=6)
            end_date = today

        elif selected_range == "this_month":
            start_date = today.replace(day=1)
            end_date = today

        elif selected_range == "custom":
            try:
                s_str = self.request.GET.get("start_date")
                e_str = self.request.GET.get("end_date")
                if s_str and e_str:
                    start_date = datetime.strptime(s_str, "%Y-%m-%d").date()
                    end_date = datetime.strptime(e_str, "%Y-%m-%d").date()
                    if start_date > end_date:
                        start_date, end_date = end_date, start_date
                else:
                    start_date, end_date = today, today
            except (TypeError, ValueError):
                start_date, end_date = today, today

        # 'all' range leaves start_date and end_date as None

        # Apply date range filtering
        if start_date and end_date:
            start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
            end_dt = timezone.make_aware(datetime.combine(end_date + timezone.timedelta(days=1), time.min))
            qs = qs.filter(returned_at__gte=start_dt, returned_at__lt=end_dt)

        # Apply product/pack search filter
        query = self.request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(
                Q(release__product__blend__name__icontains=query) |
                Q(release__product__pack_size__name__icontains=query)
            )

        return qs.order_by("-returned_at", "-id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()

        # Compute total monetary value of filtered returned packs
        total_val = qs.aggregate(
            total=Sum(
                ExpressionWrapper(
                    F("packs_returned") * F("release__selling_price"),
                    output_field=DecimalField()
                )
            )
        )["total"] or 0

        selected_range = (
            self.request.GET.get("preset") or 
            self.request.GET.get("range") or 
            "today"
        ).strip()

        ctx["total_returns_value"] = total_val
        ctx["selected_range"] = selected_range
        ctx["preset"] = selected_range
        return ctx
    

def current_user(request):
    return request.user if request.user.is_authenticated else None


def dashboard_router(request):
    if not request.user.is_authenticated:
        return redirect("login")
    return redirect_user_by_role(request.user)


# COMPANIES
@method_decorator(login_required, name='dispatch')
class CompanyListView(ListView):
    allowed_roles =(User.Role.ACCOUNTS)
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

@method_decorator(login_required, name='dispatch')
class CompanyDetailView(DetailView):
    model = Company
    template_name = "pipeline/company_detail.html"
    context_object_name = "company"

@method_decorator(login_required, name='dispatch')
class CompanyCreateView(CreateView):
    allowed_roles = (User.Role.ACCOUNTS,)
    model = Company
    form_class = CompanyForm
    template_name = "pipeline/company_form.html"
    success_url = reverse_lazy("company_list")

@method_decorator(login_required, name='dispatch')
class CompanyUpdateView(UpdateView):
    allowed_roles = (User.Role.ACCOUNTS )
    model = Company
    form_class = CompanyForm
    template_name = "pipeline/company_form.html"
    success_url = reverse_lazy("company_list")

@method_decorator(login_required, name='dispatch')
class CompanyDeleteView(DeleteView):
    allowed_roles =(User.Role.ACCOUNTS)
    model = Company
    template_name = "pipeline/company_confirm_delete.html"
    success_url = reverse_lazy("company_list")


# COFFEE STOCK

from django.views.generic import ListView
from .models import CoffeeStock

@method_decorator(login_required, name='dispatch')
class CoffeeStockListViews(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.ACCOUNTS)
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



@method_decorator(login_required, name='dispatch')
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

@method_decorator(login_required, name='dispatch')
class CoffeeStockCreateView(VarietyDatalistMixin, CreateView):
    allowed_roles =(User.Role.MANAGER)
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

@method_decorator(login_required, name='dispatch')
class CoffeeStockUpdateView(VarietyDatalistMixin, UpdateView):
    allowed_roles =(User.Role.MANAGER)
    model = CoffeeStock
    form_class = CoffeeStockForm
    template_name = "pipeline/stock_form.html"
    success_url = reverse_lazy("stock_list")

# ===================== SAMPLES =====================
@method_decorator(login_required, name='dispatch')
class SampleListView(ListView):
    allowed_roles =(User.Role.ACCOUNTS)
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

@method_decorator(login_required, name='dispatch')
class SampleDetailView(DetailView):
    allowed_roles =(User.Role.ACCOUNTS)
    model = Sample
    template_name = "pipeline/sample_detail.html"
    context_object_name = "sample"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["followup"], _ = Followup.objects.get_or_create(sample=self.object)
        return ctx

@method_decorator(login_required, name='dispatch')
class SampleCreateView(CreateView):
    allowed_roles =(User.Role.ACCOUNTS)
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

@method_decorator(login_required, name='dispatch')
class SampleUpdateView(UpdateView):
    model = Sample
    form_class = SampleForm
    template_name = "pipeline/sample_form.html"
    success_url = reverse_lazy("sample_list")
    allowed_roles =(User.Role.ACCOUNTS)

@method_decorator(login_required, name='dispatch')
class SampleDeleteView(DeleteView):
    model = Sample
    template_name = "pipeline/sample_confirm_delete.html"
    success_url = reverse_lazy("sample_list")
    allowed_roles =(User.Role.ACCOUNTS)


# ===================== FOLLOW-UPS =====================
@method_decorator(login_required, name='dispatch')
class FollowupListView(ListView):
    model = Followup
    template_name = "pipeline/followup_list.html"
    context_object_name = "followups"
    allowed_roles =(User.Role.ACCOUNTS)

    def get_queryset(self):
        return (Followup.objects
                .select_related("sample__company", "sample__coffee_stock__variety")
                .order_by("-created_at"))

@method_decorator(login_required, name='dispatch')
class FollowupDetailView(DetailView):
    model = Followup
    template_name = "pipeline/followup_detail.html"
    context_object_name = "followup"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if not self.object.is_converted_to_contract:
            ctx["contract_form"] = ContractForm()
        return ctx

@method_decorator(login_required, name='dispatch')
class MarkGuideSentView(View):
    def post(self, request, pk):
        followup = get_object_or_404(Followup, pk=pk)
        mark_guide_sent(followup)
        messages.success(request, "Day-3 guide marked as sent.")
        return redirect("followup_detail", pk=followup.pk)

@method_decorator(login_required, name='dispatch')
class MarkContractSentView(View):
    allowed_roles =(User.Role.ACCOUNTS)
    def post(self, request, pk):
        followup = get_object_or_404(Followup, pk=pk)
        mark_contract_sent(followup)
        messages.success(request, "Day-7 contract prompt marked as sent.")
        return redirect("followup_detail", pk=followup.pk)

@method_decorator(login_required, name='dispatch')
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
@method_decorator(login_required, name='dispatch')
class ContractListView(ListView):
    model = Contract
    template_name = "pipeline/contract_list.html"
    context_object_name = "contracts"
    allowed_roles =(User.Role.ACCOUNTS)

    def get_queryset(self):
        qs = Contract.objects.select_related("company", "sample").order_by("-signed_at")
        qs, preset, today, start, end = apply_date_filters(self.request, qs, "signed_at")
        self._preset = preset
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["preset"] = getattr(self, "_preset", "this_month")
        return ctx

@method_decorator(login_required, name='dispatch')
class ContractDetailView(DetailView):
    model = Contract
    template_name = "pipeline/contract_detail.html"
    context_object_name = "contract"
    allowed_roles =(User.Role.ACCOUNTS)


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

from .utils.util import apply_date_filters

@method_decorator(login_required, name='dispatch')
class StockMovementListView(ListView):
    model = StockMovement
    template_name = "pipeline/stock_movement_list.html"
    context_object_name = "movements"
    paginate_by = 50
    allowed_roles =(User.Role.ACCOUNTS, User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER)

    def get_queryset(self):
        queryset = (
            StockMovement.objects
            .select_related("stock__variety", "created_by")
            .order_by("-created_at")
        )

        queryset, preset, today, start, end = apply_date_filters(
            self.request,
            queryset,
            "created_at",
        )

        self._preset = preset
        self._start = start
        self._end = end

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        preset = getattr(self, "_preset", "this_month")
        start = getattr(self, "_start", None)
        end = getattr(self, "_end", None)

        context["preset"] = preset
        context["start_date"] = start
        context["end_date"] = end

        # The shared report filter expects period.start_date/end_date
        context["period"] = {
            "start_date": start,
            "end_date": end,
        }

        return context

@method_decorator(login_required, name='dispatch')
class DashboardView(TemplateView):
    template_name = "pipeline/dashboard.html"
    allowed_roles =(User.Role.ACCOUNTS, User.Role.MANAGER, User.Role.ADMIN)

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
@method_decorator(login_required, name='dispatch')
class ProcessingWorkspaceView(RoleRequiredMixin, View):
    allowed_roles = (
        User.Role.MANAGER,
        User.Role.ADMIN
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

@method_decorator(login_required, name='dispatch')
class IssueProcessingRunView(RoleRequiredMixin, View):
    """Step 1: Called when coffee is taken and loaded into the machinery."""
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)

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

@method_decorator(login_required, name='dispatch')
class CompleteProcessingRunView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)

    def post(self, request, pk):
        run = get_object_or_404(ProcessingRun.objects.select_related("stock"), pk=pk)
        user = request.user
        notes = request.POST.get("notes", "")

        try:
            if run.process_type == "roasting":
                # Roasting outputs bulk roasted coffee; roasting loss (chaff/moisture) is calculated automatically
                output_qty = request.POST.get("output_quantity") or request.POST.get("good_quantity") or "0"
                
                completed_run, roasting_loss = complete_roasting(
                    processing_run=run,
                    output_quantity=output_qty,
                    user=user,
                    notes=notes,
                )
                messages.success(
                    request,
                    f"Roasting complete: {completed_run.output_quantity} kg roasted output registered "
                    f"({roasting_loss:.2f} kg roasting loss)."
                )

            elif run.process_type == "sorting":
                # Sorting is where quakers (bad beans) are separated from good roasted coffee
                good_qty = request.POST.get("good_quantity") or request.POST.get("output_quantity") or "0"
                quaker_qty = request.POST.get("quaker_quantity") or request.POST.get("bad_quantity") or "0"
                
                completed_run = complete_sorting(
                    processing_run=run,
                    good_quantity=good_qty,
                    quaker_quantity=quaker_qty,
                    user=user,
                    notes=notes,
                )
                messages.success(
                    request, 
                    f"Sorting complete: {completed_run.output_quantity} kg good coffee & "
                    f"{completed_run.secondary_output_quantity} kg quakers registered."
                )

            elif run.process_type == "grinding":
                output_qty = request.POST.get("output_quantity") or "0"
                completed_run, _ = complete_grinding(
                    processing_run=run,
                    output_quantity=output_qty,
                    user=user,
                    notes=notes,
                )
                messages.success(
                    request, 
                    f"Grinding complete: {completed_run.output_quantity} kg ground coffee produced."
                )

        except Exception as exc:
            messages.error(request, str(exc))

        return redirect("processing_workspace")

# PACKAGED INVENTORY VIEWS
@method_decorator(login_required, name='dispatch')
class PackagedInventoryListView(InventoryRoleRequiredMixin, ListView):
    model = PackagedInventory
    template_name = "pipeline/packaged_inventory_list.html"
    context_object_name = "inventory"
    allowed_roles =(User.Role.ACCOUNTS, User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER)

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

@method_decorator(login_required, name='dispatch')
class PackagedProductDetailView(InventoryRoleRequiredMixin, DetailView):
    model = PackagedProduct
    template_name = "pipeline/packaged_product_detail.html"
    context_object_name = "product"
    allowed_roles =(User.Role.ACCOUNTS, User.Role.MANAGER, User.Role.ADMIN)

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



@method_decorator(login_required, name='dispatch')
class PackagedProductCreateView(InventoryRoleRequiredMixin, FormView):
    # 1. Cleanly assign the class type here
    form_class = PackagedProductBulkForm
    template_name = "pipeline/packaged_product_form.html"  
    success_url = reverse_lazy("packaged_inventory_list")
    allowed_roles =(User.Role.MANAGER, User.Role.ADMIN)  

    # 2. Modify the form instance dynamically before it goes to the template
    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        
        #  FIXED: Loop dynamically through every actual field name in the form
        for name, field in form.fields.items():
            field.widget.attrs.update({
                'class': 'w-full rounded-md border border-[#E4DECB] px-3 py-2.5 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-rust'
            })
                    
        return form
  


from decimal import Decimal
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.views.generic import TemplateView, View
from django.db import transaction
from django.core.exceptions import ValidationError
from web.models import StockRequest, PackRelease, PackReturn

class SingleItemReturnForm(forms.Form):
    release_id = forms.IntegerField(widget=forms.HiddenInput())
    packs_returned = forms.IntegerField(
        min_value=0, 
        required=True,
        widget=forms.NumberInput(attrs={'value': 0})
    )
    condition = forms.ChoiceField(
        choices=(("good", "Good Condition"), ("damaged", "Damaged Pack")),
        initial="good"
    )

# Open web/views.py and update these specific methods inside PackReturnCreateView:

ReturnItemFormSet = formset_factory(SingleItemReturnForm, extra=0)

@method_decorator(login_required, name='dispatch')
class PackReturnCreateView(InventoryRoleRequiredMixin, TemplateView):
    template_name = "pipeline/pack_return_form.html"
    allowed_roles = (User.Role.CASHIER, User.Role.ADMIN, User.Role.ACCOUNTS)

    def get_release_from_url(self):
        release = get_object_or_404(
            PackRelease.objects.select_related(
                "product__blend",
                "product__pack_size",
                "released_to",
                "request_item__request",
                "branch",
            ),
            pk=self.kwargs["pk"],
        )

    # Consignment/display stock must never use the ordinary
    # warehouse return workflow.
        if release.purpose == "display":
            raise PermissionDenied(
                "Consignment stock must be returned through the "
                "verified branch consignment return workflow."
            )

        return release

    def get_order(self):
        release = self.get_release_from_url()
        if release.request_item_id and release.request_item.request_id:
            return release.request_item.request
        raise ValidationError(
            "This release batch is not tied to a valid master Stock Request order."
        )

    def _get_releases(self, order):
        return list(
            PackRelease.objects.filter(request_item__request=order)
            .select_related("product__blend", "product__pack_size", "released_to")
            .order_by("product__blend__name", "product__pack_size__grams", "pk")
        )
    

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = self.get_order()
        releases = self._get_releases(order)
        active_releases = [release for release in releases if release.packs_returnable > 0]

        initial_data = [
            {
                "release_id": release.pk,
                "packs_returned": 0,
                "condition": "good",
            }
            for release in active_releases
        ]

        if self.request.method == "POST":
            formset = ReturnItemFormSet(self.request.POST)
        else:
            formset = ReturnItemFormSet(initial=initial_data)

        context.update(
            {
                "order": order,
                "formset": formset,
                "form_release_pairs": zip(formset, active_releases),
                "active_releases": active_releases,
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        """
        🎯 PROCESS CUSTOM TEMPLATE FIELDS DIRECTLY (No Formset Required)
        """
        order = self.get_order()
        releases = self._get_releases(order)
        release_map = {str(release.pk): release for release in releases}
        
        # 1. Fetch the list of checked row IDs from the checklist checkboxes
        selected_ids = request.POST.getlist("selected_items")
        selections = []
        
        # 2. Extract values directly using your exact HTML field names
        for r_id in selected_ids:
            release = release_map.get(str(r_id))
            if not release:
                continue
                
            # Read your custom HTML input names: qty_XX and condition_XX
            qty_raw = request.POST.get(f"qty_{r_id}", "0")
            condition_raw = request.POST.get(f"condition_{r_id}", "good")
            
            try:
                qty = int(qty_raw)
            except (ValueError, TypeError):
                qty = 0
                
            if qty <= 0:
                messages.error(request, f"Please enter a valid return quantity for {release.product.blend.name}.")
                return self.render_to_response(self.get_context_data())
                
            max_returnable = release.packs_returnable
            if qty > max_returnable:
                messages.error(request, f"Only {max_returnable} packs of {release.product.blend.name} are currently eligible for return.")
                return self.render_to_response(self.get_context_data())
                
            # Pack payload exactly how your confirmation and review views expect them
            selections.append({
                "release_id": release.pk,
                "product": f"{release.product.blend.name} ({release.product.pack_size.label})",
                "quantity": qty,
                "condition": condition_raw,
                "max": max_returnable,
                "unit_price": str(release.selling_price),
                "value": str(qty * release.selling_price),
            })
            
        if not selections:
            messages.error(request, "Select at least one unsold product that has physically returned to the warehouse.")
            return self.render_to_response(self.get_context_data())
            
        # 3. Cache the verified payload into the session and redirect cleanly to Stage 2
        import uuid
        token = uuid.uuid4().hex
        request.session[f"return_review:{token}"] = {
            "order_id": order.pk,
            "selections": selections,
            "submitted_by": request.user.pk,
        }
        request.session.modified = True
        
        return redirect("pack_return_confirm", token=token)


@method_decorator(login_required, name='dispatch')
class PackReturnConfirmationView(InventoryRoleRequiredMixin, TemplateView):
    template_name = "pipeline/pack_return_confirmation.html"
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)

    def get_payload(self, token):
        payload = self.request.session.get(f"return_review:{token}")
        if not payload:
            raise ValidationError(
                "This return review has expired. Please build the return worksheet again."
            )
        return payload

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        payload = self.get_payload(self.kwargs["token"])
        order = get_object_or_404(StockRequest, pk=payload["order_id"])
        context.update(
            {
                "order": order,
                "token": self.kwargs["token"],
                "selections": payload["selections"],
                "total_packs": sum(item["quantity"] for item in payload["selections"]),
                "total_value": sum(
                    (Decimal(item["value"]) for item in payload["selections"]),
                    Decimal("0.00"),
                ),
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        token = self.kwargs["token"]
        payload = self.get_payload(token)

        if request.POST.get("action") == "cancel":
            request.session.pop(f"return_review:{token}", None)
            return redirect(
                "pack_return_form",
                pk=self._first_release_for_order(payload["order_id"]),
            )

        if request.POST.get("action") != "confirm":
            return redirect("pack_return_confirm", token=token)

        order_id = payload["order_id"]
        selections = payload["selections"]

        with transaction.atomic():
            for item in selections:
                release = get_object_or_404(
                    PackRelease.objects.select_for_update(),
                    pk=item["release_id"],
                    request_item__request_id=order_id,
                )

                if release.purpose == "display":
                    raise PermissionDenied(
                        "Display/consignment stock cannot be returned through "
                        "the ordinary warehouse return workflow."
                    )

                # Revalidate against sold + approved + pending quantities immediately
                # before creating the pending row.
                if item["quantity"] > release.packs_returnable:
                    raise ValidationError(
                        f"The returnable quantity for {release.product} changed. Re-open the worksheet."
                    )

                PackReturn.objects.create(
                    release=release,
                    packs_returned=int(item["quantity"]),
                    condition=item["condition"],
                    disposition="accepted",
                    status="pending_approval",
                    submitted_by=request.user,
                    returned_at=timezone.now(),
                    reason="Un-sold stock returned to warehouse",
                    notes="Submitted from return confirmation worksheet.",
                )

        request.session.pop(f"return_review:{token}", None)
        messages.success(
            request,
            "Return submitted for approval. No stock or account balance has changed yet.",
        )
        return redirect("pending_return_approvals")

    @staticmethod
    def _first_release_for_order(order_id):
        release = (
            PackRelease.objects.filter(request_item__request_id=order_id)
            .order_by("pk")
            .first()
        )
        return release.pk if release else None

@method_decorator(login_required, name='dispatch')
class PackReturnApproveView(InventoryRoleRequiredMixin, View):
    allowed_roles = (
        User.Role.MANAGER,
        User.Role.ADMIN,
    )

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        return_item = get_object_or_404(PackReturn, pk=pk)
        action = request.POST.get("action")

        try:
            if action == "approve":
                # Ensure the helper gets called cleanly
                approve_existing_pack_return(return_item=return_item, user=request.user)
                messages.success(
                    request,
                    f"Return #{return_item.pk} approved. Stock restored to warehouse.",
                )
            elif action == "reject":
                locked = PackReturn.objects.select_for_update().get(pk=return_item.pk)
                if locked.status != "pending_approval":
                    raise ValidationError("This return has already been processed.")
                
                locked.status = "rejected"
                locked.approved_by = request.user
                locked.approved_at = timezone.now()
                locked.rejection_reason = request.POST.get("rejection_reason", "Rejected by manager.")
                locked.save(
                    update_fields=[
                        "status",
                        "approved_by",
                        "approved_at",
                        "rejection_reason",
                    ]
                )
                messages.warning(request, f"Return #{return_item.pk} rejected.")
            else:
                messages.error(request, "Invalid action submitted.")

        except ValidationError as exc:
            # Clean extraction of ValidationError messages
            error_msg = exc.messages[0] if hasattr(exc, 'messages') else str(exc)
            messages.error(request, error_msg)
        except Exception as e:
            messages.error(request, f"An unexpected error occurred: {str(e)}")

        return redirect("pending_return_approvals")  

@method_decorator(login_required, name='dispatch')
class PendingReturnApprovalListView(InventoryRoleRequiredMixin, ListView):
    template_name = "pipeline/pack_return_approval_list.html"
    context_object_name = "pending_returns"
    allowed_roles = (
        User.Role.MANAGER,
        User.Role.CASHIER,
        User.Role.ADMIN,
    )

    def get_queryset(self):
        return (
            PackReturn.objects
            .filter(status="pending_approval")
            .exclude(release__purpose="display")
            .select_related(
                "release__product__blend",
                "release__product__pack_size",
                "release__released_to",
                "release__request_item__request__company",
                "submitted_by",
            )
            .order_by("returned_at")
        )

@method_decorator(login_required, name='dispatch')
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
        with transaction.atomic():
            release = get_object_or_404(
                PackRelease.objects.select_for_update(), pk=self.kwargs['pk']
            )
            payment = form.save(commit=False)
            payment.release = release
            payment.collected_by = self.request.user
            balance = release.outstanding_balance
            if payment.amount <= 0:
                form.add_error('amount', 'Payment must be greater than zero.')
                return self.form_invalid(form)
            if payment.amount > balance:
                form.add_error('amount', f'Maximum outstanding balance: UGX {balance:,.0f}.')
                return self.form_invalid(form)
            payment.save()
        messages.success(self.request, f'Installment of UGX {payment.amount:,.0f} recorded.')
        return redirect(self.success_url)


@method_decorator(login_required, name='dispatch')
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

@method_decorator(login_required, name='dispatch')
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
        return redirect("consignment_list")


from decimal import Decimal
from django.db.models import F, Sum, ExpressionWrapper, DecimalField
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import ListView



@method_decorator(login_required, name='dispatch')
class CreditControlLedgerView(RoleRequiredMixin, ListView):
    model = PackRelease
    template_name = "pipeline/credit_control_ledger.html"
    context_object_name = "releases"
    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.ADMIN,
    )

    def get_queryset(self):
        qs = (
            PackRelease.objects
            .select_related("product__blend", "product__pack_size", "released_to")
            .prefetch_related("returns", "payments")
            .order_by("-released_at")
        )

        # Apply date filters for table list
        qs, preset, today, start, end = apply_date_filters(
            self.request,
            qs,
            "released_at",
        )

        self._preset = preset
        self._start = start
        self._end = end
        self._view_scope = self.request.GET.get("scope", "active")

        if self._view_scope == "active":
            return [
                release for release in qs
                if getattr(release, "outstanding_balance", Decimal("0.00")) > Decimal("0.00")
            ]
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. TOTAL VALUE DISPATCHED IN SELECTED PERIOD
        dispatched_qs, preset, today, start, end = apply_date_filters(
            self.request,
            PackRelease.objects.all(),
            "released_at",
        )

        total_value_issued = dispatched_qs.aggregate(
            total=Sum(
                ExpressionWrapper(
                    F("packs_out") * F("selling_price"),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )
        )["total"] or Decimal("0.00")

        # 2. LIQUID CASH COLLECTED IN SELECTED PERIOD
        payments_qs, _, _, _, _ = apply_date_filters(
            self.request,
            PaymentReceipt.objects.all(),
            "collected_at",
        )

        total_collected_revenue = (
            payments_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        )

        # 3. TOTAL OUTSTANDING DEBT (GLOBAL / ALL TIME)
        all_releases = PackRelease.objects.prefetch_related("returns", "payments").all()
        total_outstanding_debt = sum(
            (
                release.outstanding_balance
                for release in all_releases
                if getattr(release, "outstanding_balance", Decimal("0.00")) > Decimal("0.00")
            ),
            Decimal("0.00"),
        )

        # CONTEXT MAPPING MATCHING TEMPLATE EXPECTATIONS
        ctx.update({
            "preset": preset,
            "period": {
                "start_date": start,
                "end_date": end,
            },
            "view_scope": getattr(self, "_view_scope", "active"),
            "total_value_issued": total_value_issued,
            "total_collected_revenue": total_collected_revenue,
            "total_outstanding_debt": total_outstanding_debt,
        })
        return ctx


@method_decorator(login_required, name='dispatch')
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

from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
# Ensure you import PackSettlement at the top of your file if it isn't there!
from web.models import PackRelease, PackSettlement 

from decimal import Decimal
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.db import transaction
# Ensure you import your models accurately
from web.models import StockRequest, PackRelease, PackSettlement 

# =========================================================================
# ⚙️ FUNCTION 1: THE CORE DATABASE CALCULATION (Keyword-only arguments)
# =========================================================================
@transaction.atomic
def record_card_payment(
    *,
    stock_request,
    amount,
    method,
    payment_reference="",
    collected_by=None,
    notes="",
):
    """
    Core database logic: Splits one payment across an order's releases.
    """
    amount = Decimal(str(amount))

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
    settlements = []

    for release in releases:
        if remaining <= Decimal("0.00"):
            break

        owed = release.outstanding_balance
        if owed <= Decimal("0.00"):
            continue

        pay_now = min(owed, remaining)

        # Creates a PackSettlement row so the dashboard cards immediately decrease
        settlement = PackSettlement.objects.create(
            release=release,
            packs_sold=release.billable_quantity,
            amount_paid=pay_now,
            payment_method=method,
            payment_reference=payment_reference,
            status="cleared" if pay_now == owed else "partial",
            cleared_by=collected_by,
            notes=notes,
        )
        settlements.append(settlement)
        remaining -= pay_now

    return settlements


# =========================================================================
# 🌐 FUNCTION 2: THE WEB WRAPPER (Accepts HTTP Request and URL parameters)
# =========================================================================
@login_required
@require_POST
def record_card_payment_view(request, pk):
    """
    Web View: Catches the browser form request, extracts data, and calls database logic.
    """
    stock_request = get_object_or_404(StockRequest, pk=pk)

    try:
        amount = Decimal(request.POST.get("amount", "0"))
        method = request.POST.get("method", "").strip()
        payment_reference = request.POST.get("payment_reference", "").strip()
        notes = request.POST.get("notes", "").strip()

        if not method:
            raise ValidationError("Payment method is required.")

        # Calls the database logic function cleanly
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
    except Exception as exc:
        messages.error(request, f"Failed to record payment: {str(exc)}")

    return redirect("order_queue")


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

@method_decorator(login_required, name='dispatch')
class ManagementReportsView(RoleRequiredMixin, TemplateView):
    """
    Management / Accounts reporting centre.

    Period-based figures use the selected reporting period.
    Inventory and outstanding receivables are live current-state figures.
    """

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.ACCOUNTS
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

@method_decorator(login_required, name='dispatch')
class CompanyAccountDetailView(RoleRequiredMixin, DetailView):
    """
    Displays complete financial ledger for Supermarkets or Restaurants:
    1. Direct Sales & Balances
    2. Stock currently out on Display / Consignment
    3. Total Payments Received
    """
model = Company
template_name = "pipeline/company_list.html"
context_object_name = "company"

allowed_roles = (
    User.Role.ADMIN,
    User.Role.ACCOUNTS,
    User.Role.CASHIER,
)

def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    company = Company.objects.first() 
    context["company"] = company
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

from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from django.shortcuts import get_object_or_404, render
from django.contrib.auth.decorators import login_required

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, F, ExpressionWrapper, DecimalField

from .models import AccountHolder, PackRelease
from .utils.util import apply_date_filters  # Adjust import path if needed


@login_required
def account_holder_detail(request, pk):
    account = get_object_or_404(AccountHolder, pk=pk)

    # 1. Base QuerySet
    releases = PackRelease.objects.filter(released_to=account).select_related(
        'product__blend', 
        'product__pack_size'
    ).order_by('-released_at')

    # 2. Apply Date Filter on 'released_at'
    releases, preset, today, start, end = apply_date_filters(
        request, 
        releases, 
        date_field="released_at"
    )

    # 3. Calculate total gross value directly in SQL on filtered queryset
    value_aggregate = releases.aggregate(
        total_value=Sum(
            ExpressionWrapper(
                F('packs_out') * F('selling_price'),
                output_field=DecimalField()
            )
        )
    )
    total_value = value_aggregate['total_value'] or 0

    # 4. Calculate total_paid and total_outstanding across filtered releases
    total_paid = sum(r.total_amount_paid for r in releases)
    total_outstanding = sum(r.outstanding_balance for r in releases)

    # 5. Range label for summary cards
    labels = {
        "today": "Today",
        "yesterday": "Yesterday",
        "this_week": "This Week",
        "last_7_days": "Last 7 Days",
        "this_month": "This Month",
        "overall": "All Time",
    }
    if preset == "custom" and start and end:
        range_label = f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"
    else:
        range_label = labels.get(preset, "All Time")

    context = {
        'account': account,
        'releases': releases,
        'total_value': total_value,
        'total_paid': total_paid,
        'total_outstanding': total_outstanding,
        # Required by report_filters.html
        'preset': preset,
        'period': {
            'start_date': start,
            'end_date': end,
        },
        'selected_range_label': range_label,
    }
    return render(request, 'pipeline/account_holder_detail.html', context)

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

    # company expenses
    from decimal import Decimal
from datetime import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import ListView, TemplateView

from .forms import ExpenseForm
from .models import Expense, User


@method_decorator(login_required, name="dispatch")
class ExpenseTrackerView(RoleRequiredMixin, TemplateView):
    template_name = "pipeline/expense_tracker.html"
    allowed_roles = (
        User.Role.ADMIN,
        User.Role.ACCOUNTS,
    )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if "form" not in ctx:
            ctx["form"] = ExpenseForm(
                initial={
                    "expense_date": timezone.localdate()
                }
            )
        return ctx

    def post(self, request, *args, **kwargs):
        form = ExpenseForm(request.POST)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.logged_by = request.user
            expense.save()
            messages.success(
                request,
                f"Expense of UGX {expense.amount:,.0f} logged successfully under "
                f"'{expense.get_category_display()}'."
            )
            return redirect("expense_tracker")
            
        return self.render_to_response({"form": form})


from datetime import datetime
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
from django.views.generic import ListView

from .models import Expense, User
from .utils.util import apply_date_filters


@method_decorator(login_required, name="dispatch")
class ExpenseListView(RoleRequiredMixin, ListView):
    model = Expense
    template_name = "pipeline/expense_list.html"
    context_object_name = "expenses"
    paginate_by = 20
    allowed_roles = (
        User.Role.ADMIN,
        User.Role.ACCOUNTS,
    )

    def get_queryset(self):
        qs = (
            Expense.objects
            .select_related("logged_by")
            .order_by("-expense_date", "-created_at")
        )

        # 1. Apply Date Filtering using utils.py on 'expense_date'
        qs, preset, today, start, end = apply_date_filters(
            self.request,
            qs,
            date_field="expense_date",
        )

        # 2. Optional Category Filter
        category_filter = self.request.GET.get("category")
        if category_filter:
            qs = qs.filter(category=category_filter)

        # Store for context calculation
        self._preset = preset
        self._start = start
        self._end = end

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Aggregate Total Expenses across the entire filtered queryset
        filtered_qs = self.get_queryset()
        total_expenses = filtered_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        preset = getattr(self, "_preset", "this_month")
        start = getattr(self, "_start", None)
        end = getattr(self, "_end", None)

        # Generate human-readable range label
        labels = {
            "today": "Today",
            "yesterday": "Yesterday",
            "this_week": "This Week",
            "last_7_days": "Last 7 Days",
            "this_month": "This Month",
            "overall": "All Time",
        }
        if preset == "custom" and start and end:
            range_label = f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"
        else:
            range_label = labels.get(preset, "All Time")

        # Category choices for filter dropdown
        categories = getattr(Expense, "CATEGORY_CHOICES", getattr(Expense, "CategoryChoices", None))
        if hasattr(categories, "choices"):
            categories = categories.choices

        # Build clean query string for pagination links
        query_params = self.request.GET.copy()
        if "page" in query_params:
            del query_params["page"]

        context.update({
            "total_expenses": total_expenses,
            "preset": preset,
            "period": {
                "start_date": start,
                "end_date": end,
            },
            "selected_range_label": range_label,
            "category_filter": self.request.GET.get("category", ""),
            "categories": categories or [],
            "extra_qs": query_params.urlencode(),
        })
        return context


### INTERNAL ACCOUNTS VIEW
class InternalAccountListView(
    RoleRequiredMixin,
    ListView,
):

    model = InternalAccount

    template_name = (
        "internal_stock/internal_account_list.html"
    )

    context_object_name = "accounts"

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.ACCOUNTS,
    )

    def get_queryset(self):

        return (
            InternalAccount.objects
            .order_by("name")
        )

class InternalAccountCreateView(
    RoleRequiredMixin,
    CreateView,
):

    model = InternalAccount

    form_class = InternalAccountForm

    template_name = (
        "internal_stock/internal_account_form.html"
    )

    success_url = reverse_lazy(
        "internal_account_list"
    )

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.ACCOUNTS,
    )

    def form_valid(self, form):

        messages.success(
            self.request,
            (
                f"Internal account "
                f"'{form.instance.name}' "
                f"created successfully."
            ),
        )

        return super().form_valid(form)


class InternalStockIssueListView(
    RoleRequiredMixin,
    ListView,
):
    model = InternalStockIssue
    template_name = "internal_stock/internal_stock_issue_list.html"
    context_object_name = "issues"
    paginate_by = 20
    allowed_roles = (
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.ACCOUNTS,
    )

    def get_queryset(self):
        queryset = (
            InternalStockIssue.objects
            .select_related("account", "issued_by")
            .prefetch_related(
                "items__coffee_stock__variety",
                "items__packaged_product__blend",
                "items__packaged_product__pack_size",
            )
            .order_by("-issue_date", "-created_at")
        )

        # 1. Read query parameters sent by date_filters.html
        self.period = self.request.GET.get("period", "").strip()
        self.from_date = self.request.GET.get("from_date", "").strip()
        self.to_date = self.request.GET.get("to_date", "").strip()

        today = timezone.now().date()

        # 2. Filter directly on issue_date
        if self.period == "today":
            queryset = queryset.filter(issue_date=today)

        elif self.period == "yesterday":
            queryset = queryset.filter(issue_date=today - timedelta(days=1))

        elif self.period == "this_week":
            start_of_week = today - timedelta(days=today.weekday())
            queryset = queryset.filter(issue_date__gte=start_of_week, issue_date__lte=today)

        elif self.period == "this_month":
            start_of_month = today.replace(day=1)
            queryset = queryset.filter(issue_date__gte=start_of_month, issue_date__lte=today)

        elif self.period == "custom" or self.from_date or self.to_date:
            if self.from_date:
                queryset = queryset.filter(issue_date__gte=self.from_date)
            if self.to_date:
                queryset = queryset.filter(issue_date__lte=self.to_date)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Pass filter state to template
        context["period"] = getattr(self, "period", "")
        context["from_date"] = getattr(self, "from_date", "")
        context["to_date"] = getattr(self, "to_date", "")

        # Preserve query string across pagination links
        query_params = self.request.GET.copy()
        if "page" in query_params:
            del query_params["page"]
        context["extra_qs"] = query_params.urlencode()

        return context
    

class InternalStockIssueCreateView(
    RoleRequiredMixin,
    View,
):

    template_name = (
        "internal_stock/internal_stock_issue_form.html"
    )

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.ACCOUNTS,
    )

    def get(self, request):

        return render(
            request,
            self.template_name,
            self.get_context_data(),
        )

    def post(self, request):

        account_id = request.POST.get(
            "account"
        )

        account = get_object_or_404(
            InternalAccount,
            pk=account_id,
            is_active=True,
        )

        issue_date = request.POST.get(
            "issue_date"
        )

        reason = request.POST.get(
            "reason",
            "",
        ).strip()

        notes = request.POST.get(
            "notes",
            "",
        ).strip()

        item_types = request.POST.getlist(
            "item_type"
        )

        coffee_stock_ids = request.POST.getlist(
            "coffee_stock"
        )

        stock_stages = request.POST.getlist(
            "stock_stage"
        )

        quantity_kg_list = request.POST.getlist(
            "quantity_kg"
        )

        packaged_product_ids = request.POST.getlist(
            "packaged_product"
        )

        packs_list = request.POST.getlist(
            "packs"
        )

        unit_cost_list = request.POST.getlist(
            "unit_cost"
        )

        items = []

        for index, item_type in enumerate(
            item_types
        ):

            if item_type == "non_packaged":

                items.append({
                    "type": "non_packaged",

                    "coffee_stock_id": (
                        coffee_stock_ids[index]
                        if index <
                        len(coffee_stock_ids)
                        else None
                    ),

                    "stock_stage": (
                        stock_stages[index]
                        if index <
                        len(stock_stages)
                        else None
                    ),

                    "quantity_kg": (
                        quantity_kg_list[index]
                        if index <
                        len(quantity_kg_list)
                        else None
                    ),

                    "unit_cost": (
                        unit_cost_list[index]
                        if index <
                        len(unit_cost_list)
                        and unit_cost_list[index]
                        else None
                    ),
                })

            elif item_type == "packaged":

                items.append({
                    "type": "packaged",

                    "packaged_product_id": (
                        packaged_product_ids[index]
                        if index <
                        len(packaged_product_ids)
                        else None
                    ),

                    "packs": (
                        packs_list[index]
                        if index <
                        len(packs_list)
                        else None
                    ),

                    "unit_cost": (
                        unit_cost_list[index]
                        if index <
                        len(unit_cost_list)
                        and unit_cost_list[index]
                        else None
                    ),
                })

        try:

            if not issue_date:

                issue_date = timezone.localdate()

            issue = create_internal_stock_issue(
                account=account,
                issue_date=issue_date,
                reason=reason,
                notes=notes,
                user=request.user,
                items=items,
            )

            messages.success(
                request,
                (
                    f"Internal stock issue "
                    f"#{issue.pk} recorded successfully."
                ),
            )

            return redirect(
                "internal_stock_issue_detail",
                pk=issue.pk,
            )

        except (
            ValidationError,
            ValueError,
        ) as exc:

            messages.error(
                request,
                str(exc),
            )

            context = self.get_context_data()

            context["submitted"] = request.POST

            return render(
                request,
                self.template_name,
                context,
            )

    def get_context_data(self):

        stocks = list(
            CoffeeStock.objects
            .select_related("variety")
            .order_by(
                "batch_number"
            )
        )

        stock_options = []

        for stock in stocks:

            for stage_value, stage_label in (
                (
                    StockStage.GREEN,
                    "Green Coffee",
                ),
                (
                    StockStage.ROASTED,
                    "Roasted Coffee",
                ),
                (
                    StockStage.GROUND,
                    "Ground Coffee",
                ),
            ):

                available = (
                    get_stage_inventory(
                        stock,
                        stage_value,
                    )
                )

                if available <= 0:
                    continue

                stock_options.append({
                    "value": (
                        f"{stock.pk}|"
                        f"{stage_value}"
                    ),
                    "stock_id": stock.pk,
                    "stage": stage_value,
                    "stage_label": stage_label,
                    "available": available,
                    "batch_number": (
                        stock.batch_number
                    ),
                    "variety": (
                        stock.variety.name
                    ),
                })

        packaged_products = []

        products = (
            PackagedProduct.objects
            .filter(is_active=True)
            .select_related(
                "blend",
                "pack_size",
            )
            .order_by(
                "blend__name",
                "pack_size__grams",
                "form",
            )
        )

        for product in products:

            available = (
                get_packaged_internal_available(
                    product
                )
            )

            if available <= 0:
                continue

            packaged_products.append({
                "id": product.pk,
                "label": str(product),
                "available": available,
            })

        return {
            "accounts": (
                InternalAccount.objects
                .filter(is_active=True)
                .order_by("name")
            ),

            "stock_options": stock_options,

            "packaged_products": (
                packaged_products
            ),

            "today": timezone.localdate(),

            "stock_stages": (
                (
                    StockStage.GREEN,
                    "Green Coffee",
                ),
                (
                    StockStage.ROASTED,
                    "Roasted Coffee",
                ),
                (
                    StockStage.GROUND,
                    "Ground Coffee",
                ),
            ),
        }


from datetime import date, datetime, time, timedelta
from decimal import Decimal
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.generic import DetailView


class InternalAccountDetailView(
    RoleRequiredMixin,
    DetailView,
):
  model = InternalAccount
  template_name = "internal_stock/internal_account_detail.html"
  context_object_name = "account"
  allowed_roles = (
      User.Role.ADMIN,
      User.Role.MANAGER,
      User.Role.ACCOUNTS,
  )

  def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    account = self.object

    issues = (
        account.stock_issues.select_related(
            "account",
            "issued_by",
        )
        .prefetch_related(
            "items__coffee_stock__variety",
            "items__packaged_product__blend",
            "items__packaged_product__pack_size",
        )
        .order_by(
            "-issue_date",
            "-created_at",
        )
    )

    # ---------------------------------------------
    # DATE FILTER & PERIOD HANDLING
    # ---------------------------------------------
    selected_period = self.request.GET.get("period", "all")
    from_date_raw = self.request.GET.get("from_date", "").strip()
    to_date_raw = self.request.GET.get("to_date", "").strip()

    today = timezone.now().date()
    from_date = None
    to_date = None

    if selected_period == "today":
      from_date = today
      to_date = today
    elif selected_period == "yesterday":
      from_date = today - timedelta(days=1)
      to_date = today - timedelta(days=1)
    elif selected_period == "this_week":
      from_date = today - timedelta(days=today.weekday())  # Monday start
      to_date = today
    elif selected_period == "this_month":
      from_date = today.replace(day=1)
      to_date = today
    elif selected_period == "custom" or (from_date_raw or to_date_raw):
      selected_period = "custom"
      if from_date_raw:
        from_date = parse_date(from_date_raw)
      if to_date_raw:
        to_date = parse_date(to_date_raw)
    else:
      selected_period = "all"

    # Filter QuerySet
    if from_date:
      issues = issues.filter(issue_date__gte=from_date)

    if to_date:
      # Use 23:59:59 end-of-day cutoff if issue_date is a DateTimeField
      end_datetime = datetime.combine(to_date, time.max)
      issues = issues.filter(issue_date__lte=end_datetime)

    issues = list(issues)

    # ---------------------------------------------
    # TOTALS
    # ---------------------------------------------
    total_issues = len(issues)
    total_value = sum(
        (item.total_value for issue in issues for item in issue.items.all()),
        Decimal("0.00"),
    )
    total_kg = sum(
        (
            item.quantity_kg or Decimal("0.00")
            for issue in issues
            for item in issue.items.all()
        ),
        Decimal("0.00"),
    )
    total_packs = sum(
        (item.packs or 0 for issue in issues for item in issue.items.all()),
        0,
    )

    context.update({
        "issues": issues,
        "total_issues": total_issues,
        "total_value": total_value,
        "total_kg": total_kg,
        "total_packs": total_packs,
        "selected_period": selected_period,
        "from_date": from_date.strftime("%Y-%m-%d") if from_date else "",
        "to_date": to_date.strftime("%Y-%m-%d") if to_date else "",
    })
    return context

class InternalStockIssueDetailView(
    RoleRequiredMixin,
    DetailView,
):
    model = InternalStockIssue

    template_name = (
        "internal_stock/internal_stock_issue_detail.html"
    )

    context_object_name = "issue"

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.MANAGER,
        User.Role.ACCOUNTS,
    )

    def get_queryset(self):

        return (
            InternalStockIssue.objects
            .select_related(
                "account",
                "issued_by",
            )
            .prefetch_related(
                "items__coffee_stock__variety",
                "items__packaged_product__blend",
                "items__packaged_product__pack_size",
            )
        )

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        items = list(
            self.object.items.all()
        )

        context["items"] = items

        context["total_value"] = sum(
            (
                item.total_value
                for item in items
            ),
            Decimal("0.00"),
        )

        return context

from datetime import timedelta
from django.utils import timezone

def apply_date_filter(queryset, request, date_field='issue_date'):
    period = request.GET.get('period', 'all')
    from_date = request.GET.get('from_date', '')
    to_date = request.GET.get('to_date', '')
    today = timezone.now().date()

    if period == 'today':
        queryset = queryset.filter(**{f"{date_field}": today})
    elif period == 'yesterday':
        queryset = queryset.filter(**{f"{date_field}": today - timedelta(days=1)})
    elif period == 'this_week':
        start_of_week = today - timedelta(days=today.weekday())
        queryset = queryset.filter(**{f"{date_field}__gte": start_of_week, f"{date_field}__lte": today})
    elif period == 'this_month':
        start_of_month = today.replace(day=1)
        queryset = queryset.filter(**{f"{date_field}__gte": start_of_month, f"{date_field}__lte": today})
    elif period == 'custom' or from_date or to_date:
        period = 'custom'
        if from_date:
            queryset = queryset.filter(**{f"{date_field}__gte": from_date})
        if to_date:
            queryset = queryset.filter(**{f"{date_field}__lte": to_date})

    context_data = {
        'selected_period': period,
        'from_date': from_date,
        'to_date': to_date,
    }
    return queryset, context_data

    # reports
@method_decorator(login_required, name="dispatch")
class ReportOverviewView(RoleRequiredMixin, TemplateView):
    template_name = "reports/overview.html"

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.ACCOUNTS,
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        period = resolve_report_period(self.request)

        consumption = get_stock_consumption_report(
            period
        )

        sales = get_sales_report(
            period
        )

        context.update({
            "period": period,
            "period_label": period.label,
            "preset": period.preset,

            # First executive report module
            "consumption": consumption,
            "sales": sales,
        })

        return context


@method_decorator(login_required, name="dispatch")
class StockConsumptionReportView(
    RoleRequiredMixin,
    TemplateView,
):
    template_name = "reports/stock_consumption.html"

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.ACCOUNTS,
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        period = resolve_report_period(self.request)

        report = get_stock_consumption_report(
            period
        )

        context.update({
            "period": period,
            "period_label": period.label,
            "preset": period.preset,
            **report,
        })

        return context

@method_decorator(login_required, name="dispatch")
class SalesReportView(
    RoleRequiredMixin,
    TemplateView,
):
    template_name = "reports/sales.html"

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.ACCOUNTS,
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(
            **kwargs
        )

        period = resolve_report_period(
            self.request
        )

        report = get_sales_report(
            period
        )

        context.update({
            "period": period,
            "period_label": period.label,
            "preset": period.preset,
            **report,
        })

        return context

@method_decorator(login_required, name='dispatch')
class FinanceReportView(RoleRequiredMixin, TemplateView):
    template_name = 'reports/finance.html'
    allowed_roles = (User.Role.ADMIN, User.Role.ACCOUNTS)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        period = resolve_report_period(self.request)
        ctx.update(period=period, preset=period.preset, period_label=period.label,
                   **get_finance_report(period))
        return ctx
    
@method_decorator(login_required, name='dispatch')
class OperationsReportView(RoleRequiredMixin, TemplateView):
    template_name = 'reports/operations.html'
    allowed_roles = (User.Role.ADMIN, User.Role.ACCOUNTS)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        period = resolve_report_period(self.request)
        ctx.update(period=period, preset=period.preset, period_label=period.label,
                   **get_operations_report(period))
        return ctx

@method_decorator(login_required, name="dispatch")
class ExecutiveReportView(RoleRequiredMixin, TemplateView):
    template_name = "reports/executive.html"
    allowed_roles = (User.Role.ADMIN, User.Role.ACCOUNTS)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        period = resolve_report_period(self.request)
        ctx.update(period=period, preset=period.preset, period_label=period.label,
                   **get_executive_report(period))
        return ctx


@method_decorator(login_required, name='dispatch')
class ProfitabilityPlaceholderView(RoleRequiredMixin, TemplateView):
    template_name = 'reports/profitability.html'
    allowed_roles = (User.Role.ADMIN, User.Role.ACCOUNTS)


class ExecutiveExportMixin(RoleRequiredMixin):
    allowed_roles = (User.Role.ADMIN, User.Role.ACCOUNTS)

    def export_context(self):
        period = resolve_report_period(self.request)
        report = get_executive_report(period)
        return period, report


@method_decorator(login_required, name='dispatch')
class ExecutivePrintView(ExecutiveExportMixin, TemplateView):
    template_name = 'reports/executive_print.html'
    allowed_roles =(User.Role.ACCOUNTS, User.Role.ADMIN) 

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        period, report = self.export_context()
        context.update(period=period, period_label=period.label, preset=period.preset, **report)
        context['comparison_rows'] = [
            ('Sales (UGX)', report['sales_comparison']),
            ('Collections (UGX)', report['collections_comparison']),
            ('Expenses (UGX)', report['expenses_comparison']),
            ('Consumption (kg)', report['consumption_comparison']),
        ]
        for row in context['executive_operations']['process_rows']:
            row['output_kg'] = row['primary_kg'] + row['secondary_kg']
        return context


@method_decorator(login_required, name='dispatch')
class ExecutivePDFView(ExecutiveExportMixin, TemplateView):
    allowed_roles =(User.Role.ACCOUNTS, User.Role.ADMIN)
    def get(self, request, *args, **kwargs):
        period, report = self.export_context()
        pdf = pdf_bytes(period, report)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="nonda-executive-{period.preset}.pdf"'
        response['Cache-Control'] = 'private, no-store'
        return response


# ============================================================
# REPORT — INTERNAL USAGE DETAIL
# ============================================================

@method_decorator(login_required, name="dispatch")
class InternalUsageReportDetailView(
    RoleRequiredMixin,
    TemplateView,
):
    template_name = "reports/internal_usage_detail.html"

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.ACCOUNTS,
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        period = resolve_report_period(self.request)

        issues = (
            InternalStockIssue.objects
            .select_related(
                "account",
                "issued_by",
            )
            .prefetch_related(
                "items__coffee_stock",
                "items__packaged_product__blend",
                "items__packaged_product__pack_size",
            )
        )

        issues = period.filter_date(
            issues,
            "issue_date",
        )

        rows = []
        account_totals = {}
        total_kg = Decimal("0.00")

        for issue in issues:

            issue_total = Decimal("0.00")
            item_rows = []

            for item in issue.items.all():

                if item.packaged_product_id:

                    kg = (
                        Decimal(item.packs or 0)
                        * Decimal(
                            str(
                                item.packaged_product.kg_per_pack
                            )
                        )
                    )

                    item_name = str(
                        item.packaged_product
                    )

                    quantity_label = (
                        f"{item.packs} packs"
                    )

                else:

                    kg = Decimal(
                        str(item.quantity_kg or 0)
                    )

                    item_name = str(
                        item.coffee_stock
                    )

                    if item.stock_stage:
                        item_name += (
                            f" · "
                            f"{item.get_stock_stage_display()}"
                        )

                    quantity_label = (
                        f"{item.quantity_kg} kg"
                    )

                issue_total += kg

                item_rows.append({
                    "name": item_name,
                    "quantity": quantity_label,
                    "kg": kg,
                })

            total_kg += issue_total

            account_name = issue.account.name

            account_totals[account_name] = (
                account_totals.get(
                    account_name,
                    Decimal("0.00"),
                )
                + issue_total
            )

            rows.append({
                "issue": issue,
                "kg": issue_total,
                "items": item_rows,
            })

        summary = [
            {
                "name": name,
                "kg": kg,
            }
            for name, kg in account_totals.items()
        ]

        summary.sort(
            key=lambda row: row["kg"],
            reverse=True,
        )

        context.update({
            "period": period,
            "period_label": period.label,
            "preset": period.preset,
            "total_kg": total_kg,
            "summary": summary,
            "rows": rows,
        })

        return context


# ============================================================
# REPORT — PRODUCTION LOSS DETAIL
# ============================================================

@method_decorator(login_required, name="dispatch")
class ProductionLossReportDetailView(
    RoleRequiredMixin,
    TemplateView,
):
    template_name = "reports/production_loss_detail.html"

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.ACCOUNTS,
        User.Role.MANAGER,
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        period = resolve_report_period(self.request)

        processing_runs = (
            ProcessingRun.objects
            .filter(
                status="completed",
                loss_quantity__gt=0,
            )
            .select_related(
                "stock",
                "stock__variety",
                "completed_by",
            )
        )

        processing_runs = period.filter_datetime(
            processing_runs,
            "completed_at",
        ).order_by(
            "-loss_quantity",
            "-completed_at",
        )


        packaging_runs = (
            PackagingRun.objects
            .filter(
                status="completed",
                loss_kg__gt=0,
            )
            .select_related(
                "stock",
                "stock__variety",
                "product",
                "product__blend",
                "product__pack_size",
                "completed_by",
            )
        )

        packaging_runs = period.filter_datetime(
            packaging_runs,
            "completed_at",
        ).order_by(
            "-loss_kg",
            "-completed_at",
        )


        processing_total = sum(
            (
                run.loss_quantity
                for run in processing_runs
            ),
            Decimal("0.00"),
        )

        packaging_total = sum(
            (
                run.loss_kg
                for run in packaging_runs
            ),
            Decimal("0.00"),
        )

        context.update({
            "period": period,
            "period_label": period.label,
            "preset": period.preset,
            "processing_runs": processing_runs,
            "packaging_runs": packaging_runs,
            "processing_total": processing_total,
            "packaging_total": packaging_total,
            "total_loss": (
                processing_total
                + packaging_total
            ),
        })

        return context


# ============================================================
# REPORT — SALES DETAIL
# ============================================================

@method_decorator(login_required, name="dispatch")
class SalesBreakdownReportView(
    RoleRequiredMixin,
    TemplateView,
):
    template_name = "reports/sales_detail.html"

    allowed_roles = (
        User.Role.ADMIN,
        User.Role.ACCOUNTS,
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        period = resolve_report_period(
            self.request
        )

        report = get_sales_report(
            period
        )

        context.update({
            "period": period,
            "period_label": period.label,
            "preset": period.preset,
            **report,
        })

        return context

# ============================================================
# REPORT PRINT VIEWS
# Reuse the exact same report calculations.
# Only the presentation/template changes.
# ============================================================


class StockConsumptionPrintView(
    StockConsumptionReportView
):
    template_name = "reports/print/stock_consumption.html"
    allowed_roles =(User.Role.ACCOUNTS,
                     User.Role.ADMIN)


class SalesPrintView(
    SalesReportView
):
    template_name = "reports/print/sales.html"
    allowed_roles =(User.Role.ACCOUNTS,
                     User.Role.ADMIN)


class FinancePrintView(
    FinanceReportView
):
    template_name = "reports/print/finance.html"
    allowed_roles =(User.Role.ACCOUNTS,
                     User.Role.ADMIN)


class OperationsPrintView(
    OperationsReportView
):
    template_name = "reports/print/operations.html"
    allowed_roles =(User.Role.ACCOUNTS,
                     User.Role.ADMIN)


class OperationsDetailReportView(
    OperationsReportView
):
    template_name = "reports/operations_detail.html"


# views.py
from django.shortcuts import render

def forgot_password_view(request):
    """Temporary placeholder for password reset workflow."""
    return render(request, 'auth/forgot_password.html')