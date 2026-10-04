"""New branch-level views. Import in web/urls.py; old sales_views classes remain unused."""
from datetime import timedelta

from django.conf import settings
from django.core.paginator import Paginator
from django.utils import timezone
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView

from .sales_workflow import create_stock_request

from .sales_forms import StockRequestItemFormSet
from .consignment_forms import (
    BranchForm,
    ConsigneeCreateForm,
    ShelfTransferForm,
    BranchReturnForm,
    AuditDecisionForm,
)

from .consignment_forms import BranchForm, ConsignmentDeliveryForm, ShelfTransferForm, BranchReturnForm, AuditDecisionForm
from .models import (
    BranchStockLedger, Company, CompanyBranch, ConsignmentInventory,
    ConsignmentInvoice, PackagedProduct, PackRelease, StockAudit, User,
)
from .sales_views import RoleRequiredMixin
from .services.consignments import (
    approve_branch_audit, record_consignment_delivery,
    record_good_branch_return, reject_branch_audit, submit_branch_audit,
    transfer_to_shelf,
)

READ_ROLES = (User.Role.SALES, User.Role.MANAGER, User.Role.CASHIER,
              User.Role.ACCOUNTS, User.Role.ADMIN)
FIELD_ROLES = (User.Role.SALES, User.Role.MANAGER, User.Role.ADMIN)
MANAGER_ROLES = (User.Role.MANAGER, User.Role.ADMIN)


def _has_role(user, roles):
    return user.is_superuser or user.role in roles


def _branch(company_pk, branch_pk):
    return get_object_or_404(
        CompanyBranch.objects.select_related('company'),
        pk=branch_pk, company_id=company_pk,
    )


def _company_releases(company):
    if hasattr(company, 'prefetched_display_releases'):
        return list(company.prefetched_display_releases)
    return list(PackRelease.objects.filter(company=company, purpose='display')
                .select_related('branch', 'product')
                .prefetch_related('settlements', 'payments', 'returns'))


def _display_prefetch():
    return Prefetch('pack_releases', queryset=(
        PackRelease.objects.filter(purpose='display')
        .select_related('branch', 'product')
        .prefetch_related('settlements', 'payments', 'returns')
    ), to_attr='prefetched_display_releases')


class ConsigneeCreateView(RoleRequiredMixin, View):
    """
    Enrol an existing customer or create a new customer
    directly inside the Consignment workspace.

    A physical branch is created at the same time so the
    account immediately becomes usable for deliveries.
    """

    allowed_roles = FIELD_ROLES
    template_name = "consignments/consignee_form.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {
                "form": ConsigneeCreateForm(),
            },
        )

    @transaction.atomic
    def post(self, request):
        form = ConsigneeCreateForm(request.POST)

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                },
            )

        company = form.cleaned_data.get("existing_company")

        # ----------------------------------------------------
        # CREATE NEW COMPANY ONLY WHEN AN EXISTING ONE
        # WAS NOT SELECTED.
        # ----------------------------------------------------
        if company is None:
            company = Company.objects.create(
                name=form.cleaned_data["name"],
                country=form.cleaned_data["country"],
                city=form.cleaned_data["city"],
                contact_person=form.cleaned_data["contact_person"],
                email=form.cleaned_data["email"],
                phone_number=form.cleaned_data["phone_number"],
                address=form.cleaned_data["address"],
                is_acquired_client=True,
            )

        # ----------------------------------------------------
        # PREVENT ACCIDENTAL DUPLICATE BRANCH
        # ----------------------------------------------------
        branch_name = form.cleaned_data["branch_name"].strip()

        if CompanyBranch.objects.filter(
            company=company,
            branch_name__iexact=branch_name,
        ).exists():
            form.add_error(
                "branch_name",
                "This consignee already has a branch with this name.",
            )

            transaction.set_rollback(True)

            return render(
                request,
                self.template_name,
                {
                    "form": form,
                },
            )

        # ----------------------------------------------------
        # CREATE FIRST PHYSICAL CONSIGNMENT LOCATION
        # ----------------------------------------------------
        branch = CompanyBranch.objects.create(
            company=company,
            branch_name=branch_name,
            address=form.cleaned_data.get(
                "branch_address",
                "",
            ),
            contact_person=form.cleaned_data.get(
                "branch_contact_person",
                "",
            ),
            phone_number=form.cleaned_data.get(
                "branch_phone",
                "",
            ),
            is_active=True,
        )

        messages.success(
            request,
            f"{company.name} has been added to Consignments. "
            f"{branch.branch_name} is ready for deliveries.",
        )

        return redirect(
            "consignment_branch_detail",
            pk=company.pk,
            branch_pk=branch.pk,
        )

    
