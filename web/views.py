from django.views.generic import FormView
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
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, TemplateView, CreateView, UpdateView, DeleteView, DetailView, View

from .models import Company, PackReturn, CoffeeStock, CoffeeVariety, Sample, StockMovement, Followup, Contract, StockStage, User
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

class PackReleaseListView(InventoryRoleRequiredMixin, ListView):
    model = PackRelease
    template_name = "pipeline/pack_release_list.html"
    context_object_name = "releases"

    def get_queryset(self):
        return PackRelease.objects.select_related(
            "product__blend", "product__pack_size"
        ).order_by("-released_at")


class PackReleaseDetailView(InventoryRoleRequiredMixin, DetailView):
    model = PackRelease
    template_name = "pipeline/pack_release_detail.html"
    context_object_name = "release"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        release = self.object
        ctx["returns"] = PackReturn.objects.filter(release=release).order_by("-returned_at")
        try:
            ctx["settlement"] = release.settlement
        except Exception:
            ctx["settlement"] = None
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

class CoffeeStockListViews(ListView):
    model = CoffeeStock
    template_name = "pipeline/coffee_stock_list.html"
    context_object_name = "stocks"

    def get_queryset(self):
        qs = StockMovement.objects.select_related("stock__variety", "created_by").order_by("-created_at")
        qs, preset, today, start, end = apply_date_filters(self.request, qs, "created_at")
        self._preset = preset
        self._start = start
        self._end = end
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

# login and registration

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

# PROCESS STOCK VIEW






class ProcessStockView(InventoryRoleRequiredMixin, View):
    def post(self, request, pk):
        stock = get_object_or_404(CoffeeStock, pk=pk)
        form = ProcessingCompleteForm(request.POST)
        if form.is_valid():
            try:
                step = form.cleaned_data["step"]; user = current_user(request)
                result = issue_for_processing(
                    stock=stock, process_type="roasting" if step == "roast" else "grinding",
                    input_quantity=form.cleaned_data["input_quantity"], user=user, notes=form.cleaned_data.get("notes", ""))
                if step == "roast":
                    run, _, _ = complete_roasting(processing_run=result.processing_run, output_quantity=form.cleaned_data["output_quantity"], user=user, notes=form.cleaned_data.get("notes", ""))
                else:
                    run, _ = complete_grinding(processing_run=result.processing_run, output_quantity=form.cleaned_data["output_quantity"], user=user, notes=form.cleaned_data.get("notes", ""))
                messages.success(request, f"{run.get_process_type_display()} completed: {run.input_quantity} kg → {run.output_quantity} kg.")
            except (ValueError, ValidationError) as exc:
                messages.error(request, str(exc))
        else:
            for field, errors in form.errors.items():
                for err in errors: messages.error(request, f"{field.replace('_', ' ').title()}: {err}")
        return redirect("stock_detail", pk=stock.pk)

# ===================== PACKAGED INVENTORY VIEWS =====================

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
    template_name = "your_template_name.html"  # Make sure this matches yours
    success_url = "/success-path/"             # Make sure this matches yours

    # 2. Modify the form instance dynamically before it goes to the template
    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        
        # Option A: If you were trying to filter a specific dropdown field choice
        # Replace 'field_name' with the actual field name on PackagedProductBulkForm
        # Replace 'PackReturn' with whichever model tracks 'returned_at'
        form.fields['field_name'].queryset = PackReturn.objects.select_related(
            "release__product__blend", 
            "release__product__pack_size"
        ).order_by("-returned_at")
        
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



