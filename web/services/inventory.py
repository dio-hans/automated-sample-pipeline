from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from ..models import StockMovement


ZERO = Decimal("0")


def get_stage_inventory(stock, stage):
    """
    Return the quantity currently available at a specific stage.
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
    Return the total quantity of a batch currently
    inside the business.
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
    return get_stage_inventory(stock, "green_received")


def get_roasted_available(stock):
    return get_stage_inventory(stock, "roasted")


def get_ground_available(stock):
    return get_stage_inventory(stock, "ground")


def get_packaged_available(stock):
    return get_stage_inventory(stock, "packaged")


@transaction.atomic
def record_receipt(
    stock,
    quantity,
    user=None,
    reference="",
    notes="",
):
    """
    Receive coffee into green inventory.
    """

    if quantity <= 0:
        raise ValueError("Receipt quantity must be greater than zero.")

    return StockMovement.objects.create(
        stock=stock,
        movement_type="receipt",
        from_stage=None,
        to_stage="green_received",
        quantity=quantity,
        reference=reference,
        notes=notes,
        created_by=user,
    )


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
    Move stock between processing stages.

    The ledger is updated; stock balances are calculated from
    the resulting movements.
    """

    if quantity <= 0:
        raise ValueError(
            "Movement quantity must be greater than zero."
        )

    available = get_stage_inventory(
        stock,
        from_stage,
    )

    if quantity > available:
        raise ValueError(
            f"Insufficient stock. "
            f"Only {available} kg is available at "
            f"{from_stage}."
        )

    return StockMovement.objects.create(
        stock=stock,
        movement_type=movement_type,
        from_stage=from_stage,
        to_stage=to_stage,
        quantity=quantity,
        reference=reference,
        notes=notes,
        created_by=user,
    )