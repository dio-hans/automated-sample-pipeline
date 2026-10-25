
from datetime import timedelta
from datetime import timedelta

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, Q, Prefetch, Window
from django.template import context
from django.utils import timezone
from django.conf import settings
from django.core.paginator import Paginator
from django.utils import timezone
from django.db.models import Q, Prefetch
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView
from django.forms import formset_factory
from .sales_forms import PaymentReceiptForm
from .sales_workflow import (
    create_stock_request,
    fulfill_bulk_request_item,
    fulfill_request_item,
    process_supermarket_audit,
    record_installment_payment,
    refresh_stock_request_status,
    record_bulk_sale_payment,
)
from .sales_forms import PackReturnForm
from .models import  AccountHolder, CompanyBranch, ConsignmentInventory, PackagedProduct, PackagingRun, PackagingRun, StockAudit
from .models import (
    Company,
    PackRelease,
    PackReturn,
    PackSettlement,
    CoffeeBatchSale,
    PackagedInventory,
    StockRequest,
    StockRequestItem,
    BulkStockRequestItem,
    User,
)
from .sales_forms import (
    FulfillItemForm,
    SettlementForm,
    StockRequestForm,
    StockRequestItemFormSet,
    BulkStockRequestItemFormSet,
    BulkFulfillFormSet,
    BulkSalePaymentForm,
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
    allowed_roles = (User.Role.ADMIN, User.Role.ACCOUNTS)
    template_name = "pipeline/admin_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["staff_count"] = User.objects.count()
        ctx["company_count"] = Company.objects.count()
        ctx["request_count"] = StockRequest.objects.count()
        ctx["open_releases"] = sum(
            1 for r in PackRelease.objects.select_related("request_item__request")
            .prefetch_related("returns", "payments")
            if r.packs_outstanding > 0
        )
        return ctx


from django.db.models import Sum
from .models import PackagedInventory, StockRequest, PackRelease, CoffeeStock
from .services.inventory import (
    cache_packaged_inventory_totals,
    get_stock_rows_with_cached_inventory,
)

from decimal import Decimal, InvalidOperation
from django.views.generic import TemplateView

class InventoryDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.ACCOUNTS)
    template_name = "pipeline/inventory_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. 📦 PACKAGED INVENTORY & ALERTS
        inventory = cache_packaged_inventory_totals(
            PackagedInventory.objects.select_related("product__blend", "product__pack_size")
        )

        low_stock_items = [
            item for item in inventory
            if item.available <= getattr(item, "reorder_level", 10)
        ]

        # 2. 📋 PENDING PIPELINE REQUESTS
        pending_qs = StockRequest.objects.filter(
            status__in=("pending", "partially_fulfilled")
        ).select_related("requested_by", "company").prefetch_related(
            "items__product__blend", "items__product__pack_size"
        ).order_by("-requested_at")

        pending_requests = list(
            pending_qs.annotate(
                total_count=Window(expression=Count("pk"))
            )[:8]
        )
        pending_count = (
            pending_requests[0].total_count if pending_requests else 0
        )

        # 3. 🚚 RECENT PHYSICAL RELEASES LOG
        releases = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to", "request_item__request"
        ).order_by("-released_at")[:20]

        # 4. 🧮 SAFE BULK COFFEE QUANTITY COMPUTATION
        stocks = get_stock_rows_with_cached_inventory(
            CoffeeStock.objects.all()
        )

        total_green = Decimal("0.00")
        total_roasted = Decimal("0.00")
        total_ground = Decimal("0.00")

        for stock in stocks:
            green_val = stock.quantity_green or 0
            total_green += Decimal(str(green_val))

            roasted_val = stock.quantity_roasted or 0
            total_roasted += Decimal(str(roasted_val))

            ground_val = stock.quantity_ground or 0
            total_ground += Decimal(str(ground_val))

        # 5. CONTEXT INJECTION (Assigned directly to ctx)
        ctx.update({
            "inventory": inventory,
            "low_stock_items": low_stock_items,
            "total_packaged": sum((i.available or 0) for i in inventory),

            "pending_requests": pending_requests,
            "pending_request_count": pending_count,
            "recent_releases": releases,

            "total_green_kg": total_green,
            "total_roasted_kg": total_roasted,
            "total_ground_kg": total_ground,
            "total_bulk_kg": total_green + total_roasted + total_ground,

            "open_packaging_runs": list(
                PackagingRun.objects
                .filter(status="open")
                .annotate(total_count=Window(expression=Count("pk")))
                .order_by("issued_at")[:5]
            ),
        })
        ctx["open_packaging_runs_count"] = (
            ctx["open_packaging_runs"][0].total_count
            if ctx["open_packaging_runs"]
            else 0
        )

        return ctx

