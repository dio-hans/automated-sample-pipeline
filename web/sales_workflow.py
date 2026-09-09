
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    AccountHolder,
    PackRelease,
    PackReturn,
    PackSettlement,
    PackagedProduct,
    PackagedInventory,
    PaymentReceipt,
    StockRequest,
    StockRequestItem,
)


def _available(product):
    try:
        return product.inventory.available
    except PackagedInventory.DoesNotExist:
        return 0


@transaction.atomic
def create_stock_request(*, user, purpose, items, company=None, notes=""):
    """Create one internal stock request containing one or more products."""
    if not items:
        raise ValidationError("Add at least one product to the request.")

    request = StockRequest.objects.create(
        requested_by=user,
        company=company,
        purpose=purpose,
        notes=notes,
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
    
    # Check if this user already has an AccountHolder profile card linked
    account_holder = AccountHolder.objects.filter(system_user=request_user).first()
    
    # Auto-bootstrap profile if they don't have an account card entry yet
    if not account_holder:
        account_holder, _ = AccountHolder.objects.get_or_create(
            system_user=request_user,
            defaults={
                "name": request_user.get_full_name() or request_user.username,
                "account_type": "salesperson",
                "is_active": True
            }
        )

    # 🚀 Create the release record using the valid AccountHolder instance!
    release = PackRelease.objects.create(
        product=product, 
        request_item=item, 
        released_to=account_holder,  #  Fixed: We pass the AccountHolder here!
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
