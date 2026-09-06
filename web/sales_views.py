
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView
from django.forms import formset_factory

from .models import (
    Company,
    PackRelease,
    PackReturn,
    PackSettlement,
    PackagedInventory,
    StockRequest,
    StockRequestItem,
    User,
)
from .sales_forms import (
    FulfillItemForm,
    PackReturnCleanForm,
    SettlementForm,
    StockRequestForm,
    StockRequestItemFormSet,
)
from .sales_workflow import (
    create_stock_request,
    fulfill_request_item,
    return_packs,
    settle_release,
)


FulfillFormSet = formset_factory(FulfillItemForm, extra=0)


class RoleRequiredMixin:
    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        if request.user.is_superuser or request.user.role in self.allowed_roles:
            return super().dispatch(request, *args, **kwargs)
        messages.error(request, "You do not have permission to access this area.")
        return redirect("dashboard")


class AdminDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = (User.Role.ADMIN,)
    template_name = "pipeline/admin_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["staff_count"] = User.objects.count()
        ctx["company_count"] = Company.objects.count()
        ctx["request_count"] = StockRequest.objects.count()
        ctx["open_releases"] = sum(
            1 for r in PackRelease.objects.select_related("request_item__request")
            if r.packs_outstanding > 0
        )
        return ctx


class InventoryDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
    template_name = "pipeline/inventory_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        inventory = list(
            PackagedInventory.objects.select_related("product__blend", "product__pack_size")
        )
        pending = StockRequest.objects.filter(
            status__in=("pending", "partially_fulfilled")
        ).select_related("requested_by", "company").prefetch_related("items__product__blend", "items__product__pack_size")
        releases = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to", "request_item__request"
        ).order_by("-released_at")[:20]
        ctx.update({
            "inventory": inventory,
            "total_packaged": sum(i.available for i in inventory),
            "pending_requests": pending[:8],
            "pending_request_count": pending.count(),
            "recent_releases": releases,
        })
        return ctx


class SalesWorkspaceView(RoleRequiredMixin, TemplateView):
    allowed_roles = (User.Role.SALES, User.Role.ADMIN)
    template_name = "pipeline/sales_workspace.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx["my_requests"] = StockRequest.objects.filter(requested_by=user).select_related("company").order_by("-requested_at")[:10]
        releases = list(
            PackRelease.objects.filter(released_to=user)
            .select_related("product__blend", "product__pack_size", "request_item__request")
            .order_by("-released_at")[:50]
        )
        ctx["my_releases"] = [r for r in releases if r.packs_outstanding > 0]
        ctx["my_release_count"] = len(ctx["my_releases"])
        ctx["followup_count"] = 0
        try:
            from .models import Followup
            ctx["followup_count"] = Followup.objects.filter(is_converted_to_contract=False).count()
        except Exception:
            pass
        return ctx


class StockRequestCreateView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN)
    template_name = "pipeline/stock_request_form.html"

    def get(self, request):
        return render(request, self.template_name, {
            "form": StockRequestForm(),
            "item_formset": StockRequestItemFormSet(prefix="items"),
        })

    def post(self, request):
        form = StockRequestForm(request.POST)
        formset = StockRequestItemFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            try:
                items = [
                    (row.cleaned_data["product"], row.cleaned_data["quantity_requested"])
                    for row in formset.forms
                    if row.cleaned_data and not row.cleaned_data.get("DELETE")
                ]
                stock_request = create_stock_request(
                    user=request.user,
                    purpose=form.cleaned_data["purpose"],
                    company=form.cleaned_data.get("company"),
                    notes=form.cleaned_data.get("notes", ""),
                    items=items,
                )
                messages.success(request, f"Stock request {stock_request.short_number} submitted to the store.")
                return redirect("stock_request_detail", pk=stock_request.pk)
            except ValidationError as exc:
                form.add_error(None, exc.message)
        return render(request, self.template_name, {"form": form, "item_formset": formset})


class MyStockRequestListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN)
    model = StockRequest
    template_name = "pipeline/stock_request_list.html"
    context_object_name = "requests"

    def get_queryset(self):
        qs = StockRequest.objects.select_related("requested_by", "company").prefetch_related("items__product__blend", "items__product__pack_size")
        if self.request.user.role == User.Role.SALES:
            qs = qs.filter(requested_by=self.request.user)
        return qs


class StockRequestDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN)
    model = StockRequest
    template_name = "pipeline/stock_request_detail.html"
    context_object_name = "stock_request"

    def get_queryset(self):
        qs = StockRequest.objects.select_related("requested_by", "company").prefetch_related(
            "items__product__blend", "items__product__pack_size", "items__releases"
        )
        if self.request.user.role == User.Role.SALES:
            qs = qs.filter(requested_by=self.request.user)
        return qs


class StockRequestFulfillView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
    template_name = "pipeline/stock_request_fulfill.html"

    def get_request(self, pk):
        return get_object_or_404(
            StockRequest.objects.select_related("requested_by", "company").prefetch_related("items__product__blend", "items__product__pack_size", "items__releases"),
            pk=pk,
        )

    def get(self, request, pk):
        stock_request = self.get_request(pk)
        initial = [
            {"item_id": item.pk, "issue_quantity": item.outstanding_quantity, "selling_price": item.product.selling_price}
            for item in stock_request.items.all()
        ]
        return render(request, self.template_name, {
            "stock_request": stock_request,
            "formset": FulfillFormSet(prefix="fulfill", initial=initial),
        })

    def post(self, request, pk):
        stock_request = self.get_request(pk)
        formset = FulfillFormSet(request.POST, prefix="fulfill")
        if formset.is_valid():
            try:
                issued = 0
                for form in formset:
                    qty = form.cleaned_data["issue_quantity"]
                    if qty <= 0:
                        continue
                    item = get_object_or_404(StockRequestItem, pk=form.cleaned_data["item_id"], request=stock_request)
                    if stock_request.purpose == "sale" and form.cleaned_data["selling_price"] <= 0:
                        raise ValidationError("A sale release must have a price per pack.")
                    fulfill_request_item(
                        item=item,
                        quantity=qty,
                        selling_price=form.cleaned_data["selling_price"],
                        manager=request.user,
                        notes=form.cleaned_data.get("notes", ""),
                    )
                    issued += qty
                if issued:
                    messages.success(request, f"{issued} pack(s) issued from request {stock_request.short_number}.")
                else:
                    messages.info(request, "No stock was issued. The request remains open.")
                return redirect("stock_request_detail", pk=stock_request.pk)
            except ValidationError as exc:
                if formset.forms:
                    formset.forms[0].add_error(None, exc.message)
                else:
                    formset._non_form_errors = formset.error_class([str(exc)])
        return render(request, self.template_name, {"stock_request": stock_request, "formset": formset})




class PackReleaseListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN)
    model = PackRelease
    template_name = "pipeline/pack_release_list.html"
    context_object_name = "releases"

    def get_queryset(self):
        qs = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to", "request_item__request"
        ).prefetch_related("returns")
        if self.request.user.role == User.Role.SALES:
            qs = qs.filter(released_to=self.request.user)
        return qs


class PackReleaseDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN)
    model = PackRelease
    template_name = "pipeline/pack_release_detail.html"
    context_object_name = "release"

    def get_queryset(self):
        qs = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to", "request_item__request"
        ).prefetch_related("returns")
        if self.request.user.role == User.Role.SALES:
            qs = qs.filter(released_to=self.request.user)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        release = self.object
        ctx["returns"] = release.returns.all().order_by("-returned_at")
        try:
            ctx["settlement"] = release.settlement
        except PackSettlement.DoesNotExist:
            ctx["settlement"] = None
        return ctx