class ConsignmentListView(RoleRequiredMixin, ListView):
    allowed_roles = READ_ROLES
    model = Company
    template_name = "consignments/consignment_list.html"
    context_object_name = "companies"

    def get_queryset(self):
        qs = (
            Company.objects
            .filter(
                Q(branches__isnull=False)
                | Q(pack_releases__purpose="display")
            )
            .distinct()
            .prefetch_related(
                "branches__consignment_inventories",
                "audits",
                _display_prefetch(),
            )
            .order_by("name")
        )

        term = self.request.GET.get("q", "").strip()

        if term:
            qs = qs.filter(name__icontains=term)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        rows = []

        for company in context["companies"]:
            branches = list(company.branches.all())
            releases = _company_releases(company)
            audits = list(company.audits.all())

            rows.append({
                "company": company,

                "branch_count": len(branches),

                "on_hand": sum(
                    inventory.total_consignment_stock
                    for branch in branches
                    for inventory in branch.consignment_inventories.all()
                ),

                "billed": sum(
                    (
                        release.gross_amount
                        for release in releases
                    ),
                    Decimal("0.00"),
                ),

                "paid": sum(
                    (
                        release.total_amount_paid
                        for release in releases
                    ),
                    Decimal("0.00"),
                ),

                "outstanding": sum(
                    (
                        release.outstanding_balance
                        for release in releases
                    ),
                    Decimal("0.00"),
                ),

                "pending": sum(
                    audit.status == "pending_approval"
                    for audit in audits
                ),
            })

        context.update(
            rows=rows,

            query=self.request.GET.get(
                "q",
                "",
            ).strip(),

            total_branches=sum(
                row["branch_count"]
                for row in rows
            ),

            total_on_hand=sum(
                row["on_hand"]
                for row in rows
            ),

            total_outstanding=sum(
                (
                    row["outstanding"]
                    for row in rows
                ),
                Decimal("0.00"),
            ),

            can_edit=_has_role(
                self.request.user,
                FIELD_ROLES,
            ),
        )

        return context

class ConsignmentAuditListView(RoleRequiredMixin, View):
    allowed_roles = READ_ROLES
    template_name = "consignments/consignment_audit_list.html"

    def get(self, request):

        interval_days = getattr(
            settings,
            "CONSIGNMENT_AUDIT_INTERVAL_DAYS",
            14,
        )

        search = request.GET.get("q", "").strip()
        status_filter = request.GET.get("status", "").strip()

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

            # ---------------------------------------------
            # NO STOCK AT BRANCH
            # ---------------------------------------------
            if stock_on_hand <= 0:

                audit_status = "no_stock"

                last_audited = (
                    latest_audit.approved_at
                    if latest_audit
                    else None
                )

                due_date = None
                days_value = None

            # ---------------------------------------------
            # STOCK EXISTS BUT NEVER AUDITED
            # ---------------------------------------------
            elif latest_audit is None:

                audit_status = "due"
                last_audited = None
                due_date = today
                days_value = 0

            # ---------------------------------------------
            # PREVIOUSLY AUDITED
            # ---------------------------------------------
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

            # Pending approval takes priority.
            if pending_audit:
                audit_status = "pending"

            rows.append({
                "branch": branch,
                "company": branch.company,

                "stock_on_hand": stock_on_hand,

                "last_audited": last_audited,
                "due_date": due_date,

                "days_value": days_value,

                "days_overdue": (
                    abs(days_value)
                    if (
                        days_value is not None
                        and days_value < 0
                    )
                    else 0
                ),

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
                "rows": page_obj.object_list,
                "page_obj": page_obj,

                "search": search,
                "status_filter": status_filter,

                "interval_days": interval_days,

                "overdue_count": overdue_count,
                "due_count": due_count,
                "pending_count": pending_count,
            },
        )


class ConsignmentDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = READ_ROLES
    model = Company
    context_object_name = 'company'
    template_name = 'consignments/consignment_detail.html'

    def get_queryset(self):
        return Company.objects.select_related('account_holder').prefetch_related(
            'branches__consignment_inventories', _display_prefetch(), 'audits'
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        company = self.object
        releases = _company_releases(company)
        branch_rows = []
        for branch in company.branches.prefetch_related('consignment_inventories').order_by('branch_name'):
            b_releases = [r for r in releases if r.branch_id == branch.pk]
            branch_rows.append({
                'branch': branch,
                'sku_count': branch.consignment_inventories.count(),
                'on_hand': sum(i.total_consignment_stock for i in branch.consignment_inventories.all()),
                'billed': sum((r.gross_amount for r in b_releases), Decimal('0.00')),
                'outstanding': sum((r.outstanding_balance for r in b_releases), Decimal('0.00')),
                'pending': StockAudit.objects.filter(branch=branch, status='pending_approval').count(),
            })
        ctx.update(
            branch_rows=branch_rows,
            display_value=sum((r.gross_amount for r in releases), Decimal('0.00')),
            display_paid=sum((r.total_amount_paid for r in releases), Decimal('0.00')),
            display_outstanding=sum((r.outstanding_balance for r in releases), Decimal('0.00')),
            display_on_hand=sum(r['on_hand'] for r in branch_rows),
            audits=StockAudit.objects.filter(company=company).select_related('branch', 'audited_by').order_by('-audit_date')[:12],
            legacy_release_count=sum(r.branch_id is None for r in releases),
            can_edit=_has_role(self.request.user, FIELD_ROLES),
        )
        return ctx


class ConsignmentBranchCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = FIELD_ROLES
    form_class = BranchForm
    template_name = 'consignments/consignment_branch_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.company = get_object_or_404(Company, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.company = self.company
        messages.success(self.request, 'Branch created.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('consignment_branch_detail', kwargs={'pk': self.company.pk, 'branch_pk': self.object.pk})

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), 'company': self.company}


class ConsignmentBranchDetailView(RoleRequiredMixin, View):
    allowed_roles = READ_ROLES
    template_name = 'consignments/consignment_branch_detail.html'

    def get(self, request, pk, branch_pk):
        branch = _branch(pk, branch_pk)
        inventories = list(ConsignmentInventory.objects.filter(branch=branch)
                           .select_related('product__blend', 'product__pack_size')
                           .order_by('product__blend__name', 'product__pack_size__grams'))
        releases = list(PackRelease.objects.filter(branch=branch, purpose='display')
                        .select_related('product').prefetch_related('settlements', 'payments', 'returns')
                        .order_by('-released_at'))
        return render(request, self.template_name, {
            'company': branch.company, 'branch': branch, 'inventories': inventories,
            'releases': releases[:30],
            'recent_ledger': BranchStockLedger.objects.filter(branch=branch)
                            .select_related('product').order_by('-created_at')[:20],
            'audits': StockAudit.objects.filter(branch=branch)
                      .select_related('audited_by').order_by('-audit_date')[:15],
            'pending_audit': StockAudit.objects.filter(branch=branch, status='pending_approval').first(),
            'on_hand': sum(i.total_consignment_stock for i in inventories),
            'billed': sum((r.gross_amount for r in releases), Decimal('0.00')),
            'outstanding': sum((r.outstanding_balance for r in releases), Decimal('0.00')),
            'can_edit': _has_role(request.user, FIELD_ROLES),
            'can_manage': _has_role(request.user, MANAGER_ROLES),
        })


