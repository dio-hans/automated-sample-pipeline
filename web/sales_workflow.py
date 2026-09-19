 
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
def create_stock_request(*, user, purpose, items, account_holder=None, company=None, notes=""):
    """Create one internal stock request containing one or more products."""
    if not items:
        raise ValidationError("Add at least one product to the request.")

    request = StockRequest.objects.create(
        requested_by=user,
        company=company,
        purpose=purpose,
        notes=notes,
        account_holder=account_holder,
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
def fulfill_request_item(*, item, quantity, selling_price, manager, notes=""):
    """Issue packaged stock against one request item atomically."""
    item = (StockRequestItem.objects.select_for_update()
            .select_related("request", "product").get(pk=item.pk))
    
    quantity = int(quantity)
    selling_price = Decimal(selling_price)
    
    if item.request.status in {"cancelled", "fulfilled"}:
        raise ValidationError("This stock request is no longer open for fulfilment.")
    if quantity <= 0:
        raise ValidationError("Issue quantity must be greater than zero.")
    if selling_price < 0:
        raise ValidationError("Price per pack cannot be negative.")
    if quantity > item.outstanding_quantity:
        raise ValidationError(f"Only {item.outstanding_quantity} packs remain to fulfil this item.")
        
    product = PackagedProduct.objects.select_for_update().get(pk=item.product_id)
    try:
        inventory = PackagedInventory.objects.select_for_update().get(product=product)
    except PackagedInventory.DoesNotExist:
        raise ValidationError(f"No packaged stock exists for {product}.")
        
    available = inventory.available
    if quantity > available:
        raise ValidationError(f"Only {available} packs of {product} are currently available.")

    # ---------------------------------------------------------
    # RESOLVE ACCOUNTHOLDER FOR PACKRELEASE CONTEXT
    # ---------------------------------------------------------
    request_user = item.request.requested_by # This is the "User" instance
    

    target_account = item.request.account_holder

# Fallback: if no dedicated customer account was chosen, use/create the requesting user's profile card
    if not target_account:
        request_user = item.request.requested_by
        target_account, _ = AccountHolder.objects.get_or_create(
            system_user=request_user,
            defaults={
                "name": request_user.get_full_name() or request_user.username,
                "account_type": "salesperson",
                "is_active": True
            }
        )

    # Create the release record bound to the correct target account holder
    release = PackRelease.objects.create(
        product=product, 
        request_item=item, 
        released_to=target_account,  # Accurately assigns stock & balance to account holder
        packs_out=quantity, 
        selling_price=selling_price, 
        created_by=manager, 
        notes=notes,
    )

    request = item.request
    items = list(request.items.all())
    if all(i.outstanding_quantity == 0 for i in items):
        request.status = "fulfilled"
        request.fulfilled_at = timezone.now()
        request.save(update_fields=["status", "fulfilled_at"])
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
        .get(pk=release.pk)
    )

    packs_returned = int(packs_returned)

    if packs_returned <= 0:
        raise ValidationError(
            "Must return at least one pack."
        )

    if packs_returned > release.packs_outstanding:
        raise ValidationError(
            f"Only {release.packs_outstanding} "
            "packs remain outstanding on this release."
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

    if release.packs_outstanding == 0:
        release.status = "fully_returned"
    elif returned.counts_against_release:
        release.status = "partially_returned"

    release.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

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
    release = (
        PackRelease.objects
        .select_for_update()
        .get(pk=release.pk)
    )

    amount = Decimal(amount)

    if amount <= Decimal("0.00"):
        raise ValidationError(
            "Payment amount must be greater than zero."
        )

    if amount > release.outstanding_balance:
        raise ValidationError(
            f"Payment exceeds the outstanding balance of "
            f"UGX {release.outstanding_balance:,.2f}."
        )

    payment = PaymentReceipt.objects.create(
        release=release,
        amount=amount,
        method=method,
        payment_reference=payment_reference,
        collected_by=collected_by,
        notes=notes,
    )

    return payment

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
        actual_backroom = data['backroom_count']
        selling_price = data['selling_price']

        # 1. Fetch exact current baseline from ledger
        expected_shelf = branch.get_stock_level(product, stock_location='shelf')
        expected_backroom = branch.get_stock_level(product, stock_location='backroom')

        # 2. Calculate sold quantities
        shelf_sold = max(0, expected_shelf - actual_shelf)
        backroom_sold = max(0, expected_backroom - actual_backroom)
        total_sold = shelf_sold + backroom_sold

        amount_due = total_sold * selling_price
        total_amount_billed += amount_due

        # 3. Create Audit Record
        StockAuditItem.objects.create(
            audit=audit,
            product=product,
            expected_shelf=expected_shelf,
            actual_shelf=actual_shelf,
            expected_backroom=expected_backroom,
            actual_backroom=actual_backroom,
            total_sold=total_sold,
            selling_price=selling_price,
            amount_due=amount_due,
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
def process_supermarket_audit(*, company, user, audit_items_data):
    """
    Processes physical shelf audit for consignment/display stock:
    1. Compares expected display quantity vs actual physical count.
    2. Calculates sold units.
    3. Updates ConsignmentInventory to match the actual physical count.
    4. Converts sold units into a billable balance on the company's AccountHolder ledger.
    """
    audit = StockAudit.objects.create(
        company=company,
        audited_by=user,
    )

    total_invoice_amount = 0

    for item_data in audit_items_data:
        product = item_data["product"]
        actual_count = item_data["actual_count"]
        selling_price = item_data["selling_price"]

        # Retrieve current recorded consignment balance
        consignment_record, _ = ConsignmentInventory.objects.get_or_create(
            company=company,
            product=product,
            defaults={"current_display_quantity": 0}
        )

        expected_qty = consignment_record.current_display_quantity

        # Create audit line item
        audit_item = StockAuditItem.objects.create(
            audit=audit,
            product=product,
            expected_quantity=expected_qty,
            actual_physical_count=actual_count,
            selling_price=selling_price,
        )

        total_invoice_amount += audit_item.calculated_amount_due

        # Update consignment display quantity to match ground truth
        consignment_record.current_display_quantity = actual_count
        consignment_record.last_audited_at = timezone.now()
        consignment_record.save()

    # Move company pipeline stage to recurring supply if not already set
    if company.pipeline_stage != 'recurring_supply':
        company.pipeline_stage = 'recurring_supply'
        company.save(update_fields=['pipeline_stage'])

    return audit