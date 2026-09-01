from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from ..models import (
    CoffeeStock,
    StockMovement,
    PackagedProduct,
    PackagedInventory,
    PackagingRun,
    PackRelease,
    PackReturn,
    RoastedSackSale,
)


ZERO = Decimal("0")


# ============================================================
# RAW / STAGE INVENTORY
# ============================================================

ZERO = Decimal("0.00")


# ============================================================
# KG-BASED INVENTORY
# ============================================================

def get_stage_inventory(stock, stage):

    incoming = (
        stock.movements
        .filter(to_stage=stage)
        .aggregate(total=Sum("quantity"))["total"]
        or ZERO
    )

    outgoing = (
        stock.movements
        .filter(from_stage=stage)
        .aggregate(total=Sum("quantity"))["total"]
        or ZERO
    )

    return incoming - outgoing


def get_green_available(stock):
    return get_stage_inventory(
        stock,
        CoffeeStock.StockStage.GREEN,
    )


def get_roasted_available(stock):
    return get_stage_inventory(
        stock,
        CoffeeStock.StockStage.ROASTED,
    )


def get_ground_available(stock):
    return get_stage_inventory(
        stock,
        CoffeeStock.StockStage.GROUND,
    )


def get_quaker_available(stock):
    return get_stage_inventory(
        stock,
        CoffeeStock.StockStage.QUAKERS,
    )


def get_stock_available(stock):

    return (
        get_green_available(stock)
        + get_roasted_available(stock)
        + get_ground_available(stock)
        + get_quaker_available(stock)
    )


# NOTE:
# Do not use get_packaged_available() for packaged
# inventory. Packaged coffee is measured in PACKS,
# not kilograms, and is tracked by PackagedInventory.


# ============================================================
# RECEIVING
# ============================================================

@transaction.atomic
def record_receipt(
    stock,
    quantity,
    user=None,
    reference="",
    notes="",
):
    """
    Record coffee entering green inventory.
    """

    quantity = Decimal(quantity)

    if quantity <= 0:
        raise ValueError(
            "Receipt quantity must be greater than zero."
        )

    locked_stock = (
        CoffeeStock.objects
        .select_for_update()
        .get(pk=stock.pk)
    )

    return StockMovement.objects.create(
        stock=locked_stock,
        movement_type="receipt",
        from_stage=None,
        to_stage="green_received",
        quantity=quantity,
        reference=reference,
        notes=notes,
        created_by=user,
    )


# ============================================================
# GENERIC STAGE MOVEMENT
# ============================================================

@transaction.atomic
def move_stock(
    *,
    stock,
    quantity,
    from_stage,
    to_stage,
    movement_type,
    user=None,
    reference="",
    notes="",
):
    """
    Move coffee between kg-based inventory stages.

    This is used for genuine internal transfers such as:

        roasted → ground

    It should NOT be used for roasting, sorting or grinding
    when the process has a separate return/output.
    """

    quantity = Decimal(quantity)

    if quantity <= ZERO:
        raise ValueError(
            "Movement quantity must be greater than zero."
        )

    locked_stock = (
        CoffeeStock.objects
        .select_for_update()
        .get(pk=stock.pk)
    )

    available = get_stage_inventory(
        locked_stock,
        from_stage,
    )

    if quantity > available:
        raise ValueError(
            f"Only {available} kg is available at "
            f"{from_stage}."
        )

    return StockMovement.objects.create(
        stock=locked_stock,
        movement_type=movement_type,
        from_stage=from_stage,
        to_stage=to_stage,
        quantity=quantity,
        reference=reference,
        notes=notes,
        created_by=user,
    )
# ============================================================
# PACKAGING
# ============================================================

@transaction.atomic
def issue_for_packaging(
    *,
    stock,
    product,
    input_kg,
    user=None,
    notes="",
):
    input_kg = Decimal(input_kg)

    if input_kg <= ZERO:
        raise ValueError(
            "Packaging input must be greater than zero."
        )

    if product.form == "beans":
        source_stage = StockStage.ROASTED

    elif product.form == "ground":
        source_stage = StockStage.GROUND

    else:
        raise ValueError(
            f"Unsupported product form: {product.form}"
        )

    locked_stock = (
        CoffeeStock.objects
        .select_for_update()
        .get(pk=stock.pk)
    )

    available = get_stage_inventory(
        locked_stock,
        source_stage,
    )

    if input_kg > available:
        raise ValueError(
            f"Only {available} kg is available at "
            f"{source_stage}."
        )

    run = PackagingRun.objects.create(
        stock=locked_stock,
        product=product,
        source_stage=source_stage,
        input_kg=input_kg,
        issued_by=user,
        notes=notes,
    )

    StockMovement.objects.create(
        stock=locked_stock,
        movement_type="packaging_issue",
        from_stage=source_stage,
        to_stage=None,
        quantity=input_kg,
        reference=f"Packaging #{run.pk}",
        notes=notes,
        created_by=user,
    )

    return run

