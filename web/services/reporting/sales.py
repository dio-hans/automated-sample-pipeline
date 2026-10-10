from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum

from ...models import (
    ConsignmentAuditAllocation,
    PackSettlement,
    RoastedSackSale,
)


ZERO = Decimal("0.00")


def _decimal(value):
    if value is None:
        return ZERO
    return Decimal(str(value))


def get_sales_report(period):
    # ============================================================
    # 1. PACKAGED SALES
    # ============================================================

    settlements = (
        PackSettlement.objects
        .select_related(
            "release",
            "release__product",
            "release__product__blend",
            "release__product__pack_size",
            "release__released_to",
        )
        .order_by("cleared_at")
    )

    settlements = period.filter_datetime(
        settlements,
        "cleared_at",
    )

    packaged_packs_sold = 0
    packaged_sales_value = ZERO
    packaged_kg_sold = ZERO

    product_stats = defaultdict(
        lambda: {
            "product": None,
            "packs": 0,
            "kg": ZERO,
            "revenue": ZERO,
        }
    )

    seller_stats = defaultdict(
        lambda: {
            "name": "",
            "packs": 0,
            "revenue": ZERO,
        }
    )

    # Used for line graph
    daily_sales = defaultdict(lambda: ZERO)

    for settlement in settlements:
        release = settlement.release
        product = release.product

        packs_sold = settlement.packs_sold or 0

        if packs_sold <= 0:
            continue

        # Revenue belonging to the packs confirmed sold.
        sale_value = (
            _decimal(packs_sold)
            * _decimal(release.selling_price)
        )

        kg_sold = (
            _decimal(packs_sold)
            * _decimal(product.kg_per_pack)
        )

        packaged_packs_sold += packs_sold
        packaged_sales_value += sale_value
        packaged_kg_sold += kg_sold

        # --------------------------------------------------------
        # PRODUCT PERFORMANCE
        # --------------------------------------------------------

        row = product_stats[product.pk]

        row["product"] = product
        row["packs"] += packs_sold
        row["kg"] += kg_sold
        row["revenue"] += sale_value

        # --------------------------------------------------------
        # SALESPERSON / ACCOUNT HOLDER PERFORMANCE
        # --------------------------------------------------------

        holder = release.released_to

        holder_key = (
            holder.pk
            if holder
            else "unassigned"
        )

        seller = seller_stats[holder_key]

        seller["name"] = (
            holder.name
            if holder
            else "Unassigned"
        )

        seller["packs"] += packs_sold
        seller["revenue"] += sale_value

        # --------------------------------------------------------
        # SALES TREND
        # --------------------------------------------------------

        sale_date = settlement.cleared_at.date()

        daily_sales[sale_date] += sale_value

    # Branch-audited supermarket sales are confirmed by an approved audit,
    # not by PackSettlement. Use the allocation's stored unit price so that
    # later delivery price changes cannot rewrite historical sales.
    audit_allocations = (
        ConsignmentAuditAllocation.objects
        .filter(audit_item__audit__status="approved")
        .select_related(
            "audit_item__audit",
            "audit_item__product",
            "release__released_to",
        )
        .order_by("audit_item__audit__approved_at")
    )
    audit_allocations = period.filter_datetime(
        audit_allocations,
        "audit_item__audit__approved_at",
    )

    for allocation in audit_allocations:
        packs_sold = allocation.quantity
        product = allocation.audit_item.product
        release = allocation.release
        sale_value = (
            _decimal(packs_sold)
            * _decimal(allocation.unit_price)
        )
        kg_sold = (
            _decimal(packs_sold)
            * _decimal(product.kg_per_pack)
        )

        packaged_packs_sold += packs_sold
        packaged_sales_value += sale_value
        packaged_kg_sold += kg_sold

        row = product_stats[product.pk]
        row["product"] = product
        row["packs"] += packs_sold
        row["kg"] += kg_sold
        row["revenue"] += sale_value

        holder = release.released_to
        holder_key = holder.pk if holder else "unassigned"
        seller = seller_stats[holder_key]
        seller["name"] = holder.name if holder else "Unassigned"
        seller["packs"] += packs_sold
        seller["revenue"] += sale_value

        sale_date = allocation.audit_item.audit.approved_at.date()
        daily_sales[sale_date] += sale_value

    # ============================================================
    # 2. BULK ROASTED SALES
    # ============================================================

    bulk_sales = (
        RoastedSackSale.objects
        .select_related(
            "stock",
            "stock__variety",
            "created_by",
        )
        .order_by("sale_date")
    )

    bulk_sales = period.filter_datetime(
        bulk_sales,
        "sale_date",
    )

    bulk_kg_sold = ZERO
    bulk_sales_value = ZERO
    bulk_sale_count = 0

    bulk_buyers = defaultdict(
        lambda: {
            "name": "",
            "kg": ZERO,
            "revenue": ZERO,
        }
    )

    for sale in bulk_sales:
        quantity = _decimal(sale.kg_sold)

        value = (
            quantity
            * _decimal(sale.price_per_kg)
        )

        bulk_sale_count += 1
        bulk_kg_sold += quantity
        bulk_sales_value += value

        buyer_name = (
            sale.buyer_name.strip()
            if sale.buyer_name
            else "Unknown Buyer"
        )

        buyer = bulk_buyers[
            buyer_name.lower()
        ]

        buyer["name"] = buyer_name
        buyer["kg"] += quantity
        buyer["revenue"] += value

        sale_date = sale.sale_date.date()

        daily_sales[sale_date] += value

    # ============================================================
    # 3. TOTAL SALES
    # ============================================================

    total_sales_value = (
        packaged_sales_value
        + bulk_sales_value
    )

    total_coffee_sold_kg = (
        packaged_kg_sold
        + bulk_kg_sold
    )

    # ============================================================
    # 4. PRODUCT RANKING
    # ============================================================

    products = list(product_stats.values())

    products.sort(
        key=lambda row: (
            row["revenue"],
            row["packs"],
        ),
        reverse=True,
    )

    for row in products:
        row["kg"] = row["kg"].quantize(
            Decimal("0.01")
        )

        row["revenue"] = row["revenue"].quantize(
            Decimal("0.01")
        )

        row["revenue_share"] = (
            (
                row["revenue"]
                / packaged_sales_value
            )
            * Decimal("100")
            if packaged_sales_value > 0
            else ZERO
        ).quantize(
            Decimal("0.1")
        )

    top_products = products[:10]

    slow_products = sorted(
        products,
        key=lambda row: (
            row["packs"],
            row["revenue"],
        ),
    )[:10]

    # ============================================================
    # 5. SALESPERSON RANKING
    # ============================================================

    salespeople = list(
        seller_stats.values()
    )

    salespeople.sort(
        key=lambda row: row["revenue"],
        reverse=True,
    )

    # ============================================================
    # 6. BULK BUYER RANKING
    # ============================================================

    bulk_buyer_rows = list(
        bulk_buyers.values()
    )

    bulk_buyer_rows.sort(
        key=lambda row: row["revenue"],
        reverse=True,
    )

    # ============================================================
    # 7. SALES TREND
    # ============================================================

    trend_rows = []

    if period.start_date and period.end_date:
        current = period.start_date

        while current <= period.end_date:
            trend_rows.append({
                "date": current,
                "label": current.strftime(
                    "%d %b"
                ),
                "revenue": daily_sales[
                    current
                ].quantize(
                    Decimal("0.01")
                ),
            })

            current += timedelta(days=1)

    else:
        # Overall report:
        # aggregate existing sales by month.
        monthly_sales = defaultdict(
            lambda: ZERO
        )

        for date_value, amount in daily_sales.items():
            month_key = date_value.replace(
                day=1
            )

            monthly_sales[month_key] += amount

        for month_key in sorted(
            monthly_sales.keys()
        ):
            trend_rows.append({
                "date": month_key,
                "label": month_key.strftime(
                    "%b %Y"
                ),
                "revenue": monthly_sales[
                    month_key
                ].quantize(
                    Decimal("0.01")
                ),
            })

    # ============================================================
    # 8. CHANNEL BREAKDOWN
    # ============================================================

    channels = []

    if packaged_sales_value > 0:
        channels.append({
            "label": "Packaged Coffee",
            "value": packaged_sales_value,
            "percentage": (
                packaged_sales_value
                / total_sales_value
                * Decimal("100")
            ).quantize(
                Decimal("0.1")
            ),
        })

    if bulk_sales_value > 0:
        channels.append({
            "label": "Bulk Roasted Coffee",
            "value": bulk_sales_value,
            "percentage": (
                bulk_sales_value
                / total_sales_value
                * Decimal("100")
            ).quantize(
                Decimal("0.1")
            ),
        })

    channels.sort(
        key=lambda row: row["value"],
        reverse=True,
    )

    # ============================================================
    # 9. RETURN CONTEXT
    # ============================================================

    return {
        "total_sales_value": (
            total_sales_value.quantize(
                Decimal("0.01")
            )
        ),

        "packaged_sales_value": (
            packaged_sales_value.quantize(
                Decimal("0.01")
            )
        ),

        "bulk_sales_value": (
            bulk_sales_value.quantize(
                Decimal("0.01")
            )
        ),

        "packaged_packs_sold": (
            packaged_packs_sold
        ),

        "packaged_kg_sold": (
            packaged_kg_sold.quantize(
                Decimal("0.01")
            )
        ),

        "bulk_kg_sold": (
            bulk_kg_sold.quantize(
                Decimal("0.01")
            )
        ),

        "total_coffee_sold_kg": (
            total_coffee_sold_kg.quantize(
                Decimal("0.01")
            )
        ),

        "bulk_sale_count": bulk_sale_count,

        "top_products": top_products,
        "slow_products": slow_products,

        "salespeople": salespeople[:10],
        "bulk_buyers": bulk_buyer_rows[:10],

        "sales_channels": channels,

        "trend_rows": trend_rows,

        "trend_labels": [
            row["label"]
            for row in trend_rows
        ],

        "trend_values": [
            float(row["revenue"])
            for row in trend_rows
        ],

        "channel_labels": [
            row["label"]
            for row in channels
        ],

        "channel_values": [
            float(row["value"])
            for row in channels
        ],
    }