 
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    AccountHolder,
    BranchStockLedger,
    ConsignmentInventory,
    PackRelease,
    PackReturn,
    PackSettlement,
    PackagedProduct,
    PackagedInventory,
    PaymentReceipt,
    StockAudit,
    StockAuditItem,
    StockRequest,
    StockRequestItem,
)


def _available(product):
    try:
        return product.inventory.available
    except PackagedInventory.DoesNotExist:
        return 0


@transaction.atomic
def create_stock_request(*, user, purpose, destination_type="agent_float", items, account_holder=None, company=None, notes=""):
    """Create one internal stock request containing one or more products."""
    if not items:
        raise ValidationError("Add at least one product to the request.")

    request = StockRequest.objects.create(
        requested_by=user,
        company=company,
        purpose=purpose,
        notes=notes,
        account_holder=account_holder,
        destination_type=destination_type,
    )

    seen = set()
    for product, quantity in items:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValidationError("Requested quantities must be greater than zero.")
        if product.pk in seen:
            raise ValidationError("The same product cannot be added twice to one request.")
        seen.add(product.pk)
        StockRequestItem.objects.create(
            request=request,
            product=product,
            quantity_requested=quantity,
        )

    return request


@transaction.atomic
def execute_stock_request_fulfillment(request_item, packs_to_issue, user, notes=""):
    """
    Fulfills stock directly from store to Field Agent or Company.
    Stock remains tracked under PackRelease as 'released' until returned or paid for.
    """
    stock_request = request_item.request

    if packs_to_issue > request_item.outstanding_quantity:
        raise ValidationError(
            f"Cannot issue {packs_to_issue} units. Outstanding quantity is {request_item.outstanding_quantity}."
        )

    # 1. Deduct main warehouse inventory
    product = request_item.product
    if product.current_stock < packs_to_issue:
        raise ValidationError(
            f"Insufficient store stock! Available: {product.current_stock}, Requested: {packs_to_issue}."
        )
    
    product.current_stock -= packs_to_issue
    product.save(update_fields=["current_stock"])

    # 2. Assign responsible Account Holder
    target_account = stock_request.account_holder
    if not target_account and stock_request.company and hasattr(stock_request.company, 'account_holder'):
        target_account = stock_request.company.account_holder

    # 3. Create PackRelease (Tracks physical custody until paid or returned)
    release = PackRelease.objects.create(
        product=product,
        request_item=request_item,
        released_to=target_account,
        company=stock_request.company,
        purpose=stock_request.purpose,
        packs_out=packs_to_issue,
        selling_price=getattr(product, 'default_selling_price', Decimal("0.00")),
        status="released",
        created_by=user,
        notes=notes,
    )

    # 4. Update Stock Request status
    if stock_request.is_fully_issued:
        stock_request.status = "fulfilled"
        stock_request.fulfilled_at = timezone.now()
    else:
        stock_request.status = "partially_fulfilled"
    
    stock_request.save(update_fields=["status", "fulfilled_at"])

    return release

@transaction.atomic
def fulfill_request_item(*, item, quantity, selling_price, manager, notes=""):
    """
    Issue packaged stock against one request item atomically,
    preserving target account holder and company relationships.
    """
    item = (
        StockRequestItem.objects.select_for_update()
        .select_related("request", "request__company", "product")
        .get(pk=item.pk)
    )

    request = item.request
    quantity = int(quantity)
    selling_price = Decimal(selling_price)

    if request.status == "cancelled":
        raise ValidationError("This stock request is cancelled.")
    if quantity <= 0:
        raise ValidationError("Issue quantity must be greater than zero.")
    if selling_price < 0:
        raise ValidationError("Price per pack cannot be negative.")
    if quantity > item.outstanding_quantity:
        raise ValidationError(
            f"Only {item.outstanding_quantity} packs remain to fulfil this item."
        )


    product = PackagedProduct.objects.select_for_update().get(pk=item.product_id)
    try:
        inventory = PackagedInventory.objects.select_for_update().get(product=product)
    except PackagedInventory.DoesNotExist:
        raise ValidationError(f"No packaged stock exists for {product}.")

    available = inventory.available
    if quantity > available:
        raise ValidationError(f"Only {available} packs of {product} are currently available.")

    # ---------------------------------------------------------
    # RESOLVE ACCOUNTHOLDER & COMPANY FOR PACKRELEASE CONTEXT
    # ---------------------------------------------------------
    target_account = request.account_holder
    if not target_account and request.company and request.company.account_holder:
        target_account = request.company.account_holder

    if not target_account:
        request_user = request.requested_by
        target_account, _ = AccountHolder.objects.get_or_create(
            system_user=request_user,
            defaults={
                "name": request_user.get_full_name() or request_user.username,
                "account_type": "salesperson",
                "is_active": True,
            },
        )

    release = PackRelease.objects.create(
        product=product,
        request_item=item,
        released_to=target_account,
        company=request.company,
        purpose=request.purpose,
        packs_out=quantity,
        selling_price=selling_price,
        billable_packs=0,
        created_by=manager,
        notes=notes,
    )

    # Supermarket / display stock becomes physical stock on the customer's
    # display, not an immediate sale.
    if request.purpose == "display" and request.company_id:
        consignment, _ = ConsignmentInventory.objects.select_for_update().get_or_create(
            company=request.company,
            product=product,
            defaults={"current_display_quantity": 0},
        )
        consignment.current_display_quantity += quantity
        consignment.save(update_fields=["current_display_quantity", "last_audited_at"])

    items = list(request.items.all())
    if all(i.outstanding_quantity == 0 for i in items):
        request.status = "fulfilled"
        request.fulfilled_at = timezone.now()
    else:
        request.status = "partially_fulfilled"
        request.fulfilled_at = None

    request.save(update_fields=["status", "fulfilled_at"])
    return release