class SalesStockView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN)
    model = PackRelease
    template_name = "pipeline/sales_stock.html"
    context_object_name = "releases"

    def get_queryset(self):
        qs = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to", "request_item__request"
        ).prefetch_related("returns")
        if self.request.user.role == User.Role.SALES:
            qs = qs.filter(released_to=self.request.user)
        return qs


class PackReturnFromReleaseView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN)
    template_name = "pipeline/pack_return_form.html"

    def get_release(self, request, release_pk):
        qs = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to", "request_item__request"
        ).prefetch_related("returns")
        if request.user.role == User.Role.SALES:
            qs = qs.filter(released_to=request.user)
        return get_object_or_404(qs, pk=release_pk)

    def get(self, request, release_pk):
        release = self.get_release(request, release_pk)
        if release.packs_outstanding <= 0:
            messages.info(request, "There are no packs left to return on this release.")
            return redirect("pack_release_detail", pk=release.pk)
        return render(request, self.template_name, {"release": release, "form": PackReturnCleanForm()})

    def post(self, request, release_pk):
        release = self.get_release(request, release_pk)
        form = PackReturnCleanForm(request.POST)
        if form.is_valid():
            try:
                return_packs(
                    release=release,
                    packs_returned=form.cleaned_data["packs_returned"],
                    reason=form.cleaned_data.get("reason", ""),
                    notes=form.cleaned_data.get("notes", ""),
                    user=request.user,
                )
                messages.success(request, "Returned coffee has been received back into store inventory.")
                return redirect("pack_release_detail", pk=release.pk)
            except ValidationError as exc:
                form.add_error(None, exc.message)
        return render(request, self.template_name, {"release": release, "form": form})


class CashierQueueView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.CASHIER, User.Role.ACCOUNTS, User.Role.ADMIN)
    template_name = "pipeline/order_queue.html"
    context_object_name = "releases"

    def get_queryset(self):
        releases = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to", "request_item__request"
        ).prefetch_related("returns")
        result = []
        for release in releases.order_by("-released_at"):
            if not release.request_item_id or release.request_item.request.purpose != "sale":
                continue
            try:
                settlement = release.settlement
            except PackSettlement.DoesNotExist:
                settlement = None
            if settlement is None or not settlement.is_cleared:
                result.append(release)
        return result


class CashierClearanceView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.CASHIER, User.Role.ACCOUNTS, User.Role.ADMIN)
    template_name = "pipeline/cashier_clearance.html"

    def get_release(self, pk):
        return get_object_or_404(
            PackRelease.objects.select_related(
                "product__blend", "product__pack_size", "released_to", "request_item__request"
            ).prefetch_related("returns"),
            pk=pk,
        )

    def get(self, request, pk):
        release = self.get_release(pk)
        return render(request, self.template_name, {
            "release": release,
            "form": SettlementForm(initial={"packs_sold": release.packs_outstanding, "amount_paid": release.packs_outstanding * release.selling_price}),
        })

    def post(self, request, pk):
        release = self.get_release(pk)
        form = SettlementForm(request.POST)
        if form.is_valid():
            try:
                settlement = settle_release(
                    release=release,
                    packs_sold=form.cleaned_data["packs_sold"],
                    amount_paid=form.cleaned_data["amount_paid"],
                    payment_method=form.cleaned_data["payment_method"],
                    payment_reference=form.cleaned_data.get("payment_reference", ""),
                    cashier=request.user,
                    notes=form.cleaned_data.get("notes", ""),
                )
                if settlement.is_cleared:
                    messages.success(request, "Payment cleared successfully.")
                else:
                    messages.warning(request, f"Payment recorded. Balance remaining: UGX {settlement.balance:,.2f}.")
                return redirect("order_queue")
            except ValidationError as exc:
                form.add_error(None, exc.message)
        return render(request, self.template_name, {"release": release, "form": form})



