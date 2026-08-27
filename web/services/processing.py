from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from ..models import CoffeeStock, StockMovement
from .inventory import get_stage_inventory


PROCESS_STEPS = {
    "roast": (
        "roast_output",
        "green_received",
        "roasted",
        "roasting",
    ),

    "grind": (
        "grind_output",
        "roasted",
        "ground",
        "grinding",
    ),
}


@dataclass
class ProcessResult:
    movement: StockMovement
    loss_movement: StockMovement | None
    input_quantity: Decimal
    output_quantity: Decimal
    loss_quantity: Decimal


@transaction.atomic
def process_stock(
    *,
    stock,
    step,
    input_quantity,
    output_quantity,
    user=None,
    notes="",
):
    """
    Process coffee through roasting or grinding.

    Inventory rule:

        source quantity
              ↓
        ┌─────┴─────┐
        ↓           ↓
      output       loss

    Example:

        300 kg green
              ↓
        roasting
              ↓
        270 kg roasted
         30 kg loss

    The ledger records both the output and the loss.
    """

    if step not in PROCESS_STEPS:
        raise ValueError(
            f"Unknown processing step '{step}'. "
            f"Allowed steps: {', '.join(PROCESS_STEPS.keys())}."
        )

    movement_type, from_stage, to_stage, loss_reference = (
        PROCESS_STEPS[step]
    )

    input_quantity = Decimal(input_quantity)
    output_quantity = Decimal(output_quantity)

    if input_quantity <= 0:
        raise ValueError(
            "Input quantity must be greater than zero."
        )

    if output_quantity <= 0:
        raise ValueError(
            "Output quantity must be greater than zero."
        )

    if output_quantity > input_quantity:
        raise ValueError(
            "Output quantity cannot exceed input quantity."
        )

    # Lock the batch for the duration of this transaction.
    locked_stock = (
        CoffeeStock.objects
        .select_for_update()
        .get(pk=stock.pk)
    )

    available = get_stage_inventory(
        locked_stock,
        from_stage,
    )

    if input_quantity > available:
        raise ValueError(
            f"Insufficient stock at {from_stage}. "
            f"Only {available} kg is available."
        )

    loss_quantity = (
        input_quantity - output_quantity
    )

    reference = (
        f"{loss_reference.title()} - "
        f"{locked_stock.batch_number}"
    )

    # --------------------------------------------------
    # 1. OUTPUT
    # --------------------------------------------------

    output_movement = StockMovement.objects.create(
        stock=locked_stock,
        movement_type=movement_type,
        from_stage=from_stage,
        to_stage=to_stage,
        quantity=output_quantity,
        reference=reference,
        notes=notes,
        created_by=user,
    )

    # --------------------------------------------------
    # 2. LOSS
    # --------------------------------------------------

    loss_movement = None

    if loss_quantity > 0:
        loss_movement = StockMovement.objects.create(
            stock=locked_stock,
            movement_type="loss",
            from_stage=from_stage,
            to_stage=None,
            quantity=loss_quantity,
            reference=f"{loss_reference.title()} loss",
            notes=(
                f"Loss recorded during {loss_reference}. "
                f"Input: {input_quantity} kg. "
                f"Output: {output_quantity} kg."
            ),
            created_by=user,
        )

    # IMPORTANT:
    #
    # Do NOT update locked_stock.stage.
    #
    # The ledger is the source of truth.
    #
    # A batch may have green, roasted and ground
    # quantities simultaneously.

    return ProcessResult(
        movement=output_movement,
        loss_movement=loss_movement,
        input_quantity=input_quantity,
        output_quantity=output_quantity,
        loss_quantity=loss_quantity,
    )