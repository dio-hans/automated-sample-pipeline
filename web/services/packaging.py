from collections import defaultdict
from decimal import Decimal, InvalidOperation
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
    PackagingRunInput,
    PackagingRunOutput,
    StockMovement,
)
from .inventory import get_stage_inventory

ZERO = Decimal("0.00")

@transaction.atomic
def start_packaging_run(*, inputs, source_stage, user=None, notes=""):
    """Start an open packaging run and issue coffee from multiple source batches.
    `inputs` is an iterable of (CoffeeStock instance, kg quantity) pairs.
    The source stage is shared by all inputs in this run.
    """
    if source_stage not in ("roasted", "ground"):
        raise ValidationError("Choose either roasted or ground coffee as the source stage.")
    
    quantities_by_stock = defaultdict(lambda: ZERO)
    for stock, raw_quantity in inputs:
        try:
            quantity = Decimal(str(raw_quantity))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError("Every batch must have a valid kilogram quantity.")
        if quantity <= ZERO:
            raise ValidationError("Each batch quantity must be greater than zero.")
        if quantity != quantity.quantize(Decimal("0.01")):
            raise ValidationError("Batch quantities can have at most two decimal places.")
        quantities_by_stock[stock.pk] += quantity

    if not quantities_by_stock:
        raise ValidationError("Add at least one coffee batch to the run.")

    # Lock in a stable order to reduce the chance of competing run deadlocks.
    locked_stocks = {
        stock.pk: stock
        for stock in CoffeeStock.objects.select_for_update()
        .filter(pk__in=quantities_by_stock.keys())
        .order_by("pk")
    }
    if len(locked_stocks) != len(quantities_by_stock):
        raise ValidationError("One of the selected coffee batches no longer exists.")

    total_input = ZERO
    for stock_id, quantity in quantities_by_stock.items():
        stock = locked_stocks[stock_id]
        available = Decimal(str(get_stage_inventory(stock, source_stage)))
        if quantity > available:
            raise ValidationError(
                f"Batch {stock.batch_number} has {available} kg at {source_stage}; "
                f"the run requires {quantity} kg."
            )
        total_input += quantity

    first_stock = locked_stocks[next(iter(quantities_by_stock))]
    
    # 🎯 CHANGED: Removed packs_produced=None to prevent property setter conflicts
    run = PackagingRun.objects.create(
        stock=first_stock,                    # legacy/summary anchor
        product=None,                         # products are recorded when completed
        source_stage=source_stage,
        input_kg=total_input,
        coffee_used_kg=ZERO,
        loss_kg=ZERO,
        status="open",
        issued_by=user,
        notes=notes,
    )

    for stock_id, quantity in quantities_by_stock.items():
        stock = locked_stocks[stock_id]
        PackagingRunInput.objects.create(
            run=run,
            stock=stock,
            input_kg=quantity,
        )
        StockMovement.objects.create(
            stock=stock,
            movement_type="packaging_issue",
            from_stage=source_stage,
            to_stage=None,
            quantity=quantity,
            reference=f"PACK-{run.pk}",
            notes=notes or f"Coffee issued to packaging run #{run.pk}.",
            created_by=user,
        )
    return run