class SalesWorkspaceView(RoleRequiredMixin, TemplateView):
    allowed_roles = (User.Role.SALES, User.Role.ADMIN)
    template_name = "pipeline/sales_workspace.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        # ---------------------------------------------------------
        # ACCOUNT HOLDER LINK
        # ---------------------------------------------------------
        account_holder = (
            AccountHolder.objects
            .filter(system_user=user)
            .first()
        )

        # ---------------------------------------------------------
        # MY STOCK REQUESTS
        # ---------------------------------------------------------
        ctx["my_requests"] = (
            StockRequest.objects
            .filter(requested_by=user)
            .select_related("company")
            .order_by("-requested_at")[:10]
        )

        # ---------------------------------------------------------
        # MY RELEASES
        #
        # A release belongs to an AccountHolder via 'released_to'.
        # We find releases belonging directly to this salesperson's
        # AccountHolder account, OR where the AccountHolder is linked
        # to this system user profile.
        # ---------------------------------------------------------
        #  Fixed: We use 'released_to' for account holder matching,
        # and 'released_to__system_user' to trace back to internal users.
        release_filter = Q(released_to=account_holder) | Q(released_to__system_user=user)

        releases = list(
            PackRelease.objects
            .filter(release_filter)
            .select_related(
                "product__blend",
                "product__pack_size",
                "request_item__request",
                "released_to",  # 📝 Pulls the AccountHolder relation cleanly
            )
            .prefetch_related("returns")
            .order_by("-released_at")[:50]
        )

        # Filter down into dispatches that still have outstanding packs
        ctx["my_releases"] = [r for r in releases if r.packs_outstanding > 0]
        ctx["my_release_count"] = len(ctx["my_releases"])

        # ---------------------------------------------------------
        # FOLLOW-UPS
        # ---------------------------------------------------------
        try:
            from .models import Followup
            ctx["followup_count"] = (
                Followup.objects
                .filter(is_converted_to_contract=False)
                .count()
            )
        except Exception:
            ctx["followup_count"] = 0

        # Helpful variables passed directly to your template view context
        ctx["account_holder"] = account_holder
        return ctx


from django.urls import reverse


def get_stock_request_redirect_url(user, stock_request=None):
    """
    Return the correct destination after submitting a stock request.
    """
    role = getattr(user, "role", None)

    # Cashiers go to the order queue.
    if role == User.Role.CASHIER:
        return reverse("order_queue")

    # Sales staff return to the sales workspace.
    if role == User.Role.SALES:
        return reverse("record_sale")

    # Managers may proceed to fulfilment.
    if role == User.Role.MANAGER:
        if stock_request:
            return reverse(
                "stock_request_detail",
                kwargs={"pk": stock_request.pk},
            )
        return reverse("order_queue")

    # Admins should review requests, not automatically fulfil them.
    if role == User.Role.ADMIN:
        if stock_request:
            return reverse(
                "stock_request_detail",
                kwargs={"pk": stock_request.pk},
            )
        return reverse("stock_request_list")

    # Accounts staff should go to their own workspace.
    if role == User.Role.ACCOUNTS:
        return reverse("stock_request_list")

    return reverse("stock_request_list")



class StockRequestCreateView(RoleRequiredMixin, View):
    allowed_roles = (
        User.Role.SALES,
        User.Role.MANAGER,
        User.Role.ADMIN,
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
    )
    template_name = "pipeline/stock_request_form.html"

    def get(self, request):
        item_formset = StockRequestItemFormSet(prefix="items")
        stock_queryset = CoffeeStock.objects.select_related(
            "variety"
        ).order_by("batch_number")
        bulk_formset = BulkStockRequestItemFormSet(
            prefix="bulk",
            stock_queryset=stock_queryset,
        )
        products = list(
            item_formset.forms[0].fields["product"].queryset
        )
        product_ids = [product.pk for product in products]
        inventory = cache_packaged_inventory_totals(
            PackagedInventory.objects
            .filter(product_id__in=product_ids)
            .select_related("product__blend", "product__pack_size")
        )
        available_by_product = {
            item.product_id: item.available
            for item in inventory
        }
        catalog_products = [
            {
                "product": product,
                "available": available_by_product.get(product.pk, 0),
            }
            for product in products
        ]
        stocks = get_stock_rows_with_cached_inventory(stock_queryset)
        bulk_stock_options = [
            {
                "stock": stock,
                "green": stock._stage_quantity_cache.get("green_received", 0),
                "roasted": stock._stage_quantity_cache.get("roasted", 0),
                "ground": stock._stage_quantity_cache.get("ground", 0),
            }
            for stock in stocks
        ]

        return render(
            request,
            self.template_name,
            {
                "form": StockRequestForm(user=request.user),
                "item_formset": item_formset,
                "bulk_formset": bulk_formset,
                "catalog_products": catalog_products,
                "bulk_stock_options": bulk_stock_options,
            },
        )

    def post(self, request):
        is_sales_request = (
            request.user.role == User.Role.SALES
            and not request.user.is_superuser
        )
        form = StockRequestForm(request.POST, user=request.user)
        formset = StockRequestItemFormSet(
            request.POST,
            prefix="items",
        )
        stock_queryset = CoffeeStock.objects.select_related(
            "variety"
        ).order_by("batch_number")
        bulk_formset = BulkStockRequestItemFormSet(
            request.POST,
            prefix="bulk",
            stock_queryset=stock_queryset,
        )

        if form.is_valid() and formset.is_valid() and bulk_formset.is_valid():
            try:
                items = [
                    (
                        row.cleaned_data["product"],
                        row.cleaned_data["quantity_requested"],
                    )
                    for row in formset.forms
                    if row.cleaned_data
                    and not row.cleaned_data.get("DELETE")
                ]
                bulk_items = [
                    (
                        row.cleaned_data["stock"],
                        row.cleaned_data["stage"],
                        row.cleaned_data["quantity_requested"],
                        row.cleaned_data["price_per_kg"],
                        row.cleaned_data.get("buyer_name", ""),
                    )
                    for row in bulk_formset.forms
                    if row.cleaned_data
                    and row.cleaned_data.get("stock")
                    and not row.cleaned_data.get("DELETE")
                ]

                stock_request = create_stock_request(
                    user=request.user,
                    purpose=(
                        "sale"
                        if is_sales_request
                        else form.cleaned_data.get("purpose")
                    ),
                    destination_type=(
                        "agent_float"
                        if is_sales_request
                        else form.cleaned_data.get("destination_type", "")
                    ),
                    company=form.cleaned_data.get("company"),
                    account_holder=(
                        form.sales_account_holder
                        if is_sales_request
                        else form.cleaned_data.get("account_holder")
                    ),
                    notes=form.cleaned_data.get("notes", ""),
                    items=items,
                    bulk_items=bulk_items,
                )

                messages.success(
                    request,
                    f"Stock request {stock_request.short_number} "
                    "submitted to the store.",
                )

                return redirect(
                    get_stock_request_redirect_url(
                        request.user,
                        stock_request,
                    )
                )

            except ValidationError as exc:
                msg = (
                    exc.messages[0]
                    if hasattr(exc, "messages")
                    else str(exc)
                )
                form.add_error(None, msg)

        products = list(formset.forms[0].fields["product"].queryset)
        product_ids = [product.pk for product in products]
        inventory = cache_packaged_inventory_totals(
            PackagedInventory.objects.filter(
                product_id__in=product_ids
            ).select_related("product__blend", "product__pack_size")
        )
        available_by_product = {item.product_id: item.available for item in inventory}
        catalog_products = [
            {"product": product, "available": available_by_product.get(product.pk, 0)}
            for product in products
        ]
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "item_formset": formset,
                "bulk_formset": bulk_formset,
                "catalog_products": catalog_products,
                "bulk_stock_options": [
                    {
                        "stock": stock,
                        "green": stock._stage_quantity_cache.get("green_received", 0),
                        "roasted": stock._stage_quantity_cache.get("roasted", 0),
                        "ground": stock._stage_quantity_cache.get("ground", 0),
                    }
                    for stock in get_stock_rows_with_cached_inventory(
                        stock_queryset
                    )
                ],
            },
        )

