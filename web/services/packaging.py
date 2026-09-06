

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import (
    CoffeeStock,
    PackagedProduct,
    PackagingRun,
    PackagedInventory,
    PackRelease,
    PackReturn,
    StockMovement,
)
from .inventory import get_stage_inventory


@transaction.atomic
def execute_packaging_run(*, stock: CoffeeStock, product: PackagedProduct, source_stage: str,
                           input_kg: Decimal, packs_produced: int, user=None, notes: str = "") -> PackagingRun:
    input_kg = Decimal(input_kg); packs_produced = int(packs_produced)
    if input_kg <= 0: raise ValidationError("Packaging input must be greater than zero.")
    if packs_produced <= 0: raise ValidationError("At least one pack must be produced.")
    if not product.is_active: raise ValidationError("The selected packaged product is inactive.")
    expected_stage = "roasted" if product.form == "beans" else "ground"
    if source_stage != expected_stage:
        raise ValidationError(f"{product.get_form_display()} coffee must be packaged from {expected_stage} stock.")
    locked_stock = CoffeeStock.objects.select_for_update().get(pk=stock.pk)
    available = get_stage_inventory(locked_stock, source_stage)
    if available < input_kg:
        raise ValidationError(f"Insufficient {source_stage} inventory. Available: {available} kg, required: {input_kg} kg.")
    product = PackagedProduct.objects.select_for_update().get(pk=product.pk)
    represented_kg = Decimal(packs_produced) * product.kg_per_pack
    if represented_kg > input_kg:
        raise ValidationError(f"{packs_produced} packs require {represented_kg} kg, but only {input_kg} kg was issued.")
    loss_kg = input_kg - represented_kg; now = timezone.now()
    run = PackagingRun.objects.create(stock=locked_stock, product=product, source_stage=source_stage,
        input_kg=input_kg, packs_produced=packs_produced, coffee_used_kg=represented_kg, loss_kg=loss_kg,
        status="completed", issued_at=now, completed_at=now, issued_by=user, completed_by=user, notes=notes)
    StockMovement.objects.create(stock=locked_stock, movement_type="packaging_issue", from_stage=source_stage,
        to_stage=None, quantity=input_kg, reference=f"PACK-{run.pk}", notes=notes, created_by=user)
    if loss_kg > 0:
        StockMovement.objects.create(stock=locked_stock, movement_type="loss", from_stage=None, to_stage=None,
            quantity=loss_kg, reference=f"PACK-LOSS-{run.pk}",
            notes=f"Packaging loss: {input_kg} kg issued, {represented_kg} kg packed.", created_by=user)
    PackagedInventory.objects.get_or_create(product=product)
    return run


@transaction.atomic
def execute_pack_release(*, product: PackagedProduct, released_to, packs_out: int,
                       selling_price: Decimal, user=None, notes: str = "", request_item=None) -> PackRelease:
    """Release physical packaged stock to a salesperson."""
    packs_out = int(packs_out)
    selling_price = Decimal(selling_price)
    if packs_out <= 0:
        raise ValidationError("Release quantity must be greater than zero.")
    if selling_price < 0:
        raise ValidationError("Price per pack cannot be negative.")

    product = PackagedProduct.objects.select_for_update().get(pk=product.pk)
    try:
        available = product.inventory.available
    except PackagedInventory.DoesNotExist:
        available = 0

    if packs_out > available:
        raise ValidationError(
            f"Insufficient packaged stock. Available: {available}, requested: {packs_out}."
        )

    return PackRelease.objects.create(
        product=product,
        request_item=request_item,
        released_to=released_to,
        packs_out=packs_out,
        selling_price=selling_price,
        created_by=user,
        notes=notes,
    )


@transaction.atomic
def execute_pack_return(*, release: PackRelease, packs_returned: int,
                       reason: str = "", user=None, notes: str = "") -> PackReturn:
    """Receive physical packs back into store stock."""
    release = PackRelease.objects.select_for_update().get(pk=release.pk)
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

    release.status = (
        "fully_returned" if release.packs_outstanding == 0 else "partially_returned"
    )
    release.save(update_fields=["status", "updated_at"])
    return returned



