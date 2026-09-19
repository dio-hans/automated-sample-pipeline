from django.db.models import Q
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView
from django.forms import formset_factory
from .sales_forms import PackReturnForm, PaymentReceiptForm
from .sales_workflow import record_payment
from .models import  AccountHolder
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


from django.db.models import Sum
from .models import PackagedInventory, StockRequest, PackRelease, CoffeeStock

from decimal import Decimal
from django.views.generic import TemplateView

class InventoryDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN)
    template_name = "pipeline/inventory_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. 📦 PACKAGED INVENTORY & ALERTS
        inventory = list(
            PackagedInventory.objects.select_related("product__blend", "product__pack_size")
        )

        low_stock_items = [
            item for item in inventory 
            if getattr(item, "is_low_stock", (item.available or 0) <= getattr(item, "reorder_level", 10))
        ]

        # 2. 📋 PENDING PIPELINE REQUESTS
        pending_qs = StockRequest.objects.filter(
            status__in=("pending", "partially_fulfilled")
        ).select_related("requested_by", "company").prefetch_related(
            "items__product__blend", "items__product__pack_size"
        ).order_by("-requested_at")  # ✅ Matches model schema

        pending_count = pending_qs.count()
        pending_requests = list(pending_qs[:8])

        # 3. 🚚 RECENT PHYSICAL RELEASES LOG
        releases = PackRelease.objects.select_related(
            "product__blend", "product__pack_size", "released_to", "request_item__request"
        ).order_by("-released_at")[:20]

        # 4. 🧮 SAFE BULK COFFEE QUANTITY COMPUTATION
        # Computes quantities safely using model properties/fallbacks without throwing SQL FieldErrors
        stocks = CoffeeStock.objects.all()

        total_green = Decimal("0.00")
        total_roasted = Decimal("0.00")
        total_ground = Decimal("0.00")

        for stock in stocks:
            # Fallback checks handle dynamic model properties safely
            green_val = getattr(stock, "quantity_green", getattr(stock, "quantity_after_sorting", 0)) or 0
            total_green += Decimal(str(green_val))

            roasted_val = getattr(stock, "quantity_roasted", 0) or 0
            total_roasted += Decimal(str(roasted_val))

            ground_val = getattr(stock, "quantity_ground", 0) or 0
            total_ground += Decimal(str(ground_val))

        # 5. CONTEXT INJECTION
        ctx.update({
            "inventory": inventory,
            "low_stock_items": low_stock_items,
            "total_packaged": sum((i.available or 0) for i in inventory),

            "pending_requests": pending_requests,
            "pending_request_count": pending_count,
            "recent_releases": releases,

            # Warehouse lot capacity values
            "total_green_kg": total_green,
            "total_roasted_kg": total_roasted,
            "total_ground_kg": total_ground,
            "total_bulk_kg": total_green + total_roasted + total_ground,
        })
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

class StockRequestCreateView(RoleRequiredMixin, View):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER)
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
                    account_holder=form.cleaned_data.get("account_holder"),
                    notes=form.cleaned_data.get("notes", ""),
                    items=items,
                )
                messages.success(request, f"Stock request {stock_request.short_number} submitted to the store.")
                return redirect("record_sale")
            except ValidationError as exc:
                form.add_error(None, exc.message)
        return render(request, self.template_name, {"form": form, "item_formset": formset})


class MyStockRequestListView(RoleRequiredMixin, ListView):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER)
    model = StockRequest
    template_name = "pipeline/stock_request_list.html"
    context_object_name = "requests"

    def get_queryset(self):
        qs = (
            StockRequest.objects
            .filter(status__in=("pending", "partially_fulfilled"))  # ✂️ Hides fulfilled & cancelled orders
            .select_related("requested_by", "company")
            .prefetch_related("items__product__blend", "items__product__pack_size")
            .order_by("-requested_at")
        )
        if self.request.user.role == User.Role.SALES:
            qs = qs.filter(requested_by=self.request.user)
        return qs


class StockRequestDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN, User.Role.CASHIER)
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
    allowed_roles = (User.Role.MANAGER, User.Role.ADMIN, User.Role.SALES, User.Role.CASHIER)
    template_name = "pipeline/stock_request_fulfill.html"

    def get_request(self, pk):
        return get_object_or_404(
            StockRequest.objects.select_related("requested_by", "company").prefetch_related(
                "items__product__blend", "items__product__pack_size", "items__releases"
            ),
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
                    item_id = form.cleaned_data["item_id"]
                    qty = form.cleaned_data["issue_quantity"]
                    item = get_object_or_404(StockRequestItem, pk=item_id, request=stock_request)
                    
                    # ✅ FIXED: If they put 0, it means they have nothing to issue right now.
                    # We skip issuing any stock, but leave the item line open as a partial delivery.
                    if qty <= 0:
                        continue

                    if qty < 0:
                        raise ValidationError("Issue quantity cannot be a negative number.")
                        
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

                # 🔄 FORCE CACHE REFRESH: This completely wipes the old memory cache out
                stock_request.refresh_from_db()
                
                # 📊 Fetch the brand-new quantities straight from the database now
                fresh_items = stock_request.items.all()
                
                if all(i.outstanding_quantity == 0 for i in fresh_items):
                    # If everything requested has been completely issued out, close the ticket!
                    stock_request.status = "fulfilled"
                    stock_request.fulfilled_at = timezone.now()
                    messages.success(request, f"🎉 Stock request {stock_request.short_number} has been fully completed and closed.")
                else:
                    # If any item still has a remaining balance, it safely remains a partial dispatch
                    stock_request.status = "partially_fulfilled"
                    messages.warning(request, f"⏳ Partial dispatch recorded for request {stock_request.short_number}.")

                stock_request.save(update_fields=["status", "fulfilled_at"])
                
                return redirect("stock_request_list")
                
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



class CashierQueueView(RoleRequiredMixin, ListView):
    allowed_roles = (
        User.Role.CASHIER,
        User.Role.ACCOUNTS,
        User.Role.ADMIN,
    )

    template_name = "pipeline/order_queue.html"
    context_object_name = "releases"

    def get_queryset(self):
        from .utils.util import apply_date_filters

        qs = (
            PackRelease.objects
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
            .order_by("-released_at")
        )

        # ---------------------------------------------------------
        # DATE FILTER
        # ---------------------------------------------------------
        qs, preset, today, start, end = apply_date_filters(
            self.request,
            qs,
            "released_at",
        )

        self._preset = preset

        # ---------------------------------------------------------
        # CASHIER QUEUE
        # Only sales releases that are still awaiting clearance.
        # ---------------------------------------------------------
        result = []

        for release in qs:

            # Ignore releases not generated from a sale request
            if (
                not release.request_item_id
                or release.request_item.request.purpose != "sale"
            ):
                continue

            # Only keep releases that still have money outstanding
            if release.outstanding_balance > 0:
                result.append(release)

        return result

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

        form = PaymentReceiptForm()

        return render(
            request,
            self.template_name,
            {
                "release": release,
                "form": form,
            },
        )

    def post(self, request, pk):
        release = self.get_release(pk)

        form = PaymentReceiptForm(request.POST)

        if form.is_valid():
            try:
                payment = record_payment(
                    release=release,
                    amount=form.cleaned_data["amount"],
                    method=form.cleaned_data["method"],
                    payment_reference=form.cleaned_data.get(
                        "payment_reference",
                        "",
                    ),
                    collected_by=request.user,
                    notes=form.cleaned_data.get(
                        "notes",
                        "",
                    ),
                )

                messages.success(
                    request,
                    f"UGX {payment.amount:,.2f} payment "
                    f"recorded successfully.",
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
                "form": PackReturnCleanForm(),
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