from decimal import Decimal


def get_active_issued_stock(user):
    requests = (
        StockRequest.objects
        .select_related("requested_by", "account_holder", "company")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=StockRequestItem.objects.prefetch_related(
                    Prefetch(
                        "releases",
                        queryset=PackRelease.objects.select_related(
                            "product__blend",
                            "product__pack_size",
                            "released_to",
                        ).prefetch_related("settlements", "payments", "returns"),
                    )
                ),
            ),
            Prefetch(
                "bulk_items",
                queryset=BulkStockRequestItem.objects.select_related(
                    "stock__variety"
                ).prefetch_related("sales__payments"),
            ),
        )
        .filter(
            Q(items__releases__isnull=False)
            | Q(bulk_items__sales__isnull=False)
        )
        .distinct()
        .order_by("-requested_at")
    )

    if user.role == User.Role.SALES:
        requests = requests.filter(
            Q(items__releases__released_to__system_user=user)
            | Q(bulk_items__sales__account_holder__system_user=user)
        ).distinct()

    active_orders = []
    for order in requests:
        active_releases = []
        active_bulk_sales = []
        order_value = Decimal("0.00")
        order_outstanding = Decimal("0.00")

        for item in order.items.all():
            for release in item.releases.all():
                if (
                    user.role == User.Role.SALES
                    and (
                        not release.released_to
                        or release.released_to.system_user_id != user.pk
                    )
                ):
                    continue
                if release.outstanding_balance > Decimal("0.00"):
                    active_releases.append(release)
                    order_value += release.net_amount_due
                    order_outstanding += release.outstanding_balance

        for item in order.bulk_items.all():
            for sale in item.sales.all():
                if (
                    user.role == User.Role.SALES
                    and (
                        not sale.account_holder
                        or sale.account_holder.system_user_id != user.pk
                    )
                ):
                    continue
                if sale.outstanding_balance > Decimal("0.00"):
                    active_bulk_sales.append(sale)
                    order_value += sale.total_amount_due
                    order_outstanding += sale.outstanding_balance

        if active_releases or active_bulk_sales:
            order.queue_releases = active_releases
            order.queue_bulk_sales = active_bulk_sales
            order.total_value = order_value
            order.total_outstanding = order_outstanding
            active_orders.append(order)

    standalone_releases = (
        PackRelease.objects
        .filter(request_item__isnull=True)
        .select_related(
            "product__blend",
            "product__pack_size",
            "released_to",
        )
        .prefetch_related("settlements", "payments", "returns")
        .order_by("-released_at")
    )
    if user.role == User.Role.SALES:
        standalone_releases = standalone_releases.filter(
            released_to__system_user=user
        )
    standalone_releases = [
        release
        for release in standalone_releases
        if release.outstanding_balance > Decimal("0.00")
    ]
    return active_orders, standalone_releases


class MyStockRequestListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER)
    model = PackRelease
    template_name = "pipeline/sales_stock.html"
    context_object_name = "releases"

    def get_queryset(self):
        self.active_orders, standalone_releases = get_active_issued_stock(
            self.request.user
        )
        return standalone_releases

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["orders"] = self.active_orders
        return context

class MyStockRequestsListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER)
    model = StockRequest
    template_name = "pipeline/stock_request_list.html"
    context_object_name = "requests"


    def get_queryset(self):
        qs = (
            StockRequest.objects
            .filter(status__in=("pending", "partially_fulfilled"))
            .select_related("requested_by", "company", "account_holder")
            .prefetch_related(
                "items__product__blend",
                "items__product__pack_size",
                "bulk_items__stock__variety",
                "bulk_items__sales__payments",
            )
            .order_by("-requested_at")
        )
        if self.request.user.role == User.Role.SALES:
            qs = qs.filter(requested_by=self.request.user)
        return qs