@transaction.atomic
def complete_packaging_run(*, packaging_run, outputs, user=None, notes=""):
    """Complete an open run using one or more (product, packs) output rows."""
    run = (
        PackagingRun.objects.select_for_update()
        .select_related("stock")
        .get(pk=packaging_run.pk)
    )
    if run.status != "open":
        raise ValidationError("This packaging run is not in progress; it cannot be completed again.")
    
    counts_by_product = defaultdict(int)
    for product, raw_count in outputs:
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            raise ValidationError("Every output row must have a valid whole-number pack count.")
        if count <= 0:
            raise ValidationError("Each output row must contain at least one pack.")
        counts_by_product[product.pk] += count

    if not counts_by_product:
        raise ValidationError("Add at least one finished product and pack quantity.")

    locked_products = {
        product.pk: product
        for product in PackagedProduct.objects.select_for_update()
        .filter(pk__in=counts_by_product.keys())
        .select_related("blend", "pack_size")
        .order_by("pk")
    }

    if len(locked_products) != len(counts_by_product):
        raise ValidationError("One of the selected packaged products no longer exists.")
    
    products = list(locked_products.values())
    if any(not product.is_active for product in products):
        raise ValidationError("Inactive packaged products cannot be produced.")

    expected_form = "beans" if run.source_stage == "roasted" else "ground"
    if any(product.form != expected_form for product in products):
        raise ValidationError(
            f"This run uses {run.source_stage} coffee, so all output products must be {expected_form}."
        )

    blend_ids = {product.blend_id for product in products}
    form_values = {product.form for product in products}
    if len(blend_ids) != 1 or len(form_values) != 1:
        raise ValidationError(
            "A single packaging run must produce one blend and one form, across different pack sizes. "
            "Start a separate run for another blend or form."
        )

    total_packs = 0
    represented_kg = ZERO
    has_spiced_blend = False

    for product_id, count in counts_by_product.items():
        product = locked_products[product_id]
        if product.is_spiced_blend:
            has_spiced_blend = True

        kg_per_pack = Decimal(str(product.kg_per_pack or ZERO))
        if kg_per_pack <= ZERO:
            raise ValidationError(f"{product} has no valid kilogram weight per pack configured.")
        total_packs += count
        represented_kg += kg_per_pack * count

    total_input = Decimal(str(run.input_kg))

    # 🎯 INTEL-SAFE VALIDATION: Allow weight gain if any product in the run is a spiced blend
    if represented_kg > total_input:
        if not has_spiced_blend:
            raise ValidationError(
                f"The entered packs represent {represented_kg} kg, but the run only received "
                f"{total_input} kg. Reduce the pack quantities before completing."
            )
        loss_kg = ZERO  # Spiced blend additive weight gain is accepted silently
    else:
        loss_kg = total_input - represented_kg

    for product_id, count in counts_by_product.items():
        PackagingRunOutput.objects.create(
            run=run,
            product=locked_products[product_id],
            packs_produced=count,
        )

    now = timezone.now()
    completion_note = (notes or "").strip()
    if completion_note:
        run.notes = "\n".join(part for part in (run.notes.strip(), f"Completion: {completion_note}") if part)
    
    run.product = locked_products[next(iter(counts_by_product))]  # legacy/summary anchor
    run.coffee_used_kg = represented_kg
    run.loss_kg = loss_kg
    run.status = "completed"
    run.completed_by = user
    run.completed_at = now
    run.save(update_fields=[
        "product", "coffee_used_kg", "loss_kg", "status",
        "completed_by", "completed_at", "notes",
    ])

    if loss_kg > ZERO:
        StockMovement.objects.create(
            stock=run.stock,
            movement_type="loss",
            from_stage=None,
            to_stage=None,
            quantity=loss_kg,
            reference=f"PACK-LOSS-{run.pk}",
            notes=(
                f"Run-wide packaging loss across {run.inputs.count()} source batch(es): "
                f"{total_input} kg issued, {represented_kg} kg packed."
            ),
            created_by=user,
        )

    for product_id in counts_by_product:
        PackagedInventory.objects.get_or_create(product=locked_products[product_id])
    
    return run

@transaction.atomic
def execute_packaging_run(
    *,
    stock: CoffeeStock,
    product: PackagedProduct,
    source_stage: str,
    input_kg: Decimal,
    packs_produced: int,
    user=None,
    notes: str = "",
) -> PackagingRun:
    """Backward-compatible helper for a completed single-product run."""
    input_kg = Decimal(str(input_kg))
    packs_produced = int(packs_produced)
    if input_kg <= ZERO:
        raise ValidationError("Packaging input must be greater than zero.")
    if packs_produced <= 0:
        raise ValidationError("At least one pack must be produced.")
    if not product.is_active:
        raise ValidationError("The selected packaged product is inactive.")
    expected_stage = "roasted" if product.form == "beans" else "ground"
    if source_stage != expected_stage:
        raise ValidationError(
            f"{product.get_form_display()} coffee must be packaged "
            f"from {expected_stage} stock."
        )
    product = PackagedProduct.objects.get(pk=product.pk)
    represented_kg = Decimal(str(product.kg_per_pack)) * packs_produced
    if represented_kg > input_kg:
        raise ValidationError(
            f"{packs_produced} packs require {represented_kg} kg, "
            f"but only {input_kg} kg was issued."
        )
    run = start_packaging_run(
        inputs=[(stock, input_kg)],
        source_stage=source_stage,
        user=user,
        notes=notes,
    )
    complete_packaging_run(
        packaging_run=run,
                        outputs=[(product, packs_produced)],
        user=user,
    )
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
def execute_pack_return(
    *,
    release: PackRelease,
    packs_returned: int,
    user=None,
    condition: str = "good",
    disposition: str = "accepted",
    reason: str = "",
    notes: str = "",
    **kwargs,
) -> PackReturn:
    """Receive physical packs back into store stock."""
    release = PackRelease.objects.select_for_update().get(pk=release.pk)
    packs_returned = int(packs_returned)
    if packs_returned <=0:
        raise ValidationError("Must return at least one pack.")
    if packs_returned > release.packs_outstanding:
        raise ValidationError(
            f"Only {release.packs_outstanding} packs are outstanding on this release."
        )
    returned = PackReturn.objects.create(
        release=release,
        packs_returned=packs_returned,
        condition=condition,
        disposition=disposition,
        reason=reason,
        notes=notes,
        received_by=user,
    )
    release.status = (
        "fully_returned" if release.packs_outstanding == 0 else "partially_returned"
    )
    release.save(update_fields=["status", "updated_at"])
    return returned