# recieve the packaged coffee
@transaction.atomic
def complete_packaging(
    *,
    packaging_run,
    packs_produced,
    user=None,
    notes="",
):
    packs_produced = int(packs_produced)

    if packs_produced <= 0:
        raise ValueError(
            "At least one pack must be produced."
        )

    run = (
        PackagingRun.objects
        .select_for_update()
        .select_related("product", "stock")
        .get(pk=packaging_run.pk)
    )

    if run.status != "open":
        raise ValueError(
            "This packaging run has already been completed."
        )

    coffee_used_kg = (
        run.product.kg_per_pack
        * packs_produced
    )

    if coffee_used_kg > run.input_kg:
        raise ValueError(
            f"{packs_produced} packs require "
            f"{coffee_used_kg} kg, but only "
            f"{run.input_kg} kg was issued."
        )

    loss_kg = (
        run.input_kg
        - coffee_used_kg
    )

    run.packs_produced = packs_produced
    run.coffee_used_kg = coffee_used_kg
    run.loss_kg = loss_kg
    run.status = "completed"
    run.completed_by = user
    run.completed_at = timezone.now()

    if notes:
        run.notes = notes

    run.save(
        update_fields=[
            "packs_produced",
            "coffee_used_kg",
            "loss_kg",
            "status",
            "completed_by",
            "completed_at",
            "notes",
        ]
    )

    if loss_kg > ZERO:
        StockMovement.objects.create(
            stock=run.stock,
            movement_type="loss",
            from_stage=None,
            to_stage=None,
            quantity=loss_kg,
            reference=f"Packaging loss #{run.pk}",
            notes=notes,
            created_by=user,
        )

    PackagedInventory.objects.get_or_create(
        product=run.product,
    )

    return run


# RELEASE TO SALESPERSON

@transaction.atomic
def release_packs(
    *,
    product,
    packs_out,
    released_to,
    user=None,
    notes="",
):
    packs_out = int(packs_out)

    if packs_out <= 0:
        raise ValueError(
            "Must release at least one pack."
        )

    try:
        inventory = (
            PackagedInventory.objects
            .select_for_update()
            .get(product=product)
        )
    except PackagedInventory.DoesNotExist:
        raise ValueError(
            f"No packaged inventory exists for {product}."
        )

    available = inventory.available

    if packs_out > available:
        raise ValueError(
            f"Only {available} packs of "
            f"{product} are available."
        )

    return PackRelease.objects.create(
        product=product,
        released_to=released_to,
        packs_out=packs_out,
        created_by=user,
        notes=notes,
    )

# RETURN FROM SALESPERSON

@transaction.atomic
def return_packs(
    *,
    release,
    packs_returned,
    reason="",
    user=None,
):
    packs_returned = int(packs_returned)

    if packs_returned <= 0:
        raise ValueError(
            "Must return at least one pack."
        )

    locked_release = (
        PackRelease.objects
        .select_for_update()
        .get(pk=release.pk)
    )

    outstanding = locked_release.packs_outstanding

    if packs_returned > outstanding:
        raise ValueError(
            f"Only {outstanding} packs are "
            f"outstanding on this release."
        )

    returned = PackReturn.objects.create(
        release=locked_release,
        packs_returned=packs_returned,
        reason=reason,
        received_by=user,
    )

    remaining = locked_release.packs_outstanding

    if remaining == 0:
        locked_release.status = "fully_returned"
    else:
        locked_release.status = "partially_returned"

    locked_release.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    return returned

# BULK ROASTED COFFEE SALE


@transaction.atomic
def sell_roasted_sack(
    *,
    stock,
    buyer_name,
    kg_sold,
    price_per_kg,
    user=None,
):
    """
    Sell roasted coffee directly in bulk.

    This is separate from packaged coffee sales.
    """

    kg_sold = Decimal(kg_sold)
    price_per_kg = Decimal(price_per_kg)

    if kg_sold <= 0:
        raise ValueError(
            "Quantity sold must be greater than zero."
        )

    if price_per_kg < 0:
        raise ValueError(
            "Price per kg cannot be negative."
        )

    locked_stock = (
        CoffeeStock.objects
        .select_for_update()
        .get(pk=stock.pk)
    )

    available = get_stage_inventory(
    locked_stock,
    CoffeeStock.StockStage.ROASTED,
)

    if kg_sold > available:
        raise ValueError(
            f"Only {available} kg roasted coffee "
            f"is available."
        )

    sale = RoastedSackSale.objects.create(
        stock=locked_stock,
        buyer_name=buyer_name,
        kg_sold=kg_sold,
        price_per_kg=price_per_kg,
        created_by=user,
    )

    StockMovement.objects.create(
    stock=locked_stock,
    movement_type="dispatch",
    from_stage=CoffeeStock.StockStage.ROASTED,
    to_stage=None,
    quantity=kg_sold,
    reference=f"Roasted bulk sale: {buyer_name}",
    notes="Direct roasted coffee sale.",
    created_by=user,
)

    return sale