class BulkSaleListView(RoleRequiredMixin, ListView):
    allowed_roles = (
        User.Role.SALES,
        User.Role.MANAGER,
        User.Role.ADMIN,
        User.Role.ACCOUNTS,
        User.Role.CASHIER,
    )
    model = CoffeeBatchSale
    template_name = "pipeline/bulk_sale_list.html"
    context_object_name = "bulk_sales"

    def get_queryset(self):
        qs = CoffeeBatchSale.objects.select_related(
            "stock__variety",
            "bulk_request_item__request",
            "account_holder",
            "company",
            "created_by",
        ).prefetch_related("payments")
        if self.request.user.role == User.Role.SALES:
            qs = qs.filter(account_holder__system_user=self.request.user)
        return qs.order_by("-sold_at", "-pk")


class BulkSalePaymentView(RoleRequiredMixin, View):
    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.MANAGER,
        User.Role.ADMIN,
    )
    template_name = "pipeline/bulk_sale_payment.html"

    def get_sale(self, pk):
        return get_object_or_404(
            CoffeeBatchSale.objects.select_related(
                "stock__variety",
                "bulk_request_item__request",
                "account_holder",
                "company",
            ).prefetch_related("payments"),
            pk=pk,
        )

    def get(self, request, pk):
        sale = self.get_sale(pk)
        if sale.outstanding_balance <= 0:
            messages.info(request, "This bulk sale is fully paid.")
            return redirect("bulk_sales")
        return render(
            request,
            self.template_name,
            {
                "sale": sale,
                "form": BulkSalePaymentForm(
                    initial={"amount": sale.outstanding_balance}
                ),
            },
        )

    @transaction.atomic
    def post(self, request, pk):
        sale = self.get_sale(pk)
        form = BulkSalePaymentForm(request.POST)
        if form.is_valid():
            try:
                payment = record_bulk_sale_payment(
                    bulk_sale=sale,
                    amount=form.cleaned_data["amount"],
                    method=form.cleaned_data["payment_method"],
                    payment_reference=form.cleaned_data["payment_reference"],
                    collected_by=request.user,
                    notes=form.cleaned_data["notes"],
                )
                messages.success(
                    request,
                    f"UGX {payment.amount:,.0f} recorded against bulk sale #{sale.pk}.",
                )
                return redirect("bulk_sales")
            except ValidationError as exc:
                form.add_error(None, exc)
        return render(
            request,
            self.template_name,
            {"sale": sale, "form": form},
        )


class StockRequestDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
    model = StockRequest
    # 🎯 FIX: Point to detail template, NOT fulfill template
    template_name = "pipeline/stock_request_detail.html"
    context_object_name = "stock_request"

    def get_queryset(self):
        qs = StockRequest.objects.select_related(
            "requested_by", "company", "account_holder"
        ).prefetch_related(
            "items__product__blend",
            "items__product__pack_size",
            "items__releases",
            "bulk_items__stock__variety",
            "bulk_items__sales",
        )
        if self.request.user.role == User.Role.SALES:
            qs = qs.filter(requested_by=self.request.user)
        return qs

    
class StockRequestFulfillView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.MANAGER)
    template_name = "pipeline/stock_request_fulfill.html"

    def get_request(self, pk):
        return get_object_or_404(
            StockRequest.objects.select_related(
                "requested_by", "company", "account_holder"
            ).prefetch_related(
                "items__product__blend",
                "items__product__pack_size",
                "items__releases__payments",
                "items__releases__returns",
                "items__releases__settlements",
                "bulk_items__stock__variety",
                "bulk_items__sales__payments",
            ),
            pk=pk,
        )

    def get(self, request, pk):
        stock_request = self.get_request(pk)
        initial = [
            {"item_id": item.pk, "issue_quantity": item.outstanding_quantity, "selling_price": item.product.selling_price, "packaged_items": list(stock_request.items.all()),
"bulk_items": list(stock_request.bulk_items.all()),}
            for item in stock_request.items.all()
        ]
        bulk_initial = [
            {
                "item_id": item.pk,
                "issue_quantity": item.outstanding_quantity,
                "price_per_kg": item.price_per_kg,
            }
            for item in stock_request.bulk_items.all()
        ]
        return render(request, self.template_name, {
            "stock_request": stock_request,
            "formset": FulfillFormSet(prefix="fulfill", initial=initial),
            "bulk_formset": BulkFulfillFormSet(
                prefix="bulk_fulfill",
                initial=bulk_initial,
            ),
        })

    @transaction.atomic
    def _fulfill_lines(self, *, request, stock_request, formset, bulk_formset):
        issued = 0
        for form in formset:
            item = get_object_or_404(
                StockRequestItem,
                pk=form.cleaned_data["item_id"],
                request=stock_request,
            )
            quantity = form.cleaned_data["issue_quantity"]
            if quantity <= 0:
                continue
            if stock_request.purpose == "sale" and form.cleaned_data["selling_price"] <= 0:
                raise ValidationError("A sale release must have a price per pack.")
            fulfill_request_item(
                item=item,
                quantity=quantity,
                selling_price=form.cleaned_data["selling_price"],
                manager=request.user,
                notes=form.cleaned_data.get("notes", ""),
            )
            issued += quantity

        bulk_issued = Decimal("0.00")
        for form in bulk_formset:
            item = get_object_or_404(
                BulkStockRequestItem,
                pk=form.cleaned_data["item_id"],
                request=stock_request,
            )
            quantity = form.cleaned_data["issue_quantity"]
            if quantity <= 0:
                continue
            fulfill_bulk_request_item(
                item=item,
                quantity=quantity,
                price_per_kg=form.cleaned_data["price_per_kg"],
                user=request.user,
            )
            bulk_issued += quantity

        return refresh_stock_request_status(stock_request), issued, bulk_issued

    def post(self, request, pk):
        stock_request = self.get_request(pk)
        formset = FulfillFormSet(request.POST, prefix="fulfill")
        bulk_formset = BulkFulfillFormSet(request.POST, prefix="bulk_fulfill")

        if formset.is_valid() and bulk_formset.is_valid():
            try:
                stock_request, issued, bulk_issued = self._fulfill_lines(
                    request=request,
                    stock_request=stock_request,
                    formset=formset,
                    bulk_formset=bulk_formset,
                )
                if stock_request.status == "fulfilled":
                    messages.success(
                        request,
                        f"Stock request {stock_request.short_number} is fully fulfilled.",
                    )
                elif issued or bulk_issued:
                    messages.warning(
                        request,
                        f"Partial dispatch recorded for request {stock_request.short_number}.",
                    )
                else:
                    messages.info(request, "No stock was dispatched. The request remains open.")
                return redirect("inventory_dashboard")
            except ValidationError as exc:
                error = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if formset.forms:
                    formset.forms[0].add_error(None, error)
                elif bulk_formset.forms:
                    bulk_formset.forms[0].add_error(None, error)
                else:
                    formset._non_form_errors = formset.error_class([error])

                return render(
                    request,
                    self.template_name,
                    {
                        "stock_request": stock_request,
                        "formset": formset,
                        "bulk_formset": bulk_formset,
                    },
                )


class PackReleaseListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.ACCOUNTS, User.Role.CASHIER)
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
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.ACCOUNTS, User.Role.CASHIER)
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
        self.active_orders, standalone_releases = get_active_issued_stock(
            self.request.user
        )
        return standalone_releases

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["orders"] = self.active_orders
        return context



class CashierQueueView(RoleRequiredMixin, ListView):
    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.ADMIN,
    )

    template_name = "pipeline/order_queue.html"
    context_object_name = "orders"  # Grouped by Order (StockRequest) instead of single releases

    def get_queryset(self):
        from .utils.util import apply_date_filters

        # ---------------------------------------------------------
        # BASE QUERYSET: Stock Requests for Sales
        # ---------------------------------------------------------
        qs = (
            StockRequest.objects.filter(purpose="sale")
            .select_related(
                "requested_by",
                "account_holder",
                "company",
            )
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=StockRequestItem.objects.select_related(
                        "product__blend",
                        "product__pack_size",
                    ).prefetch_related(
                        "releases__returns",
                        "releases__settlements",
                        "releases__payments",
                    ),
                ),
                Prefetch(
                    "bulk_items",
                    queryset=BulkStockRequestItem.objects.select_related(
                        "stock__variety",
                    ).prefetch_related(
                        "sales__payments",
                    ),
                ),
            )
            .order_by("-requested_at")
        )

        # ---------------------------------------------------------
        # DATE FILTER
        # ---------------------------------------------------------
        qs, preset, today, start, end = apply_date_filters(
            self.request,
            qs,
            "requested_at",
        )

        self._preset = preset

        # ---------------------------------------------------------
        # ORDER QUEUE FILTERING
        # Only keep orders that have active releases with money outstanding.
        # ---------------------------------------------------------
        orders = []
        for order in qs:
            releases = []
            total_value = Decimal("0.00")
            total_paid = Decimal("0.00")
            total_outstanding = Decimal("0.00")
            total_issued = 0
            total_bulk_issued = Decimal("0.00")
            total_remaining = 0
            bulk_sales = []

            # Aggregate across all items and releases under this single order card
            for item in order.items.all():
                total_remaining += item.outstanding_quantity
                for release in item.releases.all():
                    total_issued += release.packs_out
                    total_value += release.gross_amount
                    total_paid += release.total_amount_paid
                    release_outstanding = release.outstanding_balance
                    total_outstanding += release_outstanding
                    
                    # 🎯 FIX: Only append releases that have an active outstanding balance
                    if release_outstanding > Decimal("0.00"):
                        releases.append(release)

            for item in order.bulk_items.all():
                total_remaining += item.outstanding_quantity
                for sale in item.sales.all():
                    total_bulk_issued += sale.kg_sold
                    total_value += sale.total_amount_due
                    total_paid += sale.total_amount_paid
                    sale_outstanding = sale.outstanding_balance
                    total_outstanding += sale_outstanding
                    if sale_outstanding > Decimal("0.00"):
                        bulk_sales.append(sale)

            # Keep order in queue if balance remains
            if total_outstanding <= Decimal("0.00"):
                continue

            # Attach transient values used only by the template.
            order.queue_releases = releases
            order.queue_bulk_sales = bulk_sales
            order.total_value = total_value
            order.total_paid = total_paid
            order.total_outstanding = total_outstanding
            order.total_issued = total_issued
            order.total_bulk_issued = total_bulk_issued
            order.total_remaining = total_remaining
            orders.append(order)

        return orders

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["preset"] = getattr(
            self,
            "_preset",
            "this_month",
        )
        return context

class CashierClearanceView(RoleRequiredMixin, View):
    allowed_roles = ("cashier", User.Role.CASHIER, User.Role.ACCOUNTS, User.Role.ADMIN)
    template_name = "pipeline/cashier_clearance.html"

    def get_release(self, pk):
        return get_object_or_404(
            PackRelease.objects.select_related(
                "product__blend", "product__pack_size", "released_to", "request_item__request"
            ).prefetch_related("returns"),
            pk=pk,
        )]

    def get(self, request, pk):
    release = self.get_release(pk)
    return render(
        request,
        self.template_name,
        {
            "release": release,
            "form": SettlementForm(initial={
                "packs_sold": release.packs_outstanding,
                "amount_paid": release.packs_outstanding * release.selling_price
            }),
        },
    )

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
                    messages.warning(
                        request,
                        f"Sale recorded. Balance remaining: "
                        f"UGX {settlement.release.outstanding_balance:,.0f}.",
                    )
                return redirect("order_queue")
            except ValidationError as exc:
                form.add_error(None, exc.message)
        return render(request, self.template_name, {"release": release, "form": form})



