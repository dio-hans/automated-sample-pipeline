from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from ..models import (
    CoffeeStock,
    InternalAccount,
    InternalStockIssue,
    InternalStockIssueItem,
    PackagedInventory,
    PackagedProduct,
    StockMovement,
    StockStage,
)

from .inventory import get_stage_inventory


ZERO = Decimal("0.00")


# ============================================================
# NON-PACKAGED STOCK
# ============================================================

def get_internal_stage_available(stock, stage):
    """
    Return the amount of non-packaged coffee currently available
    at the selected stage.

    This uses the existing Nonda inventory ledger.
    """

    return get_stage_inventory(
        stock,
        stage,
    )


def validate_non_packaged_stock(
    *,
    stock,
    stage,
    quantity,
):
    """
    Validate a green, roasted, or ground coffee issue.
    """

    if stage not in (
        StockStage.GREEN,
        StockStage.ROASTED,
        StockStage.GROUND,
    ):
        raise ValidationError(
            "Only green, roasted, or ground coffee "
            "can be issued as internal stock."
        )

    try:
        quantity = Decimal(str(quantity))
    except Exception:
        raise ValidationError(
            "Enter a valid quantity in kilograms."
        )

    if quantity <= ZERO:
        raise ValidationError(
            "Quantity must be greater than zero."
        )

    available = get_internal_stage_available(
        stock,
        stage,
    )

    if quantity > available:
        stage_label = dict(
            StockStage.choices
        ).get(stage, stage)

        raise ValidationError(
            f"Insufficient {stage_label.lower()} stock. "
            f"{stock.batch_number} has only "
            f"{available} kg available."
        )

    return quantity


# ============================================================
# PACKAGED STOCK
# ============================================================

def get_internal_pack_issues(product):
    """
    Total packs of this product that have already been
    consumed through internal stock accounts.
    """

    return (
        InternalStockIssueItem.objects
        .filter(
            packaged_product=product,
            packs__isnull=False,
        )
        .aggregate(
            total=Sum("packs")
        )["total"]
        or 0
    )


from django.core.exceptions import ObjectDoesNotExist, ValidationError


def get_packaged_internal_available(product):
    """
    Calculate packaged coffee currently available for internal issue.
    Returns 0 if no inventory record exists yet for the product.
    """
    try:
        inventory = product.inventory
    except ObjectDoesNotExist:
        # If no inventory record exists yet, return 0 available packs
        # instead of throwing a ValidationError that crashes GET page renders.
        return 0

    internal_issues = get_internal_pack_issues(product)

    # Option A: If using ledger components
    available = (
        inventory.packs_produced
        - inventory.packs_released
        + inventory.packs_returned
        - internal_issues
    )

    # Option B: If PackagedInventory already stores a live stock field, uncomment below:
    # available = inventory.packs_available - internal_issues

    return max(available, 0)


def validate_packaged_stock(*, product, packs):
    """
    Validate a packaged coffee issue quantity during POST submission.
    """
    try:
        packs = int(packs)
    except (TypeError, ValueError):
        raise ValidationError("Pack quantity must be a whole number.")

    if packs <= 0:
        raise ValidationError("Pack quantity must be greater than zero.")

    available = get_packaged_internal_available(product)

    if packs > available:
        raise ValidationError(
            f"Insufficient packaged stock. {product} has only {available} packs available."
        )

    return packs

# ============================================================
# STOCK MOVEMENT TYPE
# ============================================================

def get_internal_movement_type(account):
    """
    Damaged/spoiled stock is recorded as a loss.

    Other internal uses are recorded as dispatches.

    This keeps the existing StockMovement model intact for now.
    """

    if account.account_type == "damage":
        return "loss"

    return "dispatch"


# ============================================================
# CREATE INTERNAL STOCK ISSUE
# ============================================================