class ConsignmentDeliveryView(RoleRequiredMixin, View):
    allowed_roles = FIELD_ROLES
    template_name = "consignments/consignment_delivery.html"

    def get(self, request, pk, branch_pk):
        branch = _branch(pk, branch_pk)

        return render(
            request,
            self.template_name,
            {
                "company": branch.company,
                "branch": branch,
                "item_formset": StockRequestItemFormSet(
                    prefix="items"
                ),
            },
        )

    def post(self, request, pk, branch_pk):
        branch = _branch(pk, branch_pk)

        item_formset = StockRequestItemFormSet(
            request.POST,
            prefix="items",
        )

        if item_formset.is_valid():
            try:
                items = [
                    (
                        form.cleaned_data["product"],
                        form.cleaned_data["quantity_requested"],
                    )
                    for form in item_formset.forms
                    if form.cleaned_data
                    and not form.cleaned_data.get("DELETE")
                    and form.cleaned_data.get("product")
                ]

                stock_request = create_stock_request(
                    user=request.user,
                    purpose="display",
                    destination_type="company",
                    company=branch.company,
                    branch=branch,
                    account_holder=branch.company.account_holder,
                    items=items,
                    notes=request.POST.get(
                        "notes",
                        "",
                    ).strip(),
                )

                messages.success(
                    request,
                    f"Delivery request "
                    f"{stock_request.short_number} submitted "
                    f"for {branch.branch_name}.",
                )

                return redirect(
                    "stock_request_detail",
                    pk=stock_request.pk,
                )

            except ValidationError as exc:
                messages.error(
                    request,
                    exc.messages[0]
                    if hasattr(exc, "messages")
                    else str(exc),
                )

        return render(
            request,
            self.template_name,
            {
                "company": branch.company,
                "branch": branch,
                "item_formset": item_formset,
            },
        )


class ConsignmentShelfTransferView(RoleRequiredMixin, View):
    allowed_roles = FIELD_ROLES

    def post(self, request, pk, branch_pk):
        branch = _branch(pk, branch_pk)
        form = ShelfTransferForm(request.POST)
        if form.is_valid():
            product = get_object_or_404(PackagedProduct, pk=form.cleaned_data['product_id'])
            try:
                transfer_to_shelf(branch=branch, product=product,
                                  quantity=form.cleaned_data['quantity'], user=request.user)
                messages.success(request, 'Shelf transfer recorded.')
            except (ValidationError, ConsignmentInventory.DoesNotExist) as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, 'Enter a valid transfer quantity.')
        return redirect('consignment_branch_detail', pk=pk, branch_pk=branch_pk)


class ConsignmentBranchReturnView(RoleRequiredMixin, View):
    allowed_roles = MANAGER_ROLES

    def post(self, request, pk, branch_pk):
        branch = _branch(pk, branch_pk)
        form = BranchReturnForm(request.POST)
        if form.is_valid():
            release = get_object_or_404(PackRelease, pk=form.cleaned_data['release_id'], branch=branch)
            try:
                record_good_branch_return(
                    branch=branch, release=release, location=form.cleaned_data['location'],
                    quantity=form.cleaned_data['quantity'], manager=request.user,
                )
                messages.success(request, 'Approved return posted to the branch ledger.')
            except ValidationError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, 'Enter a valid release, location and quantity.')
        return redirect('consignment_branch_detail', pk=pk, branch_pk=branch_pk)


class ConsignmentAuditView(RoleRequiredMixin, View):
    allowed_roles = FIELD_ROLES
    template_name = 'consignments/consignment_audit.html'

    def _page(self, request, branch, error=None):
        inventories = list(ConsignmentInventory.objects.filter(branch=branch)
                           .select_related('product__blend', 'product__pack_size')
                           .order_by('product__blend__name', 'product__pack_size__grams'))
        for inv in inventories:
            release = next((r for r in PackRelease.objects.filter(
                branch=branch, product=inv.product, purpose='display'
            ).prefetch_related('returns').order_by('released_at', 'pk')
                if r.packs_out > r.billable_packs + sum(
                    ret.packs_returned for ret in r.returns.all()
                    if ret.status in ('approved', 'pending_approval')
                )), None)
            inv.audit_price = release.selling_price if release else inv.product.selling_price
        return render(request, self.template_name, {
            'company': branch.company, 'branch': branch, 'inventories': inventories,
            'pending_audit': StockAudit.objects.filter(branch=branch, status='pending_approval').first(),
            'error': error,
        })

    def get(self, request, pk, branch_pk):
        return self._page(request, _branch(pk, branch_pk))

    def post(self, request, pk, branch_pk):
        branch = _branch(pk, branch_pk)
        product_ids = request.POST.getlist('product_id')
        rows = [{
            'product_id': product_id,
            'actual_shelf': request.POST.get(f'actual_shelf_{product_id}'),
            'actual_backroom': request.POST.get(f'actual_backroom_{product_id}'),
            'shrinkage_shelf': request.POST.get(f'shrinkage_shelf_{product_id}'),
            'shrinkage_backroom': request.POST.get(f'shrinkage_backroom_{product_id}'),
            'selling_price': request.POST.get(f'selling_price_{product_id}'),
        } for product_id in product_ids]
        try:
            audit = submit_branch_audit(branch=branch, user=request.user, rows=rows,
                                        notes=request.POST.get('notes', '').strip())
            messages.success(request, f'Audit #{audit.pk} submitted for manager approval. No stock or debt has changed.')
            return redirect('consignment_audit_detail', pk=pk, branch_pk=branch_pk, audit_pk=audit.pk)
        except (ValidationError, ValueError, TypeError) as exc:
            return self._page(request, branch, str(exc))