class RecordInstallmentPaymentView(RoleRequiredMixin, View):
    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.MANAGER,
        User.Role.ADMIN,
    )

    template_name = "pipeline/record_payment_form.html"

    def get_release(self, pk):
        return get_object_or_404(
            PackRelease.objects.select_related(
                "product__blend",
                "product__pack_size",
                "released_to",
                "company",
            ).prefetch_related("returns", "payments"),
            pk=pk,
        )

    def get(self, request, pk):
        release = self.get_release(pk)

        if release.is_fully_cleared:
            messages.info(
                request,
                "This account has already been fully cleared.",
            )
            return redirect(
                "pack_release_detail",
                pk=release.pk,
            )
        return render(
            request,
            self.template_name,
            {"release": release, "form": SettlementForm()},
        )  # FIXED: Added missing closing parenthesis

    def post(self, request, pk):
        release = self.get_release(pk)
        form = SettlementForm(request.POST)

        if form.is_valid():
            try:
                payment = record_installment_payment(
                    release=release,
                    amount=form.cleaned_data["amount_paid"],
                    method=form.cleaned_data["payment_method"],
                    payment_reference=form.cleaned_data.get("payment_reference", ""),
                    collected_by=request.user,
                    notes=form.cleaned_data.get("notes", ""),
                )
                messages.success(
                    request,
                    f"UGX {payment.amount:,.0f} payment recorded successfully.",
                )
                return redirect("pack_release_detail", pk=release.pk)
            except ValidationError as exc:
                form.add_error(None, exc)

        return render(
            request,
            self.template_name,
            {"release": release, "form": form},
        )

from decimal import Decimal, InvalidOperation
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.views import View
from .models import StockRequest, User
from .sales_workflow import record_installment_payment, settle_release

class ProcessOrderPaymentView(RoleRequiredMixin, View):
    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.MANAGER,
        User.Role.ADMIN,
    )

    @transaction.atomic
    def post(self, request, pk):
        order = get_object_or_404(StockRequest, pk=pk)
        
        # 1. Parse raw payment input
        raw_amount = request.POST.get("amount", "0")
        try:
            amount_received = Decimal(str(raw_amount))
        except (ValueError, TypeError, InvalidOperation):
            messages.error(request, "Invalid payment amount entered.")
            return redirect("order_queue")

        if amount_received <= Decimal("0"):
            messages.error(request, "Payment amount must be greater than zero.")
            return redirect("order_queue")

        payment_method = request.POST.get("method", "cash")
        payment_reference = request.POST.get("payment_reference", "")
        notes = request.POST.get("notes", "")

        remaining_payment = amount_received
        total_applied = Decimal("0.00")

        # 2. Distribute payment across releases under this order card
        for item in order.items.prefetch_related("releases").all():
            for release in item.releases.all():
                if remaining_payment <= Decimal("0.00"):
                    break
                
                due = release.outstanding_balance
                if due > Decimal("0.00"):
                    pay_amount = min(remaining_payment, due)
                    
                    # Call domain helper to record settlement
                    record_installment_payment(
                        release=release,
                        amount=pay_amount,
                        method=payment_method,
                        payment_reference=payment_reference,
                        collected_by=request.user,
                        notes=notes,
                    )
                    
                    remaining_payment -= pay_amount
                    total_applied += pay_amount

        # 3. User feedback and return to Order Queue
        if total_applied > Decimal("0.00"):
            messages.success(
                request,
                f"Successfully posted UGX {total_applied:,.0f} payment for Order #{order.short_number}."
            )
        else:
            messages.warning(request, "No active outstanding items were found to settle.")

        return redirect("order_queue")
    
class PackReturnFromReleaseView(RoleRequiredMixin, View):
    allowed_roles = (
        User.Role.SALES,
        User.Role.MANAGER,
        User.Role.ADMIN,
    )

    template_name = "pipeline/pack_return_form.html"

    def get_release(self, request, release_pk):
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
        )

        if request.user.role == User.Role.SALES:
            qs = qs.filter(
                released_to__system_user=request.user
            )

        return get_object_or_404(
            qs,
            pk=release_pk,
        )

    def get(self, request, release_pk):
        release = self.get_release(
            request,
            release_pk,
        )

        if release.packs_outstanding <= 0:
            messages.info(
                request,
                "There are no packs left to return on this release.",
            )

            return redirect(
                "pack_release_detail",
                pk=release.pk,
            )

        return render(
            request,
            self.template_name,
            {
                "release": release,
                "form": PackReturnForm(),
            },
        )

    def post(self, request, release_pk):
        release = self.get_release(
            request,
            release_pk,
        )

        form = PackReturnForm(request.POST)

        if form.is_valid():
            try:
                returned = return_packs(
                    release=release,
                    packs_returned=form.cleaned_data[
                        "packs_returned"
                    ],
                    condition=form.cleaned_data[
                        "condition"
                    ],
                    disposition=form.cleaned_data[
                        "disposition"
                    ],
                    reason=form.cleaned_data.get(
                        "reason",
                        "",
                    ),
                    notes=form.cleaned_data.get(
                        "notes",
                        "",
                    ),
                    user=request.user,
                )

                messages.success(
                    request,
                    f"{returned.packs_returned} pack(s) "
                    "returned successfully.",
                )

                return redirect(
                    "pack_release_detail",
                    pk=release.pk,
                )

            except ValidationError as exc:
                form.add_error(
                    None,
                    str(exc),
                )

        return render(
            request,
            self.template_name,
            {
                "release": release,
                "form": form,
            },
        )

    class AccountHolderLedgerView(RoleRequiredMixin, DetailView):
        """
        Dedicated view to display an individual's personal statement:
        - Total goods taken (value)
        - Total paid
        - Net outstanding debt
        """
    model = AccountHolder
    template_name = "pipeline/account_ledger_detail.html"
    context_object_name = "account"
    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.MANAGER,
        User.Role.ADMIN,
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        account = self.object

        # Fetch all releases made to this person
        releases = PackRelease.objects.filter(released_to=account).select_related(
            "product__blend", "product__pack_size"
        ).prefetch_related("payments", "returns")

        # High-performance DB summaries
        total_taken_value = sum(r.gross_amount for r in releases)
        total_paid = sum(r.total_amount_paid for r in releases)
        net_owed = sum(r.outstanding_balance for r in releases)

        context.update({
            "releases": releases,
            "total_taken_value": total_taken_value,
            "total_paid": total_paid,
            "net_owed": net_owed,
        })
        return context