@transaction.atomic
def return_packs(
    *,
    release,
    packs_returned,
    condition,
    disposition,
    reason="",
    notes="",
    user=None,
):
    release = (
        PackRelease.objects
        .select_for_update()
        .select_related("company", "product")
        .get(pk=release.pk)
    )

    packs_returned = int(packs_returned)
    if packs_returned <= 0:
        raise ValidationError("Must return at least one pack.")
    if packs_returned > release.packs_outstanding:
        raise ValidationError(
            f"Only {release.packs_outstanding} packs remain outstanding on this release."
        )

    returned = PackReturn.objects.create(
        release=release,
        packs_returned=packs_returned,
        condition=condition,
        disposition=disposition,
        reason=reason,
        notes=notes,
        received_by=user,
    )

    # Returned supermarket display stock leaves the supermarket display
    # whenever Nonda accepts the physical return. It does not create financial
    # credit because unsold display units were never billed.
    if (
        release.purpose == "display"
        and release.company_id
        and returned.counts_against_release
    ):
        consignment = (
            ConsignmentInventory.objects
            .select_for_update()
            .filter(company=release.company, product=release.product)
            .first()
        )
        if consignment:
            consignment.current_display_quantity = max(
                consignment.current_display_quantity - packs_returned,
                0,
            )
            consignment.save(update_fields=["current_display_quantity"])

    if release.packs_outstanding == 0:
        release.status = "fully_returned"
    elif returned.counts_against_release:
        release.status = "partially_returned"

    release.save(update_fields=["status", "updated_at"])
    return returned


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
    """Record money received for coffee sold from a physical release."""
    release = PackRelease.objects.select_for_update().select_related(
        "product", "request_item__request"
    ).get(pk=release.pk)

    if not release.request_item_id or release.request_item.request.purpose != "sale":
        raise ValidationError("Only stock released for sale can be cleared through the cashier queue.")

    packs_sold = int(packs_sold)
    amount_paid = Decimal(amount_paid)
    returned = release.packs_returned
    max_sold = release.packs_out - returned

    if packs_sold < 0:
        raise ValidationError("Sold quantity cannot be negative.")
    if packs_sold > max_sold:
        raise ValidationError(
            f"At most {max_sold} packs can be recorded as sold on this release."
        )
    if amount_paid < 0:
        raise ValidationError("Amount paid cannot be negative.")

    amount_due = Decimal(packs_sold) * release.selling_price
    if amount_paid > amount_due:
        raise ValidationError(
            f"Amount paid cannot exceed the amount due ({amount_due:,.2f})."
        )

    settlement, _ = PackSettlement.objects.select_for_update().get_or_create(
        release=release,
        defaults={
            "packs_sold": packs_sold,
            "amount_paid": amount_paid,
            "payment_method": payment_method,
            "payment_reference": payment_reference,
            "status": "cleared" if amount_paid == amount_due else "partial",
            "cleared_by": cashier,
            "notes": notes,
        },
    )

    if settlement.pk and settlement.release_id == release.pk:
        settlement.packs_sold = packs_sold
        settlement.amount_paid = amount_paid
        settlement.payment_method = payment_method
        settlement.payment_reference = payment_reference
        settlement.status = "cleared" if amount_paid == amount_due else "partial"
        settlement.cleared_by = cashier
        settlement.notes = notes
        settlement.save()

    return settlement




