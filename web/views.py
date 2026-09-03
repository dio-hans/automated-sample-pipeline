from .permissions import RoleRequiredMixin
# Change '.utils' to match the file or path where your routing function lives
from decimal import Decimal
from .permissions import role_required, admin_required
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, TemplateView, CreateView, UpdateView, DeleteView, DetailView, View

from .models import (
    Company,
    CoffeeStock,
    CoffeeVariety,
    Sample,
    StockMovement,
    Followup,
    Contract,
    User,
    ProcessingRun,
)
from .forms import (
    CompanyForm, CoffeeStockForm, CoffeeStockIntakeForm, SampleForm, ContractForm,
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
from django.contrib.auth.decorators import login_required

from .models import ( PackagedProduct, PackagingRun, 
    PackagedInventory, PackRelease, PackReturn
)
from .forms import (
    ProcessingIssueForm,
    ProcessingCompleteForm,
    PackagedProductForm,
    PackagingRunForm,
    PackReleaseForm,
    PackReturnForm,
)

from .services.packaging import (
    execute_packaging_run, execute_pack_release, execute_pack_return
)


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

class CoffeeStockListViews(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
    model = CoffeeStock
    template_name = "pipeline/coffee_stock_list.html"
    context_object_name = "stocks"

    def get_queryset(self):
        return (
            CoffeeStock.objects
            .select_related("variety")
            .prefetch_related("movements")
            .order_by("-received_date", "-created_at")
        )
    
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["preset"] = getattr(self, "_preset", "this_month")
        ctx["total_available"] = sum(s.quantity_available for s in ctx["stocks"])
        ctx["low_stock_count"] = sum(1 for s in ctx["stocks"] if 0 < s.quantity_available <= s.reorder_level)
        ctx["out_of_stock_count"] = sum(1 for s in ctx["stocks"] if s.quantity_available <= 0)
        return ctx


class CoffeeStockDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
    model = CoffeeStock
    template_name = "pipeline/stock_detail.html"
    context_object_name = "stock"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["movements"] = self.object.movements.select_related("created_by").order_by("-created_at")
        ctx["available_green"] = get_stage_inventory(self.object, "green_received")
        ctx["available_roasted"] = get_stage_inventory(self.object, "roasted")
        ctx["available_ground"] = get_stage_inventory(self.object, "ground")
        ctx["available_quakers"] = get_stage_inventory(self.object, "quakers")
        ctx["processing_issue_form"] = ProcessingIssueForm()
        ctx["processing_complete_form"] = ProcessingCompleteForm()
        ctx["open_processing_runs"] = (
            self.object.processing_runs
            .filter(status="open")
            .select_related("issued_by")
            .order_by("-issued_at")
        )
        return ctx


class VarietyDatalistMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["existing_varieties"] = CoffeeVariety.objects.filter(is_active=True).order_by("name")
        return context


class CoffeeStockCreateView(RoleRequiredMixin, VarietyDatalistMixin, CreateView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
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


class CoffeeStockUpdateView(RoleRequiredMixin, VarietyDatalistMixin, UpdateView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
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

@role_required(User.Role.MANAGER, User.Role.ADMIN)
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


@role_required(User.Role.MANAGER, User.Role.ADMIN)
def stock_stage_inventory_api(request, pk):
    """JSON snapshot of a batch's quantity at every stage Ã¢â‚¬â€ powers the processing form."""
    stock = get_object_or_404(CoffeeStock, pk=pk)
    return JsonResponse({
        "green_received": float(get_stage_inventory(stock, "green_received")),
        "roasted": float(get_stage_inventory(stock, "roasted")),
        "ground": float(get_stage_inventory(stock, "ground")),
        "packaged": float(get_stage_inventory(stock, "packaged")),
        "available": float(stock.quantity_available),
    })

class StockMovementListView(RoleRequiredMixin, ListView):
    model = StockMovement
    template_name = "pipeline/stock_movement_list.html"
    context_object_name = "movements"
    paginate_by = 50
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)

    def get_queryset(self):
        qs = (
            StockMovement.objects
            .select_related("stock", "stock__variety", "created_by")
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
        context["preset"] = getattr(self, "_preset", "this_month")
        context["start_date"] = getattr(self, "_start", None)
        context["end_date"] = getattr(self, "_end", None)
        return context


class DashboardView(RoleRequiredMixin, TemplateView):
    template_name = "pipeline/dashboard.html"
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        stocks = list(
            CoffeeStock.objects
            .select_related("variety")
            .prefetch_related("movements")
        )
        packaged = list(PackagedInventory.objects.select_related("product"))

        movements = (
            StockMovement.objects
            .select_related("stock", "stock__variety", "created_by")
            .order_by("-created_at")
        )

        context["stocks"] = stocks
        context["recent_movements"] = movements[:10]
        context["total_stock"] = sum(
            stock.quantity_available for stock in stocks
        )
        context["green_stock"] = sum(
            stock.quantity_green for stock in stocks
        )
        context["roasted_stock"] = sum(
            stock.quantity_roasted for stock in stocks
        )
        context["ground_stock"] = sum(
            stock.quantity_ground for stock in stocks
        )
        context["packaged_stock"] = sum(
            inventory.available for inventory in packaged
        )
        context["low_stock"] = [
            stock for stock in stocks if stock.is_low_stock
        ]
        context["out_of_stock"] = [
            stock for stock in stocks if stock.quantity_available <= 0
        ]
        return context


@role_required(User.Role.MANAGER, User.Role.ADMIN)
def low_stock_list(request):
    stocks = (
        CoffeeStock.objects
        .select_related("variety")
        .prefetch_related("movements")
    )
    low_stocks = [
        stock
        for stock in stocks
        if stock.quantity_available <= stock.reorder_level
    ]
    low_stocks.sort(
        key=lambda stock: (
            stock.quantity_available > 0,
            stock.quantity_available,
        )
    )

    for stock in low_stocks:
        stock.shortfall = max(
            stock.reorder_level - stock.quantity_available,
            Decimal("0.00"),
        )

    return render(
        request,
        "pipeline/low_stock_list.html",
        {
            "low_stocks": low_stocks,
            "low_stock_count": len(low_stocks),
        },
    )





def user_login(request):
    if request.method == "POST":
        form = UserLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if not user.is_active:
                messages.error(
                    request,
                    "Access denied: your account has been suspended.",
                )
                return redirect("login")

            login(request, user)
            messages.success(request, f"Welcome back, {user.username}!")
            return redirect_user_by_role(user)
    else:
        form = UserLoginForm()

    return render(request, "pipeline/login.html", {"form": form})


@login_required
def user_logout(request):
    logout(request)
    messages.info(request, "Session terminated successfully.")
    return redirect("login")



def register_user(request):
    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            new_user = form.save()
            messages.success(
                request,
                f"User account created for {new_user.username}.",
            )
            return redirect("login")
    else:
        form = UserRegistrationForm()

    system_users = User.objects.all().order_by("role", "username")

    return render(
        request,
        "pipeline/registration.html",
        {
            "form": form,
            "users": system_users,
        },
    )


@admin_required
def toggle_user_status(request, user_id):
    if request.method != "POST":
        return redirect("register")

    employee = get_object_or_404(User, id=user_id)

    if employee == request.user:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect("register")

    if employee.is_superuser:
        messages.error(request, "A superuser account cannot be deactivated here.")
        return redirect("register")

    if employee.role == User.Role.ADMIN and employee.is_active:
        active_admins = User.objects.filter(
            role=User.Role.ADMIN,
            is_active=True,
        ).count()
        if active_admins <= 1:
            messages.error(
                request,
                "The last active administrator cannot be deactivated.",
            )
            return redirect("register")

    employee.is_active = not employee.is_active
    employee.save(update_fields=["is_active"])

    if employee.is_active:
        messages.success(request, f"{employee.username} has been activated.")
    else:
        messages.warning(request, f"{employee.username} has been suspended.")

    return redirect("register")


# ===================== PROCESSING =====================

class ProcessStockView(RoleRequiredMixin, View):
    """
    Start a processing run from the batch detail page.

    The issue is recorded immediately, so the source-stage quantity leaves
    available inventory while the coffee is physically at processing.
    """
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)

    def post(self, request, pk):
        stock = get_object_or_404(CoffeeStock, pk=pk)
        form = ProcessingIssueForm(request.POST)

        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect("stock_detail", pk=stock.pk)

        try:
            result = issue_for_processing(
                stock=stock,
                process_type=form.cleaned_data["process_type"],
                input_quantity=form.cleaned_data["input_quantity"],
                user=current_user(request),
                notes=form.cleaned_data.get("notes", ""),
            )
            messages.success(
                request,
                f"{result.processing_run.get_process_type_display()} run "
                f"#{result.processing_run.pk} started.",
            )
        except (ValueError, ValidationError) as exc:
            messages.error(request, str(exc))

        return redirect("stock_detail", pk=stock.pk)


class ProcessCompleteView(RoleRequiredMixin, View):
    """Receive output from an open processing run and close it."""
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)

    def post(self, request, pk):
        run = get_object_or_404(
            ProcessingRun.objects.select_related("stock"),
            pk=pk,
        )
        form = ProcessingCompleteForm(request.POST)

        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect("stock_detail", pk=run.stock_id)

        output = form.cleaned_data["output_quantity"]
        quakers = form.cleaned_data.get("quaker_quantity") or Decimal("0.00")
        notes = form.cleaned_data.get("notes", "")

        try:
            if run.process_type == "roasting":
                complete_roasting(
                    processing_run=run,
                    output_quantity=output,
                    user=current_user(request),
                    notes=notes,
                )
            elif run.process_type == "sorting":
                complete_sorting(
                    processing_run=run,
                    good_quantity=output,
                    quaker_quantity=quakers,
                    user=current_user(request),
                    notes=notes,
                )
            elif run.process_type == "grinding":
                complete_grinding(
                    processing_run=run,
                    output_quantity=output,
                    user=current_user(request),
                    notes=notes,
                )
            else:
                raise ValidationError("Unsupported processing run type.")

            messages.success(
                request,
                f"Processing run #{run.pk} completed successfully.",
            )
        except (ValueError, ValidationError) as exc:
            messages.error(request, str(exc))

        return redirect("stock_detail", pk=run.stock_id)


# ===================== PACKAGED INVENTORY VIEWS =====================

class PackagedInventoryListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
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


class PackagedProductDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
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


class PackagedProductCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
    model = PackagedProduct
    form_class = PackagedProductForm
    template_name = "pipeline/packaged_product_form.html"
    success_url = reverse_lazy("packaged_inventory_list")


# ===================== PACKAGING RUN VIEWS =====================

class PackagingRunListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
    model = PackagingRun
    template_name = "pipeline/packaging_run_list.html"
    context_object_name = "packaging_runs"

    def get_queryset(self):
        return PackagingRun.objects.select_related(
            "product__blend", "product__pack_size", "stock"
        ).order_by("-issued_at")


class PackagingRunDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
    model = PackagingRun
    template_name = "pipeline/packaging_run_detail.html"
    context_object_name = "run"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        run = self.object
        kg_per_pack = run.product.kg_per_pack
        ctx["represented_kg"] = Decimal(run.packs_produced) * kg_per_pack
        return ctx


class PackagingRunCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
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

class PackReleaseListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.MANAGER, User.Role.SALES, User.Role.ADMIN)
    model = PackRelease
    template_name = "pipeline/pack_release_list.html"
    context_object_name = "releases"

    def get_queryset(self):
        qs = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to"
        ).order_by("-released_at")
        if self.request.user.role == User.Role.SALES:
            qs = qs.filter(released_to=self.request.user)
        return qs


class PackReleaseDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = (User.Role.MANAGER, User.Role.SALES, User.Role.ADMIN)
    model = PackRelease
    template_name = "pipeline/pack_release_detail.html"
    context_object_name = "release"

    def get_queryset(self):
        qs = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to"
        ).prefetch_related("returns")
        if self.request.user.role == User.Role.SALES:
            qs = qs.filter(released_to=self.request.user)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        release = self.object
        ctx["returns"] = PackReturn.objects.filter(release=release).order_by("-returned_at")
        try:
            ctx["settlement"] = release.settlement
        except Exception:
            ctx["settlement"] = None
        return ctx


class PackReleaseCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
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
                price_per_pack=form.cleaned_data["price_per_pack"],
                user=current_user(self.request),
                notes=form.cleaned_data.get("notes", "")
            )
            messages.success(self.request, "Stock release executed successfully.")
            return redirect(self.success_url)
        except ValidationError as e:
            form.add_error(None, e.message)
            return self.form_invalid(form)


class PackReturnListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
    model = PackReturn
    template_name = "pipeline/pack_return_list.html"
    context_object_name = "returns"

    def get_queryset(self):
        return PackReturn.objects.select_related(
            "release__product__blend", "release__product__pack_size"
        ).order_by("-returned_at")


class PackReturnCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
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