#consignment views
class ConsignmentListView(RoleRequiredMixin, ListView):
    allowed_roles = (
        User.Role.SALES,
        User.Role.MANAGER,
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.ADMIN,
    )
    template_name = "pipeline/consignment_list.html"
    context_object_name = "companies"

    def get_queryset(self):
        return (
            Company.objects
            .filter(display_inventories__isnull=False)
            .distinct()
            .select_related("account_holder")
            .prefetch_related(
                "display_inventories__product__blend",
                "display_inventories__product__pack_size",
                "audits__items__product",
            )
            .order_by("name")
        )


# ------------------------------------------------------------
# SUPERMARKET CONSIGNMENT DETAIL
# ------------------------------------------------------------
class ConsignmentDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = (
        User.Role.SALES,
        User.Role.MANAGER,
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.ADMIN,
    )
    model = Company
    template_name = "pipeline/consignment_detail.html"
    context_object_name = "company"

    def get_queryset(self):
        return (
            Company.objects
            .select_related("account_holder")
            .prefetch_related(
                "display_inventories__product__blend",
                "display_inventories__product__pack_size",
                "audits__items__product",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = self.object

        display_releases = list(
            PackRelease.objects
            .filter(company=company, purpose="display")
            .select_related("product__blend", "product__pack_size", "released_to")
            .prefetch_related("returns", "settlements")
            .order_by("-released_at")
        )

        context["display_releases"] = display_releases
        context["display_value"] = sum(
            r.gross_amount for r in display_releases
        )
        context["display_paid"] = sum(
            r.total_amount_paid for r in display_releases
        )
        context["display_outstanding"] = sum(
            r.outstanding_balance for r in display_releases
        )
        context["audits"] = company.audits.all().order_by("-audit_date")[:10]
        return context


# ------------------------------------------------------------
# SUPERMARKET AUDIT
# ------------------------------------------------------------
class ConsignmentAuditView(RoleRequiredMixin, View):
    allowed_roles = (
        User.Role.SALES,
        User.Role.MANAGER,
        User.Role.ADMIN,
    )
    template_name = "pipeline/consignment_audit.html"

    def get_company(self, pk):
        return get_object_or_404(Company, pk=pk)

    def get(self, request, pk):
        company = self.get_company(pk)
        inventories = list(
            ConsignmentInventory.objects
            .filter(company=company)
            .select_related("product__blend", "product__pack_size")
            .order_by("product__blend__name", "product__pack_size__grams")
        )
        return render(
            request,
            self.template_name,
            {"company": company, "inventories": inventories},
        )

    def post(self, request, pk):
        company = self.get_company(pk)
        product_ids = request.POST.getlist("product_id")
        audit_rows = []

        for product_id in product_ids:
            actual_key = f"actual_count_{product_id}"
            price_key = f"selling_price_{product_id}"

            actual_raw = request.POST.get(actual_key, "0").strip()
            price_raw = request.POST.get(price_key, "0").strip()

            try:
                actual_count = int(actual_raw)
                selling_price = Decimal(price_raw)
            except (ValueError, TypeError, InvalidOperation):
                messages.error(request, "One or more audit values are invalid.")
                return self.get(request, pk)

            product = get_object_or_404(PackagedProduct, pk=product_id)
            audit_rows.append({
                "product": product,
                "actual_count": actual_count,
                "selling_price": selling_price,
            })

        try:
            audit, total_invoice_amount = process_supermarket_audit(
                company=company,
                user=request.user,
                audit_items_data=audit_rows,
                notes=request.POST.get("notes", "").strip(),
            )
            messages.success(
                request,
                f"Audit saved. Newly billable display sales: UGX {total_invoice_amount:,.0f}.",
            )
            return redirect("consignment_detail", pk=company.pk)
        except ValidationError as exc:
            messages.error(request, str(exc))
            return self.get(request, pk)


# IMPORTANT: PackRelease.released_to is AccountHolder, not User.
# In PackReleaseListView.get_queryset() and PackReleaseDetailView.get_queryset(),
# change the SALES filters to:
#     qs = qs.filter(released_to__system_user=self.request.user)
# instead of `released_to=self.request.user`.

# PackReleaseDetailView should also prefetch settlements:
#     .prefetch_related("returns", "settlements")

# Add this get_queryset override body inside ConsignmentListView after retrieving qs,
# so the template has a trustworthy display total without doing arithmetic in Django templates.
# If you prefer, replace ConsignmentListView with the version below:

class ConsignmentListView(RoleRequiredMixin, ListView):
    allowed_roles = (
        User.Role.SALES,
        User.Role.MANAGER,
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.ADMIN,
    )
    template_name = "pipeline/consignment_list.html"
    context_object_name = "companies"

    def get_queryset(self):
        qs = list(
            Company.objects
            .filter(display_inventories__isnull=False)
            .distinct()
            .select_related("account_holder")
            .prefetch_related(
                "display_inventories__product__blend",
                "display_inventories__product__pack_size",
                "audits__items__product",
            )
            .order_by("name")
        )
        for company in qs:
            inventories = list(company.display_inventories.all())
            company.display_inventory_count = len(inventories)
            company.display_total_packs = sum(i.current_display_quantity for i in inventories)
            company.last_display_audit = company.audits.all().order_by("-audit_date").first()
        return qs


class ConsignmentAuditListView(RoleRequiredMixin, View):
    """
    Global audit schedule across every active consignment branch.

    Designed to remain usable when Nonda has hundreds of branches.
    """

    allowed_roles = (
                    User.Role.ACCOUNTS,
                    User.Role.ADMIN,)
    
    template_name = "consignments/consignment_audit_list.html"

    def get(self, request):

        interval_days = getattr(
            settings,
            "CONSIGNMENT_AUDIT_INTERVAL_DAYS",
            14,
        )

        search = request.GET.get(
            "q",
            "",
        ).strip()

        status_filter = request.GET.get(
            "status",
            "",
        ).strip()

        branches = (
            CompanyBranch.objects
            .filter(is_active=True)
            .select_related("company")
            .prefetch_related(
                "consignment_inventories",
                Prefetch(
                    "audits",
                    queryset=(
                        StockAudit.objects
                        .filter(status="approved")
                        .order_by(
                            "-approved_at",
                            "-audit_date",
                        )
                    ),
                    to_attr="approved_audits",
                ),
                Prefetch(
                    "audits",
                    queryset=(
                        StockAudit.objects
                        .filter(status="pending_approval")
                        .order_by("-audit_date")
                    ),
                    to_attr="pending_audits",
                ),
            )
            .order_by(
                "company__name",
                "branch_name",
            )
        )

        if search:
            branches = branches.filter(
                Q(company__name__icontains=search)
                | Q(branch_name__icontains=search)
                | Q(address__icontains=search)
            )

        today = timezone.localdate()

        rows = []

        for branch in branches:

            inventories = list(
                branch.consignment_inventories.all()
            )

            stock_on_hand = sum(
                inventory.total_consignment_stock
                for inventory in inventories
            )

            latest_audit = (
                branch.approved_audits[0]
                if branch.approved_audits
                else None
            )

            pending_audit = (
                branch.pending_audits[0]
                if branch.pending_audits
                else None
            )


            # -------------------------------------------------
            # NO STOCK
            # -------------------------------------------------

            if stock_on_hand <= 0:
                audit_status = "no_stock"
                last_audited = (
                    latest_audit.approved_at
                    if latest_audit
                    else None
                )
                due_date = None
                days_value = None


            # -------------------------------------------------
            # NEVER AUDITED
            # -------------------------------------------------

            elif latest_audit is None:
                audit_status = "due"
                last_audited = None
                due_date = today
                days_value = 0


            else:

                audit_datetime = (
                    latest_audit.approved_at
                    or latest_audit.audit_date
                )

                last_date = timezone.localtime(
                    audit_datetime
                ).date()

                due_date = (
                    last_date
                    + timedelta(days=interval_days)
                )

                last_audited = audit_datetime

                days_value = (
                    due_date - today
                ).days

                if days_value < 0:
                    audit_status = "overdue"

                elif days_value == 0:
                    audit_status = "due"

                elif days_value <= 3:
                    audit_status = "due_soon"

                else:
                    audit_status = "ok"


            # Pending overrides scheduling status visually.
            if pending_audit:
                audit_status = "pending"


            rows.append({
                "branch": branch,
                "company": branch.company,
                "stock_on_hand": stock_on_hand,
                "last_audited": last_audited,
                "due_date": due_date,
                "days_value": days_value,
                "audit_status": audit_status,
                "pending_audit": pending_audit,
            })


        priority = {
            "overdue": 0,
            "due": 1,
            "due_soon": 2,
            "pending": 3,
            "ok": 4,
            "no_stock": 5,
        }

        if status_filter:
            rows = [
                row
                for row in rows
                if row["audit_status"] == status_filter
            ]

        rows.sort(
            key=lambda row: (
                priority.get(
                    row["audit_status"],
                    99,
                ),
                row["due_date"] or today,
                row["company"].name.lower(),
                row["branch"].branch_name.lower(),
            )
        )


        overdue_count = sum(
            row["audit_status"] == "overdue"
            for row in rows
        )

        due_count = sum(
            row["audit_status"] in {
                "due",
                "due_soon",
            }
            for row in rows
        )

        pending_count = sum(
            row["audit_status"] == "pending"
            for row in rows
        )


        paginator = Paginator(
            rows,
            50,
        )

        page_obj = paginator.get_page(
            request.GET.get("page")
        )


        return render(
            request,
            self.template_name,
            {
                "page_obj": page_obj,
                "rows": page_obj.object_list,
                "search": search,
                "status_filter": status_filter,
                "interval_days": interval_days,
                "overdue_count": overdue_count,
                "due_count": due_count,
                "pending_count": pending_count,
            },
        )