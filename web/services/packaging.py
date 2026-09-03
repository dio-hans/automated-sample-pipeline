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
    """Complete a packaging run and make its produced packs available."""
    input_kg = Decimal(input_kg)
    packs_produced = int(packs_produced)

    if input_kg <= 0:
        raise ValidationError("Packaging input must be greater than zero.")
    if packs_produced <= 0:
        raise ValidationError("At least one pack must be produced.")

    stock = (
        CoffeeStock.objects
        .select_for_update()
        .get(pk=stock.pk)
    )
    product = PackagedProduct.objects.select_for_update().get(pk=product.pk)

    expected_stage = (
        "ground"
        if product.form == "ground"
        else "roasted"
    )
    if source_stage != expected_stage:
        raise ValidationError(
            f"{product.get_form_display()} products must be packaged from "
            f"{expected_stage} coffee."
        )

    available = get_stage_inventory(stock, source_stage)
    if available < input_kg:
        raise ValidationError(
            f"Insufficient {source_stage} inventory. Available: {available} kg, required: {input_kg} kg."
        )
    represented_kg = Decimal(packs_produced) * product.kg_per_pack
    if represented_kg > input_kg:
        raise ValidationError(
            f"{packs_produced} packs require {represented_kg} kg, but only {input_kg} kg was issued."
        )

    loss_kg = input_kg - represented_kg
    now = timezone.now()

    run = PackagingRun.objects.create(
        stock=stock,
        product=product,
        source_stage=source_stage,
        input_kg=input_kg,
        packs_produced=packs_produced,
        coffee_used_kg=represented_kg,
        loss_kg=loss_kg,
        status="completed",
        issued_at=now,
        completed_at=now,
        issued_by=user,
        completed_by=user,
        notes=notes,
    )

    StockMovement.objects.create(
        stock=stock,
        movement_type="packaging_issue",
        from_stage=source_stage,
        to_stage=None,
        quantity=input_kg,
        reference=f"PACK-{run.pk}",
        notes=notes,
        created_by=user,
    )

    PackagedInventory.objects.get_or_create(product=product)
    return run


@transaction.atomic
def execute_pack_release(*, product: PackagedProduct, released_to, packs_out: int,
                       price_per_pack: Decimal, user=None, notes: str = "", request_item=None) -> PackRelease:
    """Release physical packaged stock to a salesperson."""
    packs_out = int(packs_out)
    price_per_pack = Decimal(price_per_pack)
    if packs_out <= 0:
        raise ValidationError("Release quantity must be greater than zero.")
    if price_per_pack < 0:
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
        price_per_pack=price_per_pack,
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