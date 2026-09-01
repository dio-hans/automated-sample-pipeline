from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ..models import (
    CoffeeStock,
    StockMovement,
    ProcessingRun,
    StockStage,
)

from .inventory import get_stage_inventory


ZERO = Decimal("0.00")


@dataclass
class ProcessingResult:

    processing_run: ProcessingRun
    movement: StockMovement


# ============================================================
# ISSUE COFFEE FOR PROCESSING
# ============================================================

@transaction.atomic
def issue_for_processing(
    *,
    stock,
    process_type,
    input_quantity,
    user=None,
    notes="",
):
    """
    Issue coffee to a processing operation.

    This DOES NOT record the output.

    Example:

        400 kg green
              ↓
        issued for roasting

    The roasting return is recorded later.
    """

    input_quantity = Decimal(input_quantity)

    if input_quantity <= 0:
        raise ValueError(
            "Input quantity must be greater than zero."
        )

    process_config = {
        "roasting": {
            "input_stage": StockStage.GREEN,
            "output_stage": StockStage.ROASTED,
            "movement_type": "roast_issue",
        },

        "sorting": {
            "input_stage": StockStage.ROASTED,
            "output_stage": StockStage.ROASTED,
            "movement_type": "sort_input",
        },

        "grinding": {
            "input_stage": StockStage.ROASTED,
            "output_stage": StockStage.GROUND,
            "movement_type": "grind_input",
        },
    }

    if process_type not in process_config:
        raise ValueError(
            f"Unsupported processing type: {process_type}"
        )

    config = process_config[process_type]

    locked_stock = (
        CoffeeStock.objects
        .select_for_update()
        .get(pk=stock.pk)
    )

    available = get_stage_inventory(
        locked_stock,
        config["input_stage"],
    )

    if input_quantity > available:
        raise ValueError(
            f"Only {available} kg is available at "
            f"{config['input_stage']}."
        )

    processing_run = ProcessingRun.objects.create(
        stock=locked_stock,
        process_type=process_type,
        input_stage=config["input_stage"],
        output_stage=config["output_stage"],
        input_quantity=input_quantity,
        issued_by=user,
        notes=notes,
    )

    movement = StockMovement.objects.create(
        stock=locked_stock,
        movement_type=config["movement_type"],
        from_stage=config["input_stage"],
        to_stage=None,
        quantity=input_quantity,
        reference=f"Processing #{processing_run.pk}",
        notes=notes,
        created_by=user,
    )

    return ProcessingResult(
        processing_run=processing_run,
        movement=movement,
    )


# ============================================================
# COMPLETE ROASTING
# ============================================================

@transaction.atomic
def complete_roasting(
    *,
    processing_run,
    output_quantity,
    user=None,
    notes="",
):
    """
    Receive roasted coffee back from roasting.

    Example:

        Issued: 400 kg

        Returned: 370 kg
        Loss:      30 kg
    """

    output_quantity = Decimal(output_quantity)

    if output_quantity <= 0:
        raise ValueError(
            "Roasted quantity must be greater than zero."
        )

    run = (
        ProcessingRun.objects
        .select_for_update()
        .select_related("stock")
        .get(pk=processing_run.pk)
    )

    if run.process_type != "roasting":
        raise ValueError(
            "This processing run is not a roasting run."
        )

    if run.status != "open":
        raise ValueError(
            "This roasting run has already been completed."
        )

    if output_quantity > run.input_quantity:
        raise ValueError(
            "Roasted output cannot exceed input."
        )

    loss_quantity = (
        run.input_quantity
        - output_quantity
    )

    output_movement = StockMovement.objects.create(
        stock=run.stock,
        movement_type="roast_return",
        from_stage=None,
        to_stage=StockStage.ROASTED,
        quantity=output_quantity,
        reference=f"Processing #{run.pk}",
        notes=notes,
        created_by=user,
    )

    loss_movement = None

    if loss_quantity > ZERO:

        loss_movement = StockMovement.objects.create(
            stock=run.stock,
            movement_type="loss",
            from_stage=None,
            to_stage=None,
            quantity=loss_quantity,
            reference=f"Roasting loss #{run.pk}",
            notes=(
                f"Roasting loss. "
                f"Input: {run.input_quantity} kg. "
                f"Output: {output_quantity} kg."
            ),
            created_by=user,
        )

    run.output_quantity = output_quantity
    run.loss_quantity = loss_quantity
    run.status = "completed"
    run.completed_by = user
    run.completed_at = timezone.now()

    if notes:
        run.notes = notes

    run.save(
        update_fields=[
            "output_quantity",
            "loss_quantity",
            "status",
            "completed_by",
            "completed_at",
            "notes",
        ]
    )

    return run, output_movement, loss_movement


# ============================================================
# COMPLETE SORTING
# ============================================================

