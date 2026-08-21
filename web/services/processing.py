from dataclasses import dataclass
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from ..models import StockMovement
from .inventory import get_stage_inventory


# Each processing step: (movement_type, from_stage, to_stage, loss_reference)
PROCESS_STEPS = {
    "roast":   ("roast_output",   "green_received", "roasted",  "roasting"),
    "grind":   ("grind_output",   "roasted",        "ground",   "grinding"),
    "package": ("package_output", "ground",         "packaged", "packaging"),
}


@dataclass
class ProcessResult:
    movement: StockMovement
    loss_movement: StockMovement
    input_quantity: Decimal
    output_quantity: Decimal
    loss_quantity: Decimal


@transaction.atomic
def process_stock(*, stock, step, input_quantity, output_quantity, user=None, notes=""):
    """
    Move coffee through one processing step, recording the loss explicitly.

    Two movements are posted:
      - output: from_stage -> to_stage, qty = output (what survived the step)
      - loss:   from_stage -> None,     qty = input - output (what was lost)

    Both draw from the source stage, so stage_quantity(source) drops by the
    full input weight and stage_quantity(target) rises by the output weight.
    Every gram is traceable in the ledger.
    """
    if step not in PROCESS_STEPS:
        raise ValueError(f"Unknown processing step '{step}'.")

    movement_type, from_stage, to_stage, loss_ref = PROCESS_STEPS[step]

    input_quantity = Decimal(input_quantity)
    output_quantity = Decimal(output_quantity)

    if output_quantity <= 0:
        raise ValueError("Output quantity must be greater than zero.")
    if output_quantity > input_quantity:
        raise ValueError("Output cannot be greater than input.")

    available = get_stage_inventory(stock, from_stage)
    if input_quantity > available:
        raise ValueError(
            f"Insufficient stock at {from_stage}. Only {available} kg available."
        )

    loss_quantity = input_quantity - output_quantity

    output_movement = StockMovement.objects.create(
        stock=stock,
        movement_type=movement_type,
        from_stage=from_stage,
        to_stage=to_stage,
        quantity=output_quantity,
        notes=notes,
        created_by=user,
    )

    loss_movement = None
    if loss_quantity > 0:
        loss_movement = StockMovement.objects.create(
            stock=stock,
            movement_type="loss",
            from_stage=from_stage,
            to_stage=None,
            quantity=loss_quantity,
            reference=loss_ref,
            notes=f"Loss during {loss_ref}",
            created_by=user,
        )

    # Advance the batch's current stage pointer
    stock.stage = to_stage
    stock.save(update_fields=["stage", "updated_at"])

    return ProcessResult(
        movement=output_movement,
        loss_movement=loss_movement,
        input_quantity=input_quantity,
        output_quantity=output_quantity,
        loss_quantity=loss_quantity,
    )