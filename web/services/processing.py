from django.contrib import messages
from django.views import View
from dataclasses import dataclass
from decimal import Decimal
from django.shortcuts import get_object_or_404, redirect

from django.db import transaction
from django.utils import timezone

from ..permissions import RoleRequiredMixin

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

    STAGE_MAP = {
    "roasting": StockStage.ROASTED if hasattr(StockStage, "ROASTED") else "roasted",
    "grinding": StockStage.GROUND if hasattr(StockStage, "GROUND") else "ground",
    "sorting": StockStage.SORTED if hasattr(StockStage, "SORTED") else "sorted",
}

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

from decimal import Decimal
from django.core.exceptions import ValidationError
from ..models import StockMovement

# ============================================================
# COMPLETE ROASTING
# ============================================================

from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils import timezone
from ..models import StockMovement, StockStage

@transaction.atomic
def complete_roasting(processing_run, good_quantity, bad_quantity, user, notes=""):
    """
    Step 2: Completes an active roasting run.
    Deducts from input stage and updates Roasted and Quakers inventory states.
    """
    good_qty = Decimal(good_quantity)
    bad_qty = Decimal(bad_quantity)
    total_output = good_qty + bad_qty

    if total_output > processing_run.input_quantity:
        raise ValidationError(
            f"Output ({total_output} kg) cannot be greater than the input lot weight "
            f"({processing_run.input_quantity} kg)."
        )

    # Automatic Process Loss Calculation (Moisture Loss / Silver-skin chaff)
    process_loss = processing_run.input_quantity - total_output

    # Update processing run status
    processing_run.output_quantity = good_qty
    if hasattr(processing_run, "bad_quantity_sorted"):
        processing_run.bad_quantity_sorted = bad_qty
    processing_run.status = "completed"
    processing_run.completed_at = timezone.now()
    processing_run.save()

    stock = processing_run.stock
    input_stage = processing_run.input_stage  # Uses GREEN or configured input stage

    # 1. Log Good Roasted Output
    StockMovement.objects.create(
        stock=stock,
        from_stage=None,
        to_stage=StockStage.ROASTED,
        quantity=good_qty,
        movement_type="processing_complete",
        created_by=user,
        notes=notes
    )

    # 2. Log Bad Roasted Output (Quakers)
    if bad_qty > 0:
        quakers_stage = getattr(StockStage, "QUAKERS", "quakers")
        StockMovement.objects.create(
            stock=stock,
            from_stage=input_stage,
            to_stage=quakers_stage,
            quantity=bad_qty,
            movement_type="defect_sorting",
            created_by=user,
            notes=f"Sorted bad beans saved for staff consumption. {notes}"
        )

    return processing_run, process_loss


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



class CompleteProcessingRunView(RoleRequiredMixin, View):
    allowed_roles = ("store_manager", "manager", "admin")

    def post(self, request, pk):
        run = get_object_or_404(
            ProcessingRun.objects.select_related("stock"),
            pk=pk,
        )

        try:
            user = request.user
            notes = request.POST.get("notes", "")

            # ========================================================
            # ROASTING
            # ========================================================
            if run.process_type == "roasting":
                output_qty = request.POST.get("output_quantity", "0")

                completed_run, process_loss = complete_roasting(
                    processing_run=run,
                    good_quantity=output_qty,
                    bad_quantity="0",
                    user=user,
                    notes=notes,
                )

                messages.success(
                    request,
                    f"Roasting complete: "
                    f"{completed_run.input_quantity} kg input → "
                    f"{completed_run.output_quantity} kg roasted coffee. "
                    f"{process_loss:.2f} kg process loss.",
                )

            # ========================================================
            # SORTING
            # ========================================================
            elif run.process_type == "sorting":
                good_qty = request.POST.get("output_quantity", "0")
                quaker_qty = request.POST.get("quaker_quantity", "0")

                completed_run = complete_sorting(
                    processing_run=run,
                    good_quantity=good_qty,
                    quaker_quantity=quaker_qty,
                    user=user,
                    notes=notes,
                )

                messages.success(
                    request,
                    f"Sorting complete: "
                    f"{completed_run.input_quantity} kg input → "
                    f"{completed_run.output_quantity} kg good roasted coffee, "
                    f"{completed_run.secondary_output_quantity} kg quakers, "
                    f"{completed_run.loss_quantity} kg loss.",
                )

            # ========================================================
            # GRINDING
            # ========================================================
            elif run.process_type == "grinding":
                output_qty = request.POST.get("output_quantity", "0")

                completed_run, _ = complete_grinding(
                    processing_run=run,
                    output_quantity=output_qty,
                    user=user,
                    notes=notes,
                )

                messages.success(
                    request,
                    f"Grinding complete: "
                    f"produced {completed_run.output_quantity} kg ground coffee.",
                )

            else:
                raise ValueError(
                    f"Unsupported processing type: {run.process_type}"
                )

        except Exception as exc:
            messages.error(request, str(exc))

        return redirect("processing_workspace")