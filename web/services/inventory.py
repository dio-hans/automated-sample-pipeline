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

def get_stage_inventory(stock, stage):
    """
    Calculate the current quantity of a batch at a stage.

    Inventory is derived from the movement ledger.

        incoming to stage
        -
        outgoing from stage
        =
        current stage balance
    """

    incoming = (
        stock.movements
        .filter(to_stage=stage)
        .aggregate(total=Sum("quantity"))
        ["total"]
        or ZERO
    )

    outgoing = (
        stock.movements
        .filter(from_stage=stage)
        .aggregate(total=Sum("quantity"))
        ["total"]
        or ZERO
    )

    return incoming - outgoing


def get_stock_available(stock):
    """
    Return the total quantity of coffee from this batch
    that is currently somewhere inside the kg-based
    inventory stages.

    This is derived from the ledger.
    """

    incoming = (
        stock.movements
        .exclude(to_stage=None)
        .aggregate(total=Sum("quantity"))
        ["total"]
        or ZERO
    )

    outgoing = (
        stock.movements
        .exclude(from_stage=None)
        .aggregate(total=Sum("quantity"))
        ["total"]
        or ZERO
    )

    return incoming - outgoing


def get_green_available(stock):
    return get_stage_inventory(
        stock,
        "green_received",
    )


def get_roasted_available(stock):
    return get_stage_inventory(
        stock,
        "roasted",
    )


