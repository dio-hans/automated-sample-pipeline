from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from django.db.models import Sum, Q, F
from django.utils import timezone
from ...models import (
    ConsignmentAuditAllocation,
    ConsignmentInventory,
    PackRelease,
    PackSettlement,
    CoffeeBatchSale,
    RoastedSackSale,
    StockAuditItem,
    Company,
)

ZERO = Decimal("0.00")

def _decimal(value):
    if value is None:
        return ZERO
    return Decimal(str(value))

def get_sales_report(period):
    # ============================================================
    # 1. PACKAGED SALES (DIRECT ACCOUNT HOLDERS)
    # ============================================================
    settlements = (
        PackSettlement.objects
        .filter(release__isnull=False)
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
    
    # Avoid counting supermarket display settlements twice.
    # Approved supermarket audits will supply those confirmed sales instead.
    settlements = settlements.exclude(
        release__purpose="display",
        release__branch__isnull=False,
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
    
    daily_sales = defaultdict(lambda: ZERO)
    
    for settlement in settlements:
        release = settlement.release
        product = release.product
        packs_sold = settlement.packs_sold or 0
        if packs_sold <= 0:
            continue
            
        sale_value = _decimal(packs_sold) * _decimal(release.selling_price)
        kg_sold = _decimal(packs_sold) * _decimal(product.kg_per_pack)
        
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
        
        sale_date = settlement.cleared_at.date()
        daily_sales[sale_date] += sale_value

    # ============================================================
    # 2. CONFIRMED SUPERMARKET CONSIGNMENT SALES
    # ============================================================
    consignment_audits = (
        StockAuditItem.objects
        .filter(
            audit__status="approved",
            audit__branch__isnull=False,
        )
        .select_related(
            "audit__branch__company",
            "product__blend",
            "product__pack_size",
        )
    )
    
    consignment_audits = period.filter_datetime(
        consignment_audits,
        "audit__approved_at",
    )
    
    consignment_packs_sold = 0
    consignment_sales_value = ZERO
    consignment_kg_sold = ZERO
    
    for item in consignment_audits:
        packs_sold = item.quantity_sold or 0
        if packs_sold <= 0:
            continue
            
        sale_value = _decimal(item.calculated_amount_due)
        kg_sold = _decimal(packs_sold) * _decimal(item.product.kg_per_pack)
        
        consignment_packs_sold += packs_sold
        consignment_sales_value += sale_value
        consignment_kg_sold += kg_sold
        
        row = product_stats[item.product_id]
        row["product"] = item.product
        row["packs"] += packs_sold
        row["kg"] += kg_sold
        row["revenue"] += sale_value
        
        if item.audit.approved_at:
            sale_date = timezone.localtime(item.audit.approved_at).date()
            daily_sales[sale_date] += sale_value

    # Consignment Audit Allocations
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
        
        sale_value = _decimal(packs_sold) * _decimal(allocation.unit_price)
        kg_sold = _decimal(packs_sold) * _decimal(product.kg_per_pack)
        
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
    # 3. BULK ROASTED SALES
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
    bulk_sales = list(bulk_sales)
    request_bulk_sales = period.filter_datetime(
        CoffeeBatchSale.objects.select_related(
            "stock__variety",
            "account_holder",
            "company",
            "created_by",
        ),
        "sold_at",
    )
    bulk_sales.extend(request_bulk_sales)
    
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
        value = quantity * _decimal(sale.price_per_kg)
        
        bulk_sale_count += 1
        bulk_kg_sold += quantity
        bulk_sales_value += value
        
        if sale.buyer_name:
            buyer_name = sale.buyer_name.strip()
        else:
            account_holder = getattr(sale, "account_holder", None)
            company = getattr(sale, "company", None)
            buyer_name = str(account_holder or company or "Unknown Buyer")
        buyer = bulk_buyers[buyer_name.lower()]
        buyer["name"] = buyer_name
        buyer["kg"] += quantity
        buyer["revenue"] += value
        
        sale_date = (
            sale.sold_at if hasattr(sale, "sold_at") else sale.sale_date
        ).date()
        daily_sales[sale_date] += value

    # ============================================================
    # 4. TOTAL RECONCILED VALUE MATRIX
    # ============================================================
    total_sales_value = packaged_sales_value + consignment_sales_value + bulk_sales_value
    total_coffee_sold_kg = packaged_kg_sold + consignment_kg_sold + bulk_kg_sold

    # ============================================================
    # 5. PRODUCT PERFORMANCE & SHARE RANKING
    # ============================================================
    products = list(product_stats.values())
    products.sort(key=lambda row: (row["revenue"], row["packs"]), reverse=True)
    
    confirmed_packaged_value = packaged_sales_value + consignment_sales_value
    
    for row in products:
        row["kg"] = row["kg"].quantize(Decimal("0.01"))
        row["revenue"] = row["revenue"].quantize(Decimal("0.01"))
        row["revenue_share"] = (
            ((row["revenue"] / confirmed_packaged_value) * Decimal("100"))
            if confirmed_packaged_value > 0
            else ZERO
        ).quantize(Decimal("0.1"))
        
    top_products = products[:10]
    slow_products = sorted(products, key=lambda row: (row["packs"], row["revenue"]))[:10]

    salespeople = list(seller_stats.values())
    salespeople.sort(key=lambda row: row["revenue"], reverse=True)
    
    bulk_buyer_rows = list(bulk_buyers.values())
    bulk_buyer_rows.sort(key=lambda row: row["revenue"], reverse=True)

    trend_rows = []
    if period.start_date and period.end_date:
        current = period.start_date
        while current <= period.end_date:
            trend_rows.append({
                "date": current,
                "label": current.strftime("%d %b"),
                "revenue": daily_sales[current].quantize(Decimal("0.01")),
            })
            current += timedelta(days=1)
    else:
        monthly_sales = defaultdict(lambda: ZERO)
        for date_value, amount in daily_sales.items():
            month_key = date_value.replace(day=1)
            monthly_sales[month_key] += amount
        for month_key in sorted(monthly_sales.keys()):
            trend_rows.append({
                "date": month_key,
                "label": month_key.strftime("%b %Y"),
                "revenue": monthly_sales[month_key].quantize(Decimal("0.01")),
            })

    channels = []
    if packaged_sales_value > 0:
        channels.append({
            "label": "Packaged Coffee",
            "value": packaged_sales_value,
            "percentage": ((packaged_sales_value / total_sales_value) * Decimal("100")).quantize(Decimal("0.1")) if total_sales_value > 0 else ZERO,
        })
    if consignment_sales_value > 0:
        channels.append({
            "label": "Supermarket Consignment",
            "value": consignment_sales_value,
            "percentage": ((consignment_sales_value / total_sales_value) * Decimal("100")).quantize(Decimal("0.1")) if total_sales_value > 0 else ZERO,
        })
    if bulk_sales_value > 0:
        channels.append({
            "label": "Bulk Coffee",
            "value": bulk_sales_value,
            "percentage": ((bulk_sales_value / total_sales_value) * Decimal("100")).quantize(Decimal("0.1")) if total_sales_value > 0 else ZERO,
        })
    channels.sort(key=lambda row: row["value"], reverse=True)

    return {
        "total_sales_value": total_sales_value.quantize(Decimal("0.01")),
        "packaged_sales_value": packaged_sales_value.quantize(Decimal("0.01")),
        "consignment_sales_value": consignment_sales_value.quantize(Decimal("0.01")),
        "bulk_sales_value": bulk_sales_value.quantize(Decimal("0.01")),
        "packaged_packs_sold": packaged_packs_sold,
        "consignment_packs_sold": consignment_packs_sold,
        "bulk_sale_count": bulk_sale_count,
        "packaged_kg_sold": packaged_kg_sold.quantize(Decimal("0.01")),
        "consignment_kg_sold": consignment_kg_sold.quantize(Decimal("0.01")),
        "bulk_kg_sold": bulk_kg_sold.quantize(Decimal("0.01")),
        "total_coffee_sold_kg": total_coffee_sold_kg.quantize(Decimal("0.01")),
        "top_products": top_products,
        "slow_products": slow_products,
        "salespeople": salespeople[:10],
        "bulk_buyers": bulk_buyer_rows[:10],
        "sales_channels": channels,
        "trend_rows": trend_rows,
        "trend_labels": [row["label"] for row in trend_rows],
        "trend_values": [float(row["revenue"]) for row in trend_rows],
        "channel_labels": [row["label"] for row in channels],
        "channel_values": [float(row["value"]) for row in channels],
    }


def get_sales_control_report(period):
    """Prepares account holder and supermarket consignment tables, dispatch totals, and live AR/stock data."""
    def money(val):
        return _decimal(val)

    # 1. Account Holder Ledger rows
    releases = PackRelease.objects.filter(released_to__isnull=False).select_related("released_to", "product")
    period_releases = period.filter_datetime(releases, "released_at")

    holder_map = defaultdict(lambda: {
        "account": None,
        "name": "",
        "period_dispatch_packs": 0,
        "period_dispatch_value": ZERO,
        "period_sold_packs": 0,
        "period_sold_value": ZERO,
        "unconfirmed_packs": 0,
        "paid_to_date": ZERO,
        "outstanding": ZERO,
    })

    for rel in period_releases:
        h = rel.released_to
        row = holder_map[h.pk]
        row["account"] = h
        row["name"] = h.name
        packs = rel.packs_out or 0
        val = money(packs) * money(rel.selling_price)
        row["period_dispatch_packs"] += packs
        row["period_dispatch_value"] += val

    settlements = period.filter_datetime(PackSettlement.objects.select_related("release__released_to"), "cleared_at")
    for s in settlements:
        if s.release and s.release.released_to:
            h = s.release.released_to
            row = holder_map[h.pk]
            row["account"] = h
            row["name"] = h.name
            spacks = s.packs_sold or 0
            sval = money(spacks) * money(s.release.selling_price)
            row["period_sold_packs"] += spacks
            row["period_sold_value"] += sval

    # Compute AR & outstanding across all time for account holders
    all_releases = PackRelease.objects.filter(released_to__isnull=False).select_related("released_to")
    all_settlements = PackSettlement.objects.select_related("release__released_to")

    total_credit_extended = ZERO
    total_collected_to_date = ZERO

    for rel in all_releases:
        total_credit_extended += money(rel.packs_out) * money(rel.selling_price)
    for s in all_settlements:
        if s.release:
            total_collected_to_date += money(s.packs_sold) * money(s.release.selling_price)

    total_outstanding_debt = total_credit_extended - total_collected_to_date

    account_holder_rows = sorted(holder_map.values(), key=lambda x: x["period_dispatch_value"], reverse=True)

    # 2. Supermarket Consignment Ledger rows
    supermarket_companies = Company.objects.filter(is_supermarket=True) if hasattr(Company, 'is_supermarket') else Company.objects.all()
    supermarket_rows = []

    dispatch_total_value = ZERO
    dispatch_total_packs = 0
    current_shelf_packs = 0
    current_backroom_packs = 0
    current_stock_value = ZERO

    for comp in supermarket_companies:
        branch_count = comp.branches.count() if hasattr(comp, 'branches') else 0
        # Calculate stock and dispatches for this supermarket
        comp_releases = PackRelease.objects.filter(branch__company=comp)
        period_comp_releases = period.filter_datetime(comp_releases, "released_at")
        
        d_packs = sum(r.packs_out or 0 for r in period_comp_releases)
        d_val = sum((money(r.packs_out) * money(r.selling_price)) for r in period_comp_releases)
        
        dispatch_total_packs += d_packs
        dispatch_total_value += d_val

        # Shelf and backroom inventory (Fixed field names)
        inv_items = ConsignmentInventory.objects.filter(branch__company=comp).select_related('product')
        shelf = sum(i.current_shelf_quantity or 0 for i in inv_items)
        backroom = sum(i.current_backroom_quantity or 0 for i in inv_items)
        stock_val = sum((money(i.total_consignment_stock) * money(i.product.selling_price)) for i in inv_items if i.product)

        current_shelf_packs += shelf
        current_backroom_packs += backroom
        current_stock_value += stock_val

        supermarket_rows.append({
            "company": comp,
            "branch_count": branch_count,
            "period_dispatch_packs": d_packs,
            "period_dispatch_value": d_val,
            "period_sold_packs": 0,
            "period_sold_value": ZERO,
            "shelf_packs": shelf,
            "backroom_packs": backroom,
            "current_stock_value": stock_val,
            "outstanding": d_val,
        })

    return {
        "account_holder_rows": account_holder_rows,
        "supermarket_rows": supermarket_rows,
        "dispatch_total_value": dispatch_total_value.quantize(Decimal("0.01")),
        "dispatch_total_packs": dispatch_total_packs,
        "total_credit_extended": total_credit_extended.quantize(Decimal("0.01")),
        "total_collected_to_date": total_collected_to_date.quantize(Decimal("0.01")),
        "total_outstanding_debt": total_outstanding_debt.quantize(Decimal("0.01")),
        "current_shelf_packs": current_shelf_packs,
        "current_backroom_packs": current_backroom_packs,
        "current_stock_value": current_stock_value.quantize(Decimal("0.01"))}