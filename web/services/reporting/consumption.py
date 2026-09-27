from collections import defaultdict
from decimal import Decimal

from ...models import (
    InternalStockIssue,
    PackRelease,
    PackReturn,
    ProcessingRun,
    PackagingRun,
)


ZERO = Decimal("0.00")


CATEGORY_LABELS = {
    "sales": "Sales",
    "marketing": "Marketing",
    "staff": "Staff Consumption",
    "hospitality": "Hospitality / Visitors",
    "damage": "Damages / Spoilage",
    "testing": "Product Testing",
    "processing_loss": "Processing Loss",
    "packaging_loss": "Packaging Loss",
    "other": "Other",
}


def _decimal(value):
    if value is None:
        return ZERO

    return Decimal(str(value))


def _add_category(bucket, key, kg):
    kg = _decimal(kg)

    if kg <= 0:
        return

    bucket[key] += kg


def _packaged_sales_consumption(period):
    """
    Returns net packaged coffee sold/released for sale,
    converted from packs into coffee-equivalent kilograms.

    Returns are deducted.
    """

    releases = (
    PackRelease.objects
    .filter(
        request_item__request__purpose="sale",
    )
    .select_related(
        "product",
        "product__blend",
        "product__pack_size",
    )
    .prefetch_related("returns")
)

    releases = period.filter_datetime(
        releases,
        "released_at",
    )

    total_kg = ZERO

    for release in releases:
        returned_packs = sum(
    returned.packs_returned
    for returned in release.returns.all()
)

        net_packs = max(
            release.packs_out - returned_packs,
            0,
        )

        total_kg += (
            _decimal(net_packs)
            * _decimal(release.product.kg_per_pack)
        )

    return total_kg


def _internal_consumption(period):
    """
    Converts all internal stock issues to one comparable
    unit: coffee-equivalent kilograms.

    Bulk:
        quantity_kg

    Packaged:
        packs * product.kg_per_pack
    """

    issues = (
        InternalStockIssue.objects
        .select_related("account")
        .prefetch_related(
            "items__coffee_stock",
            "items__packaged_product__blend",
            "items__packaged_product__pack_size",
        )
    )

    issues = period.filter_date(
        issues,
        "issue_date",
    )

    category_totals = defaultdict(
        lambda: ZERO
    )

    account_totals = defaultdict(
        lambda: {
            "kg": ZERO,
            "items": 0,
        }
    )

    for issue in issues:
        category = (
            issue.account.account_type
            if issue.account
            else "other"
        )

        account_name = (
            issue.account.name
            if issue.account
            else "Other"
        )

        for item in issue.items.all():

            if item.packaged_product_id:
                item_kg = (
                    _decimal(item.packs)
                    * _decimal(
                        item.packaged_product.kg_per_pack
                    )
                )

            else:
                item_kg = _decimal(
                    item.quantity_kg
                )

            category_totals[category] += item_kg

            account_totals[account_name]["kg"] += item_kg
            account_totals[account_name]["items"] += 1

    return category_totals, account_totals


def _processing_losses(period):
    """
    Real completed processing loss.

    We use ProcessingRun.loss_quantity instead of summing
    StockMovement loss rows so the same physical loss is
    not accidentally counted twice.
    """

    runs = ProcessingRun.objects.filter(
        status="completed",
    )

    runs = period.filter_datetime(
        runs,
        "completed_at",
    )

    total = ZERO
    by_process = defaultdict(lambda: ZERO)

    for run in runs:
        loss = _decimal(run.loss_quantity)

        if loss <= 0:
            continue

        total += loss
        by_process[run.process_type] += loss

    return total, by_process


def _packaging_losses(period):
    """
    Coffee lost during completed packaging runs.
    """

    runs = (
        PackagingRun.objects
        .filter(status="completed")
        .select_related(
            "product__blend",
            "product__pack_size",
        )
    )

    runs = period.filter_datetime(
        runs,
        "completed_at",
    )

    total = ZERO

    for run in runs:
        total += _decimal(run.loss_kg)

    return total


