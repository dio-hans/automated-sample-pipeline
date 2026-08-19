"""
Stock intake resolution.

The intake form asks for a variety *name* rather than a foreign key, the
same way the Nyondo hardware intake form asks for a material name. Typing
a name that already exists restocks that coffee line; a name that does not
exist yet auto-generates a new variety definition.
"""

from dataclasses import dataclass
from datetime import date

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


def find_open_batch(variety, batch_data):
    """
    Find the batch this intake belongs to: same variety, same supplier,
    same receiving date. Anything else is a physically different delivery
    and must stay a separate, traceable batch.
    """

    lookup = {"variety": variety}

    for field in BATCH_IDENTITY_FIELDS:
        lookup[field] = batch_data.get(field) or None

    if lookup["received_date"] is None:
        return None

    return CoffeeStock.objects.filter(**lookup).order_by("-created_at").first()


@transaction.atomic
def record_intake(variety_name, batch_data, quantity_received, user=None):
    """
    Receive coffee against a typed variety name and return an IntakeResult.
    """

    batch_data = dict(batch_data)
    received_date = batch_data.get("received_date")

    if isinstance(received_date, date):
        batch_number_date = received_date
    else:
        batch_number_date = None

    variety, variety_created = resolve_variety(variety_name, batch_data)

    stock = None if variety_created else find_open_batch(variety, batch_data)
    batch_created = stock is None

    if batch_created:
        stock = CoffeeStock.objects.create(
            variety=variety,
            batch_number=generate_batch_number(variety, batch_number_date),
            **batch_data,
        )
    else:
        stock.reorder_level = batch_data.get(
            "reorder_level",
            stock.reorder_level,
        )
        stock.save(update_fields=["reorder_level", "updated_at"])

    record_receipt(
        stock,
        quantity_received,
        user=user,
        reference=stock.batch_number,
    )

    return IntakeResult(
        stock=stock,
        variety=variety,
        variety_created=variety_created,
        batch_created=batch_created,
        quantity_received=quantity_received,
    )