@transaction.atomic
def complete_sorting(
    *,
    processing_run,
    good_quantity,
    quaker_quantity,
    user=None,
    notes="",
):
    """
    Sorting produces two retained outputs:

        Good roasted coffee
        Quakers

    Both remain inventory.
    """

    good_quantity = Decimal(good_quantity)
    quaker_quantity = Decimal(quaker_quantity)

    if good_quantity < ZERO:
        raise ValueError(
            "Good coffee quantity cannot be negative."
        )

    if quaker_quantity < ZERO:
        raise ValueError(
            "Quaker quantity cannot be negative."
        )

    run = (
        ProcessingRun.objects
        .select_for_update()
        .select_related("stock")
        .get(pk=processing_run.pk)
    )

    if run.process_type != "sorting":
        raise ValueError(
            "This processing run is not a sorting run."
        )

    if run.status != "open":
        raise ValueError(
            "This sorting run has already been completed."
        )

    accounted = (
        good_quantity
        + quaker_quantity
    )

    if accounted > run.input_quantity:
        raise ValueError(
            "Good coffee plus quakers cannot exceed sorting input."
        )

    loss_quantity = (
        run.input_quantity
        - accounted
    )

    # Good roasted coffee
    if good_quantity > ZERO:

        StockMovement.objects.create(
            stock=run.stock,
            movement_type="sort_good_output",
            from_stage=None,
            to_stage=StockStage.ROASTED,
            quantity=good_quantity,
            reference=f"Sorting #{run.pk}",
            notes=notes,
            created_by=user,
        )

    # Quakers
    if quaker_quantity > ZERO:

        StockMovement.objects.create(
            stock=run.stock,
            movement_type="quaker_output",
            from_stage=None,
            to_stage=StockStage.QUAKERS,
            quantity=quaker_quantity,
            reference=f"Sorting #{run.pk}",
            notes="Retained quakers.",
            created_by=user,
        )

    # Sorting loss, if any
    if loss_quantity > ZERO:

        StockMovement.objects.create(
            stock=run.stock,
            movement_type="loss",
            from_stage=None,
            to_stage=None,
            quantity=loss_quantity,
            reference=f"Sorting loss #{run.pk}",
            notes=notes,
            created_by=user,
        )

    run.output_quantity = good_quantity

    run.secondary_output_stage = (
        StockStage.QUAKERS
    )

    run.secondary_output_quantity = (
        quaker_quantity
    )

    run.loss_quantity = loss_quantity

    run.status = "completed"

    run.completed_by = user
    run.completed_at = timezone.now()

    if notes:
        run.notes = notes

    run.save(
        update_fields=[
            "output_quantity",
            "secondary_output_stage",
            "secondary_output_quantity",
            "loss_quantity",
            "status",
            "completed_by",
            "completed_at",
            "notes",
        ]
    )

    return run


# ============================================================
# COMPLETE GRINDING
# ============================================================

@transaction.atomic
def complete_grinding(
    *,
    processing_run,
    output_quantity,
    user=None,
    notes="",
):
    """
    Receive ground coffee after grinding.
    """

    output_quantity = Decimal(output_quantity)

    if output_quantity <= ZERO:
        raise ValueError(
            "Ground output must be greater than zero."
        )

    run = (
        ProcessingRun.objects
        .select_for_update()
        .select_related("stock")
        .get(pk=processing_run.pk)
    )

    if run.process_type != "grinding":
        raise ValueError(
            "This processing run is not a grinding run."
        )

    if run.status != "open":
        raise ValueError(
            "This grinding run has already been completed."
        )

    if output_quantity > run.input_quantity:
        raise ValueError(
            "Ground output cannot exceed input."
        )

    loss_quantity = (
        run.input_quantity
        - output_quantity
    )

    output_movement = StockMovement.objects.create(
        stock=run.stock,
        movement_type="grind_return",
        from_stage=None,
        to_stage=StockStage.GROUND,
        quantity=output_quantity,
        reference=f"Processing #{run.pk}",
        notes=notes,
        created_by=user,
    )

    if loss_quantity > ZERO:

        StockMovement.objects.create(
            stock=run.stock,
            movement_type="loss",
            from_stage=None,
            to_stage=None,
            quantity=loss_quantity,
            reference=f"Grinding loss #{run.pk}",
            notes=notes,
            created_by=user,
        )

    run.output_quantity = output_quantity
    run.loss_quantity = loss_quantity
    run.status = "completed"
    run.completed_by = user
    run.completed_at = timezone.now()

    if notes:
        run.notes = notes

    run.save(
        update_fields=[
            "output_quantity",
            "loss_quantity",
            "status",
            "completed_by",
            "completed_at",
            "notes",
        ]
    )

    return run, output_movement