def get_stock_consumption_report(period):
    """
    Main management stock-consumption report.

    Everything is converted to coffee-equivalent KG so
    percentages remain mathematically meaningful.
    """

    categories = defaultdict(lambda: ZERO)

    # ------------------------------------------------------
    # 1. PACKAGED SALES
    # ------------------------------------------------------

    sales_kg = _packaged_sales_consumption(period)

    _add_category(
        categories,
        "sales",
        sales_kg,
    )

    # ------------------------------------------------------
    # 2. INTERNAL USAGE
    # ------------------------------------------------------

    internal_categories, account_totals = (
        _internal_consumption(period)
    )

    for key, kg in internal_categories.items():
        _add_category(
            categories,
            key,
            kg,
        )

    # ------------------------------------------------------
    # 3. PROCESSING LOSS
    # ------------------------------------------------------

    processing_loss, processing_by_type = (
        _processing_losses(period)
    )

    _add_category(
        categories,
        "processing_loss",
        processing_loss,
    )

    # ------------------------------------------------------
    # 4. PACKAGING LOSS
    # ------------------------------------------------------

    packaging_loss = _packaging_losses(period)

    _add_category(
        categories,
        "packaging_loss",
        packaging_loss,
    )

    # ------------------------------------------------------
    # TOTAL
    # ------------------------------------------------------

    total_consumed_kg = sum(
        categories.values(),
        ZERO,
    )

    # ------------------------------------------------------
    # CATEGORY BREAKDOWN
    # ------------------------------------------------------

    breakdown = []

    for key, kg in categories.items():

        percentage = (
            (kg / total_consumed_kg) * Decimal("100")
            if total_consumed_kg > 0
            else ZERO
        )

        breakdown.append({
            "key": key,
            "label": CATEGORY_LABELS.get(
                key,
                key.replace("_", " ").title(),
            ),
            "kg": kg.quantize(
                Decimal("0.01")
            ),
            "percentage": percentage.quantize(
                Decimal("0.1")
            ),
        })

    breakdown.sort(
        key=lambda row: row["kg"],
        reverse=True,
    )

    # ------------------------------------------------------
    # INTERNAL ACCOUNT BREAKDOWN
    # ------------------------------------------------------

    internal_accounts = [
        {
            "name": name,
            "kg": values["kg"].quantize(
                Decimal("0.01")
            ),
            "items": values["items"],
        }
        for name, values in account_totals.items()
    ]

    internal_accounts.sort(
        key=lambda row: row["kg"],
        reverse=True,
    )

    # ------------------------------------------------------
    # PROCESSING BREAKDOWN
    # ------------------------------------------------------

    processing_breakdown = [
        {
            "process": key.replace(
                "_",
                " ",
            ).title(),
            "kg": kg.quantize(
                Decimal("0.01")
            ),
        }
        for key, kg in processing_by_type.items()
    ]

    processing_breakdown.sort(
        key=lambda row: row["kg"],
        reverse=True,
    )

    # ------------------------------------------------------
    # CHART DATA
    # ------------------------------------------------------

    chart_labels = [
        row["label"]
        for row in breakdown
    ]

    chart_values = [
        float(row["kg"])
        for row in breakdown
    ]

    return {
        "total_consumed_kg": (
            total_consumed_kg.quantize(
                Decimal("0.01")
            )
        ),

        "sales_kg": sales_kg.quantize(
            Decimal("0.01")
        ),

        "internal_kg": sum(
            internal_categories.values(),
            ZERO,
        ).quantize(
            Decimal("0.01")
        ),

        "processing_loss_kg": (
            processing_loss.quantize(
                Decimal("0.01")
            )
        ),

        "packaging_loss_kg": (
            packaging_loss.quantize(
                Decimal("0.01")
            )
        ),

        "breakdown": breakdown,
        "internal_accounts": internal_accounts,
        "processing_breakdown": processing_breakdown,

        "chart_labels": chart_labels,
        "chart_values": chart_values,
    }