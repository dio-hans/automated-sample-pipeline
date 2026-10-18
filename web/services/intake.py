"""
Stock intake resolution.

The intake form asks for a variety *name* rather than a foreign key, the
same way the Nyondo hardware intake form asks for a material name. Typing
a name that already exists restocks that coffee line; a name that does not
exist yet auto-generates a new variety definition.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from ..models import CoffeeStock, CoffeeVariety
from .inventory import record_receipt


VARIETY_DEFAULT_FIELDS = {
    "default_coffee_type": "coffee_type",
    "default_grade": "grade",
    "default_source": "source",
    "default_process": "process",
    "default_foreign_smell": "foreign_smell",
}

# Batch attributes that identify one physical delivery. An intake matching
# all of them tops up that delivery instead of opening a new batch.
BATCH_IDENTITY_FIELDS = ("supplier", "received_date")


@dataclass
class IntakeResult:
    stock: CoffeeStock
    variety: CoffeeVariety
    variety_created: bool
    batch_created: bool
    quantity_received: object

    @property
    def message(self):
        if self.variety_created:
            return (
                f"Registered new coffee definition "
                f"'{self.variety.name}' and opened batch "
                f"{self.stock.batch_number} with "
                f"{self.quantity_received} kg."
            )

        if self.batch_created:
            return (
                f"Restocked '{self.variety.name}' — opened batch "
                f"{self.stock.batch_number} with "
                f"{self.quantity_received} kg."
            )

        return (
            f"Restocked '{self.variety.name}' — added "
            f"{self.quantity_received} kg to existing batch "
            f"{self.stock.batch_number}."
        )


def resolve_variety(name, batch_data):
    """
    Return (variety, created) for a typed variety name.

    Matching is case-insensitive so 'bugisu aa' tops up 'Bugisu AA'
    instead of creating a near-duplicate definition. A brand new name
    takes its master defaults from the batch being received; an existing
    definition only fills in defaults it is still missing.
    """

    name = name.strip()

    defaults = {
        master: batch_data.get(source) or ""
        for master, source in VARIETY_DEFAULT_FIELDS.items()
    }

    variety = CoffeeVariety.objects.filter(name__iexact=name).first()

    if variety is None:
        return CoffeeVariety.objects.create(name=name, **defaults), True

    updated_fields = []

    if not variety.is_active:
        variety.is_active = True
        updated_fields.append("is_active")

    for field, value in defaults.items():
        if value and not getattr(variety, field):
            setattr(variety, field, value)
            updated_fields.append(field)

    if updated_fields:
        variety.save(update_fields=updated_fields + ["updated_at"])

    return variety, False


def generate_batch_number(variety, received_date=None):
    received_date = received_date or timezone.localdate()

    prefix = (slugify(variety.name).replace("-", "")[:6] or "batch").upper()
    stamp = received_date.strftime("%Y%m%d")

    sequence = CoffeeStock.objects.filter(
        batch_number__startswith=f"{prefix}-{stamp}"
    ).count() + 1

    candidate = f"{prefix}-{stamp}-{sequence:03d}"

    while CoffeeStock.objects.filter(batch_number=candidate).exists():
        sequence += 1
        candidate = f"{prefix}-{stamp}-{sequence:03d}"

    return candidate

def generate_roast_batch_number(roast_date=None):
    roast_date = roast_date or timezone.localdate()
    prefix = roast_date.strftime("%y")

    existing_numbers = (
        CoffeeStock.objects
        .filter(batch_number__startswith=prefix)
        .values_list("batch_number", flat=True)
    )

    used_sequences = [
        int(number[2:])
        for number in existing_numbers
        if number[2:].isdigit()
    ]

    sequence = max(used_sequences, default=0) + 1
    candidate = f"{prefix}{sequence:02d}"

    while CoffeeStock.objects.filter(
        batch_number=candidate
    ).exists():
        sequence += 1
        candidate = f"{prefix}{sequence:02d}"

    return candidate

def find_open_batch(variety, batch_data):
    supplier = (
        batch_data.get("supplier") or ""
    ).strip()

    received_date = batch_data.get("received_date")

    # Without both identifiers, don't assume this is
    # the same physical delivery as an existing batch.
    if not supplier or not received_date:
        return None

    return (
        CoffeeStock.objects
        .filter(
            variety=variety,
            supplier__iexact=supplier,
            received_date=received_date,
        )
        .order_by("-created_at")
        .first()
    )


@transaction.atomic
def record_intake(variety_name, batch_data, quantity_received, user=None):
    batch_data = dict(batch_data)

    received_date = batch_data.get("received_date")
    batch_number_date = (
        received_date
        if isinstance(received_date, date)
        else None
    )

    received_qty = Decimal(str(quantity_received or "0.00"))
    defects_qty = Decimal(str(batch_data.get("defects") or "0.00"))

    if received_qty <= 0:
        raise ValidationError(
            "Quantity received must be greater than zero."
        )

    if defects_qty < 0:
        raise ValidationError("Defects cannot be negative.")

    if defects_qty > received_qty:
        raise ValidationError(
            "Defects cannot exceed the quantity received."
        )

    # Net usable green coffee after defects are removed.
    usable_qty = received_qty - defects_qty

    batch_data["defects"] = defects_qty
    batch_data["quantity_sorted_out"] = defects_qty
    batch_data["quantity_after_sorting"] = usable_qty

    variety, variety_created = resolve_variety(
        variety_name,
        batch_data,
    )

    stock = (
        None
        if variety_created
        else find_open_batch(variety, batch_data)
    )

    batch_created = stock is None

    if batch_created:
        stock = CoffeeStock.objects.create(
            variety=variety,
            batch_number=generate_batch_number(
                variety,
                batch_number_date,
            ),
            **batch_data,
        )

    else:
        # Accumulate the new intake's figures on the existing batch.
        stock.defects = (
            stock.defects or Decimal("0.00")
        ) + defects_qty

        stock.quantity_sorted_out = (
            stock.quantity_sorted_out or Decimal("0.00")
        ) + defects_qty

        stock.quantity_after_sorting = (
            stock.quantity_after_sorting or Decimal("0.00")
        ) + usable_qty

        update_fields = [
            "defects",
            "quantity_sorted_out",
            "quantity_after_sorting",
            "updated_at",
        ]

        if "reorder_level" in batch_data:
            stock.reorder_level = batch_data["reorder_level"]
            update_fields.append("reorder_level")

        stock.save(update_fields=update_fields)

    # Record the full physical delivery first.
    record_receipt(
        stock,
        received_qty,
        user=user,
        reference=stock.batch_number,
    )

    # Then deduct defects from available green inventory.
    if defects_qty > 0:
        StockMovement.objects.create(
            stock=stock,
            movement_type="loss",
            from_stage=StockStage.GREEN,
            to_stage=None,
            quantity=defects_qty,
            reference=f"Intake defects - {stock.batch_number}",
            notes=(
                f"{defects_qty} kg of defects removed at intake "
                f"from {received_qty} kg received."
            ),
            created_by=user,
        )

    return IntakeResult(
        stock=stock,
        variety=variety,
        variety_created=variety_created,
        batch_created=batch_created,
        quantity_received=received_qty,
    )