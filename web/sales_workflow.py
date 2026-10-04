"""Stock-request, ordinary-sale, return, and payment workflows.

Branch-level consignments live in ``web.services.consignments``.  In particular,
no function here may deliver, audit, or return branch-controlled display stock.

Money: PackSettlement and PaymentReceipt are BOTH legitimate payment records, but
ONE real transaction must be entered into only ONE of them.  Settlement.packs_sold
is an *incremental* sale-event quantity, always zero on payment-only entries.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    AccountHolder,
    PackagedInventory,
    PackagedProduct,
    PackRelease,
    PackReturn,
    PackSettlement,
    PaymentReceipt,
    StockRequest,
    StockRequestItem,
)

ZERO = Decimal("0.00")


def _money(value, label="Amount"):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"{label} must be a valid number.")
    if not result.is_finite() or result.as_tuple().exponent < -2:
        raise ValidationError(f"{label} must be a finite amount with at most two decimal places.")
    return result


def _positive_int(value, label="Quantity"):
    if isinstance(value, bool) or str(value).strip().lstrip("+").isdigit() is False:
        raise ValidationError(f"{label} must be a positive whole number.")
    n = int(value)
    if n <= 0:
        raise ValidationError(f"{label} must be greater than zero.")
    return n


def _valid_method(method, choices):
    if method not in dict(choices):
        raise ValidationError("Select a valid payment method.")
    return method


def _forbid_generic_display(release):
    if release.purpose == "display":
        if release.branch_id is not None:
            raise ValidationError(
                "Branch consignment stock must use the verified branch return workflow."
            )
        raise ValidationError(
            "This is a legacy display release without a branch. Reconcile it to a "
            "branch before processing returns."
        )


def _available(product):
    try:
        return product.inventory.available
    except PackagedInventory.DoesNotExist:
        return 0


def _refresh_request_status(stock_request):
    """Only called after releases are saved; it must not touch warehouse stock."""
    if stock_request.is_fully_issued:
        stock_request.status = "fulfilled"
        stock_request.fulfilled_at = timezone.now()
    else:
        stock_request.status = "partially_fulfilled"
        stock_request.fulfilled_at = None
    stock_request.save(update_fields=["status", "fulfilled_at"])


@transaction.atomic
def create_stock_request(
    *,
    user,
    purpose,
    destination_type="agent_float",
    items,
    account_holder=None,
    company=None,
    branch=None,
    notes="",
):
    """Create a request. Display requests cannot be fulfilled by ordinary dispatch."""
    if not items:
        raise ValidationError("Add at least one product to the request.")
    validated = []
    seen = set()
    for product, quantity in items:
        quantity = _positive_int(quantity, "Requested quantity")
        if product.pk in seen:
            raise ValidationError("The same product cannot be added twice to one request.")
        seen.add(product.pk)
        validated.append((product, quantity))

    request = StockRequest.objects.create(
        requested_by=user, company=company, purpose=purpose, notes=notes,
        account_holder=account_holder, destination_type=destination_type, branch=None
    )
    for product, quantity in validated:
        StockRequestItem.objects.create(
            request=request, product=product, quantity_requested=quantity,
        )
    return request


@transaction.atomic
def execute_stock_request_fulfillment(request_item, packs_to_issue, user, notes=""):
    """Compatibility entry point. Delegate to the one authoritative issue service."""
    item = StockRequestItem.objects.select_related("request", "product").get(pk=request_item.pk)
    if item.request.purpose == "display":
        raise ValidationError(
            "Use the branch-specific Consignment Delivery page for supermarket display stock."
        )
    return fulfill_request_item(
        item=item,
        quantity=packs_to_issue,
        selling_price=item.product.selling_price,
        manager=user,
        notes=notes,
    )


@transaction.atomic
def fulfill_request_item(
    *,
    item,
    quantity,
    selling_price,
    manager,
    notes="",
):
    """
    Issue packaged stock against one request item.

    Ordinary requests:
        create a normal PackRelease.

    Consignment/display requests:
        create a branch-linked PackRelease and place the
        issued packs into the branch backroom ledger.
    """

    item = (
        StockRequestItem.objects
        .select_for_update()
        .select_related(
            "request",
            "request__company",
            "request__branch",
            "product",
        )
        .get(pk=item.pk)
    )

    request = item.request

    quantity = int(quantity)
    selling_price = Decimal(str(selling_price))

    if request.status == "cancelled":
        raise ValidationError(
            "This stock request is cancelled."
        )

    if quantity <= 0:
        raise ValidationError(
            "Issue quantity must be greater than zero."
        )

    if selling_price < 0:
        raise ValidationError(
            "Price per pack cannot be negative."
        )

    if quantity > item.outstanding_quantity:
        raise ValidationError(
            f"Only {item.outstanding_quantity} packs "
            f"remain to fulfil this item."
        )

    product = (
        PackagedProduct.objects
        .select_for_update()
        .get(pk=item.product_id)
    )

    try:
        inventory = (
            PackagedInventory.objects
            .select_for_update()
            .get(product=product)
        )
    except PackagedInventory.DoesNotExist:
        raise ValidationError(
            f"No packaged stock exists for {product}."
        )

    available = inventory.available

    if quantity > available:
        raise ValidationError(
            f"Only {available} packs of {product} "
            f"are currently available."
        )

    # ---------------------------------------------------------
    # ACCOUNT HOLDER
    # ---------------------------------------------------------

    target_account = request.account_holder

    if (
        not target_account
        and request.company
        and request.company.account_holder
    ):
        target_account = request.company.account_holder

    if not target_account:
        request_user = request.requested_by

        target_account, _ = AccountHolder.objects.get_or_create(
            system_user=request_user,
            defaults={
                "name": (
                    request_user.get_full_name()
                    or request_user.username
                ),
                "account_type": "salesperson",
                "is_active": True,
            },
        )


    # ---------------------------------------------------------
    # CONSIGNMENT VALIDATION
    # ---------------------------------------------------------

    release_branch = None

    if request.purpose == "display":

        if not request.branch_id:
            raise ValidationError(
                "This consignment request has no destination branch."
            )

        if request.branch.company_id != request.company_id:
            raise ValidationError(
                "The request branch does not belong to the request company."
            )

        if StockAudit.objects.filter(
            branch=request.branch,
            status="pending_approval",
        ).exists():
            raise ValidationError(
                "This branch has a pending audit. "
                "Approve or reject it before issuing more stock."
            )

        release_branch = request.branch


    # ---------------------------------------------------------
    # PHYSICAL RELEASE
    # ---------------------------------------------------------

    release = PackRelease.objects.create(
        product=product,
        request_item=item,
        released_to=target_account,
        company=request.company,
        branch=release_branch,
        purpose=request.purpose,
        packs_out=quantity,
        selling_price=selling_price,
        billable_packs=0,
        created_by=manager,
        notes=notes,
    )


    # ---------------------------------------------------------
    # CONSIGNMENT BRANCH STOCK
    # All new deliveries arrive into BACKROOM first.
    # ---------------------------------------------------------

    if request.purpose == "display":

        branch_inventory, _ = (
            ConsignmentInventory.objects
            .select_for_update()
            .get_or_create(
                branch=request.branch,
                product=product,
                defaults={
                    "current_shelf_quantity": 0,
                    "current_backroom_quantity": 0,
                },
            )
        )

        branch_inventory.current_backroom_quantity += quantity

        branch_inventory.save(
            update_fields=[
                "current_backroom_quantity",
            ]
        )

        BranchStockLedger.objects.create(
            branch=request.branch,
            product=product,
            stock_location="backroom",
            transaction_type="delivery",
            quantity=quantity,
            release=release,
            created_by=manager,
        )


    # ---------------------------------------------------------
    # REQUEST STATUS
    # ---------------------------------------------------------

    request.refresh_from_db()

    request_items = (
        request.items
        .prefetch_related("releases")
        .all()
    )

    if all(
        request_item.outstanding_quantity == 0
        for request_item in request_items
    ):
        request.status = "fulfilled"
        request.fulfilled_at = timezone.now()

    else:
        request.status = "partially_fulfilled"
        request.fulfilled_at = None

    request.save(
        update_fields=[
            "status",
            "fulfilled_at",
        ]
    )

    return release

@transaction.atomic
def return_packs(*, release, packs_returned, condition, disposition,
                 reason="", notes="", user=None):
    """Submit a PENDING ordinary return; stock is restored only after approval."""
    release = (PackRelease.objects.select_for_update()
               .prefetch_related("returns", "settlements")
               .get(pk=release.pk))
    _forbid_generic_display(release)

    quantity = _positive_int(packs_returned, "Return quantity")
    sold = sum(s.packs_sold for s in release.settlements.all())
    available = max(
        release.packs_out - sold - release.packs_returned - release.packs_pending_return,
        0,
    )
    if quantity > available:
        raise ValidationError(f"Only {available} unsold packs remain eligible for return.")

    return PackReturn.objects.create(
        release=release,
        packs_returned=quantity,
        condition=condition,
        disposition=disposition,
        reason=reason,
        notes=notes,
        received_by=user,
        submitted_by=user,
        status="pending_approval",
    )


@transaction.atomic
def settle_release(
    *,
    release,
    packs_sold,
    amount_paid,
    payment_method,
    payment_reference="",
    cashier=None,
    notes="",
):
    """
    Confirm a NEW sale event from an ordinary sale release.

    PackSettlement records the actual sale quantity and any money
    received at the moment of that sale.

    Later installment payments must use PaymentReceipt instead.
    """

    # FIX 1: Kept prefetch_related so that Python can evaluate related sets instantly
    release = (
        PackRelease.objects
        .select_for_update()
        .select_related(
            "product",
            "request_item__request",
        )
        .prefetch_related("returns", "settlements")
        .get(pk=release.pk)
    )

    if (
        not release.request_item_id
        or release.request_item.request.purpose != "sale"
    ):
        raise ValidationError(
            "Only ordinary sale stock can be cleared through "
            "the cashier sale workflow."
        )

    if release.purpose == "display":
        raise ValidationError(
            "Consignment sales are confirmed through approved stock audits."
        )

    if cashier is None:
        raise ValidationError("A cashier is required to record a sale.")

    # FIX 3: Re-add your choice validation if _valid_method is defined in your file
    payment_method = _valid_method(payment_method, PackSettlement.PAYMENT_CHOICES)

    packs_sold = int(packs_sold)
    amount_paid = Decimal(str(amount_paid))

    if packs_sold <= 0:
        raise ValidationError(
            "Sold quantity must be greater than zero."
        )

    if release.selling_price <= Decimal("0.00"):
        raise ValidationError(
            "This release does not have a valid selling price."
        )

    # ---------------------------------------------------------
    # QUANTITIES ALREADY ACCOUNTED FOR
    # ---------------------------------------------------------

    already_sold = sum(
        (
            settlement.packs_sold
            for settlement in release.settlements.all()
        ),
        0,
    )

    # FIX 2: Filter using Python instead of .filter() to utilize the prefetched data
    all_returns = list(release.returns.all())
    
    approved_returns = sum(
        (item.packs_returned for item in all_returns if item.status == "approved"),
        0,
    )

    pending_returns = sum(
        (item.packs_returned for item in all_returns if item.status == "pending_approval"),
        0,
    )

    available_to_sell = max(
        release.packs_out
        - already_sold
        - approved_returns
        - pending_returns,
        0,
    )

    if packs_sold > available_to_sell:
        raise ValidationError(
            f"Only {available_to_sell} additional pack(s) "
            "remain available to sell."
        )

    amount_due = (
        Decimal(packs_sold)
        * release.selling_price
    )

    if amount_paid < Decimal("0.00"):
        raise ValidationError(
            "Amount paid cannot be negative."
        )

    if amount_paid > amount_due:
        raise ValidationError(
            f"Amount paid cannot exceed UGX {amount_due:,.2f} "
            "for this sale."
        )

    # ---------------------------------------------------------
    # CREATE A NEW SALE EVENT
    # NEVER overwrite an earlier transaction.
    # ---------------------------------------------------------

    balance_before_payment = release.outstanding_balance

    if amount_paid > balance_before_payment:
        raise ValidationError(
            f"Payment exceeds the release's outstanding balance "
            f"of UGX {balance_before_payment:,.2f}."
        )
    settlement = PackSettlement.objects.create(
    release=release,
    packs_sold=packs_sold,
    amount_paid=amount_paid,
    payment_method=payment_method,
    payment_reference=payment_reference,
    status=(
        "cleared"
        if amount_paid == balance_before_payment
        else "partial"
    ),
    cleared_by=cashier,
    notes=notes,
)

    return settlement


@transaction.atomic
def record_payment(*, release, amount, method, payment_reference="",
                   collected_by=None, notes=""):
    """Payment-only PackSettlement for an existing release: packs_sold is ZERO."""
    release = PackRelease.objects.select_for_update().get(pk=release.pk)
    if collected_by is None:
        raise ValidationError("A cashier is required to record this payment.")
    amount = _money(amount)
    if amount <= ZERO:
        raise ValidationError("Payment amount must be greater than zero.")
    method = _valid_method(method, PackSettlement.PAYMENT_CHOICES)
    owed = release.outstanding_balance  # Includes BOTH payment models.
    if owed <= ZERO:
        raise ValidationError("This release has already been fully cleared.")
    if amount > owed:
        raise ValidationError(f"Payment exceeds the outstanding balance of UGX {owed:,.2f}.")
    return PackSettlement.objects.create(
        release=release, packs_sold=0, amount_paid=amount,
        payment_method=method, payment_reference=payment_reference,
        status="cleared" if amount == owed else "partial",
        cleared_by=collected_by, notes=notes,
    )




@transaction.atomic
def record_installment_payment(*, release, amount, method, payment_reference="",
                               collected_by=None, notes=""):
    """Payment-only PaymentReceipt for the dedicated installment form.

    Do NOT create a matching PackSettlement for this same payment.
    """
    release = PackRelease.objects.select_for_update().get(pk=release.pk)
    amount = _money(amount)
    if amount <= ZERO:
        raise ValidationError("Payment amount must be greater than zero.")
    method = _valid_method(method, PaymentReceipt.PAYMENT_METHOD_CHOICES)
    owed = release.outstanding_balance
    if owed <= ZERO:
        raise ValidationError("This release is fully cleared or not yet billable.")
    if amount > owed:
        raise ValidationError(f"Payment exceeds the outstanding balance of UGX {owed:,.2f}.")
    return PaymentReceipt.objects.create(
        release=release, amount=amount, method=method,
        payment_reference=payment_reference, collected_by=collected_by, notes=notes,
    )



def record_payment(
    *,
    release,
    amount,
    method,
    payment_reference="",
    collected_by=None,
    notes="",
):
    """
    Backward-compatible name for legacy views.

    All payment-only transactions now go through PaymentReceipt.
    """

    return record_installment_payment(
        release=release,
        amount=amount,
        method=method,
        payment_reference=payment_reference,
        collected_by=collected_by,
        notes=notes,
    )


@transaction.atomic
def approve_existing_pack_return(*, return_item, user):
    """Approve an existing ordinary return; never create another return slip."""
    pending = PackReturn.objects.select_for_update().get(pk=return_item.pk)
    if pending.status != "pending_approval":
        raise ValidationError("This return has already been processed.")
    release = (PackRelease.objects.select_for_update()
               .prefetch_related("returns", "settlements")
               .get(pk=pending.release_id))
    _forbid_generic_display(release)

    approved = sum(r.packs_returned for r in release.returns.all() if r.status == "approved")
    other_pending = sum(
        r.packs_returned for r in release.returns.all()
        if r.status == "pending_approval" and r.pk != pending.pk
    )
    sold = sum(s.packs_sold for s in release.settlements.all())
    available = release.packs_out - sold - approved - other_pending
    if available < 0:
        raise ValidationError(
            "This release has inconsistent historical sold/return data; reconcile it first."
        )
    if pending.packs_returned > available:
        raise ValidationError(f"Only {available} packs remain eligible for approval.")

    pending.status = "approved"
    pending.approved_by = user
    pending.approved_at = timezone.now()
    pending.received_by = user
    pending.disposition = "accepted"
    pending.save(update_fields=[
        "status", "approved_by", "approved_at", "received_by", "disposition",
    ])
    return pending


def process_branch_audit(*, branch, user, audit_counts_data):
    """Backward-compatible, NON-BILLING adapter for the new branch audit service.

    Prior callers expect (audit, amount). Amount is ALWAYS zero until approval.
    """
    from .services.consignments import submit_branch_audit

    rows = []
    for data in audit_counts_data:
        product = data["product"]
        rows.append({
            "product_id": product.pk,
            "actual_shelf": data["shelf_count"],
            "actual_backroom": data["backroom_count"],
            "shrinkage_shelf": data.get("shrinkage_shelf", 0),
            "shrinkage_backroom": data.get("shrinkage_backroom", 0),
            "selling_price": data["selling_price"],
        })
    return submit_branch_audit(branch=branch, user=user, rows=rows), ZERO


def process_supermarket_audit(*, company, user, audit_items_data, notes=""):
    """DISABLED: company-level audits cannot safely bill multi-branch inventory."""
    raise ValidationError(
        "Company-level audit is retired. Open the supermarket's branch and submit "
        "a branch audit. Only manager approval may create an invoice."
    )
