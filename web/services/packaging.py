from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from ..models import (
    CoffeeStock, PackagedProduct, PackagingRun, 
    PackagedInventory, PackRelease, PackReturn, StockMovement
)
from .inventory import get_stage_inventory

@transaction.atomic
def execute_packaging_run(*, stock: CoffeeStock, product: PackagedProduct, source_stage: str, input_kg: Decimal, packs_produced: int, user=None, notes: str = "") -> PackagingRun:
    """
    Deducts bulk roasted/ground stock, computes loss, logs StockMovement,
    and updates PackagedInventory.
    """
    available = get_stage_inventory(stock, source_stage)
    if available < input_kg:
        raise ValidationError(f"Insufficient {source_stage} inventory. Available: {available} kg, Required: {input_kg} kg.")

    kg_per_pack = Decimal(str(product.pack_size.weight_kg))
    represented_kg = Decimal(packs_produced) * kg_per_pack
    loss_kg = input_kg - represented_kg

    if loss_kg < Decimal('0'):
        raise ValidationError(f"Packs produced ({represented_kg} kg) exceed input weight ({input_kg} kg).")

    # 1. Create PackagingRun
    run = PackagingRun.objects.create(
        stock=stock,
        product=product,
        source_stage=source_stage,
        input_kg=input_kg,
        packs_produced=packs_produced,
        loss_kg=loss_kg,
        notes=notes,
        created_by=user
    )

    # 2. Log bulk stock movement out
    StockMovement.objects.create(
        stock=stock,
        movement_type="packaging",
        from_stage=source_stage,
        quantity=input_kg,
        reference=f"RUN-{run.id}",
        created_by=user
    )

    # 3. Update PackagedInventory
    inventory, _ = PackagedInventory.objects.get_or_create(product=product)
    inventory.available += packs_produced
    inventory.save(update_fields=["available", "updated_at"])

    return run


@transaction.atomic
def execute_pack_release(*, product: PackagedProduct, released_to: str, packs_out: int, price_per_pack: Decimal, user=None, notes: str = "") -> PackRelease:
    """
    Releases packaged coffee to a recipient and decrements live inventory.
    """
    inventory = PackagedInventory.objects.select_for_update().get(product=product)
    if inventory.available < packs_out:
        raise ValidationError(f"Insufficient packaged stock. Available: {inventory.available}, Requested: {packs_out}")

    release = PackRelease.objects.create(
        product=product,
        released_to=released_to,
        packs_out=packs_out,
        price_per_pack=price_per_pack,
        amount_due=Decimal(packs_out) * price_per_pack,
        status="open",
        released_by=user,
        notes=notes
    )

    inventory.available -= packs_out
    inventory.packs_released += packs_out
    inventory.save(update_fields=["available", "packs_released", "updated_at"])

    return release


@transaction.atomic
def execute_pack_return(*, release: PackRelease, packs_returned: int, reason: str = "", user=None, notes: str = "") -> PackReturn:
    """
    Processes returned packages back into active inventory and reconciles release status.
    """
    remaining_unreturned = release.packs_out - release.packs_returned - release.packs_sold
    if packs_returned > remaining_unreturned:
        raise ValidationError(f"Cannot return {packs_returned} packs. Maximum returnable: {remaining_unreturned}")

    pack_return = PackReturn.objects.create(
        release=release,
        packs_returned=packs_returned,
        reason=reason,
        notes=notes,
        received_by=user
    )

    # Update release status balances
    release.packs_returned += packs_returned
    if release.packs_returned + release.packs_sold >= release.packs_out:
        release.status = "reconciled"
    elif release.packs_returned > 0 or release.packs_sold > 0:
        release.status = "partially_returned"
    release.save(update_fields=["packs_returned", "status", "updated_at"])

    # Increment available inventory
    inventory = PackagedInventory.objects.select_for_update().get(product=release.product)
    inventory.available += packs_returned
    inventory.packs_returned += packs_returned
    inventory.save(update_fields=["available", "packs_returned", "updated_at"])

    return pack_return