@transaction.atomic
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
    Records a payment settlement against a single, specific PackRelease.
    """
    # Lock the specific release record to prevent race conditions
    release = PackRelease.objects.select_for_update().get(pk=release.pk)

    amount = Decimal(str(amount))
    if amount <= Decimal("0.00"):
        raise ValidationError("Payment amount must be greater than zero.")

    owed = release.outstanding_balance
    if owed <= Decimal("0.00"):
        raise ValidationError("This release has already been fully cleared.")

    if amount > owed:
        raise ValidationError(
            f"Payment exceeds the outstanding balance of UGX {owed:,.2f}."
        )

    settlement = PackSettlement.objects.create(
        release=release,
        packs_sold=release.billable_quantity,
        amount_paid=amount,
        payment_method=method,
        payment_reference=payment_reference,
        status="cleared" if amount == owed else "partial",
        cleared_by=collected_by,
        notes=notes,
    )

    return settlement


@transaction.atomic
def process_branch_audit(*, branch, user, audit_counts_data):
    """
    audit_counts_data format:
    [
      {
        'product': product_obj,
        'shelf_count': 5,
        'backroom_count': 30,
        'selling_price': Decimal('15000.00')
      },
      ...
    ]
    """
    audit = StockAudit.objects.create(
        company=branch.company,
        branch=branch,
        audited_by=user,
    )

    total_amount_billed = 0

    for data in audit_counts_data:
        product = data['product']
        actual_shelf = data['shelf_count']
        actual_backroom = int(data["backroom_count"])

        selling_price = Decimal(data['selling_price'])

        # 1. Fetch exact current baseline from ledger
        expected_shelf = branch.get_stock_level(product, stock_location='shelf')
        expected_backroom = branch.get_stock_level(product, stock_location='backroom')

        # 2. Calculate sold quantities
        total_expected = expected_shelf + expected_backroom
        actual_total = actual_shelf + actual_backroom
        shelf_sold = max(expected_shelf - actual_shelf, 0)
        backroom_sold = max(expected_backroom - actual_backroom, 0)
        total_sold = shelf_sold + backroom_sold
        amount_due = Decimal(total_sold) * selling_price

        total_amount_billed += amount_due

        # 3. Create Audit Record
        StockAuditItem.objects.create(
            audit=audit,
            product=product,
            expected_quantity=total_expected,
            actual_physical_count=actual_total,
            quantity_sold=total_sold,
            selling_price=selling_price,
            calculated_amount_due=amount_due,

        )

        # 4. Write negative adjustments to ledger to bring system baseline 
        # EXACTLY in sync with physical count for next time
        if shelf_sold > 0:
            BranchStockLedger.objects.create(
                branch=branch,
                product=product,
                stock_location='shelf',
                transaction_type='audit_sale',
                quantity=-shelf_sold, # Deducts sold quantity from shelf
                audit=audit,
                created_by=user,
            )

        if backroom_sold > 0:
            BranchStockLedger.objects.create(
                branch=branch,
                product=product,
                stock_location='backroom',
                transaction_type='audit_sale',
                quantity=-backroom_sold, # Deducts sold quantity from backroom
                audit=audit,
                created_by=user,
            )

    return audit, total_amount_billed


@transaction.atomic
def process_supermarket_audit(*, company, user, audit_items_data, notes=""):
    audit = StockAudit.objects.create(
        company=company,
        audited_by=user,
        notes=notes,
    )

    total_invoice_amount = Decimal("0.00")

    for item_data in audit_items_data:
        product = item_data["product"]
        actual_count = int(item_data["actual_count"])
        selling_price = Decimal(item_data["selling_price"])

        if actual_count < 0:
            raise ValidationError("Physical count cannot be negative.")

        consignment, _ = ConsignmentInventory.objects.select_for_update().get_or_create(
            company=company,
            product=product,
            defaults={"current_display_quantity": 0},
        )

        expected_qty = consignment.current_display_quantity

        audit_item = StockAuditItem.objects.create(
            audit=audit,
            product=product,
            expected_quantity=expected_qty,
            actual_physical_count=actual_count,
            selling_price=selling_price,
            calculated_amount_due=(
                max(expected_qty - actual_count, 0) * selling_price
            ),
        )

        sold = audit_item.quantity_sold
        total_invoice_amount += audit_item.calculated_amount_due

        # Allocate newly confirmed sales against the oldest display releases
        # for this company/product. This makes consignment sales billable while
        # keeping the original physical release intact.
        remaining_sold = sold
        display_releases = list(
            PackRelease.objects
            .select_for_update()
            .filter(company=company, product=product, purpose="display")
            .order_by("released_at", "pk")
        )

        for release in display_releases:
            if remaining_sold <= 0:
                break
            already_physical_returned = release.packs_returned
            available_to_bill = max(
                release.packs_out - already_physical_returned - release.billable_packs,
                0,
            )
            if available_to_bill <= 0:
                continue

            allocation = min(available_to_bill, remaining_sold)
            release.billable_packs += allocation
            release.selling_price = selling_price
            release.save(update_fields=["billable_packs", "selling_price", "updated_at"])
            remaining_sold -= allocation

        consignment.current_display_quantity = actual_count
        consignment.last_audited_at = timezone.now()
        consignment.save(
            update_fields=["current_display_quantity", "last_audited_at"]
        )

    if company.pipeline_stage != "recurring_supply":
        company.pipeline_stage = "recurring_supply"
        company.save(update_fields=["pipeline_stage"])

    return audit, total_invoice_amount