def get_ground_available(stock):
    return get_stage_inventory(
        stock,
        "ground",
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

    Use this for simple stage transfers.

    For roasting/grinding with loss, use process_stock().
    For packaging, use run_packaging().
    """

    quantity = Decimal(quantity)

    if quantity <= 0:
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
            f"Insufficient stock at {from_stage}. "
            f"Only {available} kg is available."
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
def run_packaging(
    *,
    stock,
    product,
    input_kg,
    packs_produced,
    user=None,
    notes="",
):
    """
    Convert roasted or ground coffee into packaged SKU units.

    Beans:
        roasted -> packaged product

    Ground:
        ground -> packaged product

    Packaging is NOT a generic processing step.

    The kg is removed from the source stage and the
    resulting packs are added to PackagedInventory.

    Expected coffee required:

        packs_produced × product.kg_per_pack

    Actual packaging loss:

        input_kg - coffee represented by packs

    """

    input_kg = Decimal(input_kg)
    packs_produced = int(packs_produced)

    if input_kg <= 0:
        raise ValueError(
            "Packaging input must be greater than zero."
        )

    if packs_produced <= 0:
        raise ValueError(
            "Must produce at least one pack."
        )

    # --------------------------------------------------
    # Determine source stage
    # --------------------------------------------------

    if product.form == "ground":
        source_stage = "ground"
    elif product.form == "beans":
        source_stage = "roasted"
    else:
        raise ValueError(
            f"Unsupported product form: {product.form}"
        )

    # --------------------------------------------------
    # Lock the batch
    # --------------------------------------------------

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
            f"{source_stage}. Need {input_kg} kg."
        )

    # --------------------------------------------------
    # Calculate expected usage
    # --------------------------------------------------

    expected_kg = (
        product.kg_per_pack * packs_produced
    )

    if expected_kg > input_kg:
        raise ValueError(
            f"{packs_produced} packs of {product} "
            f"require {expected_kg} kg, but only "
            f"{input_kg} kg was supplied."
        )

    loss_kg = input_kg - expected_kg

    # --------------------------------------------------
    # Remove coffee from kg inventory
    # --------------------------------------------------

    StockMovement.objects.create(
        stock=locked_stock,
        movement_type="package_input",
        from_stage=source_stage,
        to_stage=None,
        quantity=input_kg,
        reference=f"Packaging: {product}",
        notes=notes,
        created_by=user,
    )

    # --------------------------------------------------
    # Record packaging run
    # --------------------------------------------------

    run = PackagingRun.objects.create(
        stock=locked_stock,
        product=product,
        input_kg=input_kg,
        packs_produced=packs_produced,
        loss_kg=loss_kg,
        source_stage=source_stage,
        created_by=user,
        notes=notes,
    )

    # --------------------------------------------------
    # Lock packaged inventory
    # --------------------------------------------------

    inventory, created = (
        PackagedInventory.objects
        .select_for_update()
        .get_or_create(product=product)
    )

    inventory.packs_in_stock += packs_produced
    inventory.save(
        update_fields=[
            "packs_in_stock",
            "updated_at",
        ]
    )

    return run


# ============================================================
# RELEASE TO SALESPERSON
# ============================================================

@transaction.atomic
def release_packs(
    *,
    product,
    packs_out,
    released_to,
    price_per_pack=0,
    user=None,
    notes="",
):
    """
    Release packaged coffee from the store to a salesperson.

    RELEASE DOES NOT MEAN SOLD.

    The salesperson now has custody of the packs.
    """

    packs_out = int(packs_out)
    price_per_pack = Decimal(price_per_pack)

    if packs_out <= 0:
        raise ValueError(
            "Must release at least one pack."
        )

    if price_per_pack < 0:
        raise ValueError(
            "Price per pack cannot be negative."
        )

    inventory, created = (
        PackagedInventory.objects
        .select_for_update()
        .get_or_create(product=product)
    )

    available = inventory.available

    if packs_out > available:
        raise ValueError(
            f"Only {available} packs of {product} "
            f"are available. Need {packs_out}."
        )

    release = PackRelease.objects.create(
        product=product,
        released_to=released_to,
        packs_out=packs_out,
        price_per_pack=price_per_pack,
        created_by=user,
        notes=notes,
    )

    inventory.packs_released += packs_out

    inventory.save(
        update_fields=[
            "packs_released",
            "updated_at",
        ]
    )

    return release


# ============================================================
# RETURN FROM SALESPERSON
# ============================================================

@transaction.atomic
def return_packs(
    *,
    release,
    packs_returned,
    reason="",
    user=None,
):
    """
    Record coffee returned from a salesperson.

    Returned packs go back into store inventory.
    """

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

    outstanding = (
        locked_release.packs_out
        - locked_release.packs_returned
        - locked_release.packs_sold
    )

    if packs_returned > outstanding:
        raise ValueError(
            f"Only {outstanding} packs are outstanding "
            f"on this release. Cannot return "
            f"{packs_returned}."
        )

    ret = PackReturn.objects.create(
        release=locked_release,
        packs_returned=packs_returned,
        reason=reason,
        received_by=user,
    )

    locked_release.packs_returned += packs_returned

    remaining = (
        locked_release.packs_out
        - locked_release.packs_returned
        - locked_release.packs_sold
    )

    if remaining == 0:
        if locked_release.packs_sold > 0:
            locked_release.status = "sold"
        else:
            locked_release.status = "fully_returned"
    else:
        locked_release.status = "partially_returned"

    locked_release.save(
        update_fields=[
            "packs_returned",
            "status",
            "updated_at",
        ]
    )

    inventory, created = (
        PackagedInventory.objects
        .select_for_update()
        .get_or_create(
            product=locked_release.product
        )
    )

    inventory.packs_returned += packs_returned

    inventory.save(
        update_fields=[
            "packs_returned",
            "updated_at",
        ]
    )

    return ret


# ============================================================
# CONFIRM SOLD
# ============================================================

@transaction.atomic
def confirm_sold(
    *,
    release,
    packs_sold,
    user=None,
):
    """
    Confirm that packs released to a salesperson
    were actually sold.

    This is the transaction that connects inventory
    to sales value.
    """

    packs_sold = int(packs_sold)

    if packs_sold <= 0:
        raise ValueError(
            "Must confirm at least one sold pack."
        )

    locked_release = (
        PackRelease.objects
        .select_for_update()
        .get(pk=release.pk)
    )

    outstanding = (
        locked_release.packs_out
        - locked_release.packs_returned
        - locked_release.packs_sold
    )

    if packs_sold > outstanding:
        raise ValueError(
            f"Only {outstanding} packs can be "
            f"confirmed sold on this release."
        )

    locked_release.packs_sold += packs_sold

    remaining = (
        locked_release.packs_out
        - locked_release.packs_returned
        - locked_release.packs_sold
    )

    if remaining == 0:
        locked_release.status = "sold"
    else:
        locked_release.status = "partially_returned"

    locked_release.save(
        update_fields=[
            "packs_sold",
            "status",
            "updated_at",
        ]
    )

    return locked_release


# ============================================================
# BULK ROASTED COFFEE SALE
# ============================================================

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
        "roasted",
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
        from_stage="roasted",
        to_stage=None,
        quantity=kg_sold,
        reference=f"Roasted bulk sale: {buyer_name}",
        notes="Direct roasted coffee sale.",
        created_by=user,
    )

    return sale