class ConsignmentAuditDetailView(RoleRequiredMixin, View):
    allowed_roles = READ_ROLES
    template_name = 'consignments/consignment_audit_detail.html'

    def get(self, request, pk, branch_pk, audit_pk):
        branch = _branch(pk, branch_pk)
        audit = get_object_or_404(StockAudit.objects.select_related('audited_by', 'approved_by'),
                                  pk=audit_pk, branch=branch)
        invoice = ConsignmentInvoice.objects.filter(audit=audit).first()
        return render(request, self.template_name, {
            'company': branch.company, 'branch': branch, 'audit': audit,
            'items': audit.items.select_related('product__blend', 'product__pack_size')
                    .prefetch_related('consignment_allocations__release').all(),
            'invoice': invoice,
            'audit_total': sum((item.calculated_amount_due for item in audit.items.all()), Decimal('0.00')),
            'can_manage': _has_role(request.user, MANAGER_ROLES),
        })


class ConsignmentAuditDecisionView(RoleRequiredMixin, View):
    allowed_roles = MANAGER_ROLES

    def post(self, request, pk, branch_pk, audit_pk):
        _branch(pk, branch_pk)
        audit = get_object_or_404(StockAudit, pk=audit_pk, branch_id=branch_pk)
        form = AuditDecisionForm(request.POST)
        if form.is_valid():
            try:
                if form.cleaned_data['action'] == 'approve':
                    invoice = approve_branch_audit(audit_id=audit.pk, manager=request.user)
                    messages.success(request, f'Audit approved. Invoice {invoice.number} created for UGX {invoice.total_amount:,.0f}.')
                else:
                    reject_branch_audit(audit_id=audit.pk, manager=request.user,
                                        reason=form.cleaned_data['reason'])
                    messages.success(request, 'Audit rejected. Stock remains unchanged.')
            except ValidationError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, 'Invalid audit decision.')
        return redirect('consignment_audit_detail', pk=pk, branch_pk=branch_pk, audit_pk=audit_pk)


class ConsignmentInvoiceView(RoleRequiredMixin, DetailView):
    allowed_roles = READ_ROLES
    model = ConsignmentInvoice
    template_name = 'consignments/consignment_invoice.html'
    context_object_name = 'invoice'
    pk_url_kwarg = 'invoice_pk'

    def get_queryset(self):
        return (ConsignmentInvoice.objects.filter(
            audit__branch_id=self.kwargs['branch_pk'],
            audit__company_id=self.kwargs['pk'],
            audit__status='approved',
        ).select_related('audit__branch', 'audit__company', 'audit__approved_by')
         .prefetch_related('audit__items__product'))

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), 'company': self.object.audit.company,
                'branch': self.object.audit.branch,
                'items': self.object.audit.items.select_related('product').all()}


class ConsignmentLegacyAuditRedirectView(RoleRequiredMixin, View):
    """Keep old company-level audit links safe while making branch selection explicit."""

    allowed_roles = FIELD_ROLES

    def get(self, request, pk):
        get_object_or_404(Company, pk=pk)

        messages.info(
            request,
            "Choose the supermarket branch before starting an audit."
        )

        return redirect(
            "consignment_detail",
            pk=pk,
        )
