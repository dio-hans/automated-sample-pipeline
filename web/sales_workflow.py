from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    PackRelease,
    PackReturn,
    PackSettlement,
    PackagedProduct,
    StockRequest,
    StockRequestItem,
)


def _available(product):
    try:
        return product.inventory.available
    except product.inventory.RelatedObjectDoesNotExist:
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
def fulfill_request_item(*, item, quantity, price_per_pack, manager, notes=""):
    """Issue packaged stock against one request item."""
    item = (
        StockRequestItem.objects
        .select_for_update()
        .select_related("request", "product")
        .get(pk=item.pk)
    )
    quantity = int(quantity)
    price_per_pack = Decimal(price_per_pack)

    if quantity <= 0:
        raise ValidationError("Issue quantity must be greater than zero.")
    if price_per_pack < 0:
        raise ValidationError("Price per pack cannot be negative.")
    if quantity > item.outstanding_quantity:
        raise ValidationError(
            f"Only {item.outstanding_quantity} packs remain to fulfil this item."
        )

    product = PackagedProduct.objects.select_for_update().get(pk=item.product_id)
    available = product.inventory.available if hasattr(product, "inventory") else 0
    if quantity > available:
        raise ValidationError(
            f"Only {available} packs of {product} are currently available."
        )

    release = PackRelease.objects.create(
        product=product,
        request_item=item,
        released_to=item.request.requested_by,
        packs_out=quantity,
        price_per_pack=price_per_pack,
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
        request.save(update_fields=["status"])

    return release


@transaction.atomic
def return_packs(*, release, packs_returned, reason="", user=None, notes=""):
    """Record physical packs coming back to the store."""
    release = (
        PackRelease.objects
        .select_for_update()
        .select_related("product", "released_to")
        .get(pk=release.pk)
    )
    packs_returned = int(packs_returned)

    if packs_returned <= 0:
        raise ValidationError("Must return at least one pack.")
    if packs_returned > release.packs_outstanding:
        raise ValidationError(
            f"Only {release.packs_outstanding} packs are outstanding on this release."
        )

    returned = PackReturn.objects.create(
        release=release,
        packs_returned=packs_returned,
        reason=reason,
        notes=notes,
        received_by=user,
    )

    if release.packs_outstanding == 0:
        release.status = "fully_returned"
    else:
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

    amount_due = Decimal(packs_sold) * release.price_per_pack
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