@transaction.atomic
def create_internal_stock_issue(
    *,
    account,
    issue_date,
    reason="",
    notes="",
    user=None,
    items=None,
):
    """
    Create one internal stock issue.

    Example items:

    Non-packaged:

        {
            "type": "non_packaged",
            "coffee_stock_id": 12,
            "stock_stage": StockStage.ROASTED,
            "quantity_kg": "5.00",
            "unit_cost": "28000",
        }

    Packaged:

        {
            "type": "packaged",
            "packaged_product_id": 7,
            "packs": 20,
            "unit_cost": "12000",
        }

    The function:

        1. validates the internal account
        2. validates every stock item
        3. creates the InternalStockIssue
        4. creates each InternalStockIssueItem
        5. creates the necessary StockMovement for
           non-packaged coffee
        6. returns the completed issue
    """

    # --------------------------------------------------------
    # ACCOUNT VALIDATION
    # --------------------------------------------------------

    if account is None:
        raise ValidationError(
            "An internal account is required."
        )

    if not account.is_active:
        raise ValidationError(
            f"The internal account "
            f"'{account.name}' is inactive."
        )

    # --------------------------------------------------------
    # ITEM VALIDATION
    # --------------------------------------------------------

    if not items:
        raise ValidationError(
            "At least one stock item is required."
        )

    # --------------------------------------------------------
    # CREATE ISSUE HEADER
    # --------------------------------------------------------

    issue = InternalStockIssue.objects.create(
        account=account,
        issue_date=issue_date,
        issued_by=user,
        reason=reason,
        notes=notes,
    )

    created_items = []

    # --------------------------------------------------------
    # PROCESS EACH ITEM
    # --------------------------------------------------------

    for item_data in items:

        item_type = item_data.get("type")

        # ====================================================
        # NON-PACKAGED COFFEE
        # ====================================================

        if item_type == "non_packaged":

            coffee_stock_id = item_data.get(
                "coffee_stock_id"
            )

            stock_stage = item_data.get(
                "stock_stage"
            )

            quantity_kg = item_data.get(
                "quantity_kg"
            )

            unit_cost = item_data.get(
                "unit_cost"
            )

            if not coffee_stock_id:
                raise ValidationError(
                    "Select a coffee batch."
                )

            if not stock_stage:
                raise ValidationError(
                    "Select the coffee stage."
                )

            if quantity_kg in (
                None,
                "",
            ):
                raise ValidationError(
                    "Enter the quantity in kilograms."
                )

            # Lock this stock row while we check and issue it.
            stock = (
                CoffeeStock.objects
                .select_for_update()
                .select_related("variety")
                .get(
                    pk=coffee_stock_id
                )
            )

            quantity_kg = validate_non_packaged_stock(
                stock=stock,
                stage=stock_stage,
                quantity=quantity_kg,
            )

            # ------------------------------------------------
            # SAVE INTERNAL ISSUE ITEM
            # ------------------------------------------------

            issue_item = InternalStockIssueItem(
                issue=issue,
                coffee_stock=stock,
                stock_stage=stock_stage,
                quantity_kg=quantity_kg,
                unit_cost=(
                    Decimal(str(unit_cost))
                    if unit_cost not in (None, "")
                    else None
                ),
            )

            issue_item.full_clean()
            issue_item.save()

            # ------------------------------------------------
            # REMOVE FROM EXISTING KG INVENTORY
            # ------------------------------------------------

            movement_type = get_internal_movement_type(
                account
            )

            StockMovement.objects.create(
                stock=stock,

                movement_type=movement_type,

                from_stage=stock_stage,

                to_stage=None,

                quantity=quantity_kg,

                reference=(
                    f"Internal Stock #{issue.pk}"
                ),

                notes=(
                    reason
                    or f"Internal use: {account.name}"
                ),

                created_by=user,
            )

            created_items.append(
                issue_item
            )

        # ====================================================
        # PACKAGED COFFEE
        # ====================================================

        elif item_type == "packaged":

            packaged_product_id = item_data.get(
                "packaged_product_id"
            )

            packs = item_data.get(
                "packs"
            )

            unit_cost = item_data.get(
                "unit_cost"
            )

            if not packaged_product_id:
                raise ValidationError(
                    "Select a packaged product."
                )

            if packs in (
                None,
                "",
            ):
                raise ValidationError(
                    "Enter the number of packs."
                )

            # Lock the packaged product.
            product = (
                PackagedProduct.objects
                .select_for_update()
                .select_related(
                    "blend",
                    "pack_size",
                )
                .get(
                    pk=packaged_product_id
                )
            )

            # Lock the inventory row.
            inventory = (
                PackagedInventory.objects
                .select_for_update()
                .filter(
                    product=product
                )
                .first()
            )

            if inventory is None:
                raise ValidationError(
                    f"No packaged inventory exists "
                    f"for {product}."
                )

            packs = validate_packaged_stock(
                product=product,
                packs=packs,
            )

            # ------------------------------------------------
            # SAVE INTERNAL ISSUE ITEM
            # ------------------------------------------------

            issue_item = InternalStockIssueItem(
                issue=issue,
                packaged_product=product,
                packs=packs,
                unit_cost=(
                    Decimal(str(unit_cost))
                    if unit_cost not in (None, "")
                    else None
                ),
            )

            issue_item.full_clean()
            issue_item.save()

            created_items.append(
                issue_item
            )

        # ====================================================
        # INVALID TYPE
        # ====================================================

        else:

            raise ValidationError(
                "Invalid internal stock item type."
            )

    return issue