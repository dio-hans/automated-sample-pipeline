"""R6 executive overview: reuse R2-R5, never recompute financial ledgers."""
from decimal import Decimal
from datetime import timedelta
from .consumption import get_stock_consumption_report
from .sales import get_sales_report
from .finance import get_finance_report
from .operations import get_operations_report
from .periods import ReportPeriod

ZERO = Decimal("0.00")

def _value(report, *names):
    for name in names:
        value = report.get(name)
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return value
    return None

def _previous_period(period):
    if period.start_date is None or period.end_date is None:
        return None
    days = (period.end_date - period.start_date).days + 1
    end = period.start_date - timedelta(days=1)
    start = end - timedelta(days=days-1)
    from datetime import datetime, time
    from django.utils import timezone
    start_dt = timezone.make_aware(datetime.combine(start, time.min))
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min))
    return ReportPeriod("comparison", start, end, start_dt, end_dt, "Previous equal-length period")

def _comparison(current, previous):
    if current is None or previous is None:
        return {"current": current, "previous": previous, "change": None, "percent": None}
    delta = Decimal(str(current)) - Decimal(str(previous))
    pct = (delta / Decimal(str(previous)) * 100).quantize(Decimal("0.1")) if previous else None
    return {"current": current, "previous": previous, "change": delta, "percent": pct}

def get_executive_report(period):
    consumption = get_stock_consumption_report(period)
    sales = get_sales_report(period)
    finance = get_finance_report(period)
    operations = get_operations_report(period)
    previous = _previous_period(period)
    previous_finance = get_finance_report(previous) if previous else None
    previous_sales = get_sales_report(previous) if previous else None
    previous_consumption = get_stock_consumption_report(previous) if previous else None
    # The R3 source was not supplied as a standalone file: do not guess its KPI keys.
    # Expose its full result in the context; map sales KPIs only when a known key exists.
    sales_value = _value(sales, "total_sales_value", "total_revenue", "net_sales_value")
    previous_sales_value = _value(previous_sales, "total_sales_value", "total_revenue", "net_sales_value") if previous_sales else None
    alerts = []
    if operations["exception_count"]:
        alerts.append({"level":"high", "title":"Operational reconciliation", "detail":f"{operations['exception_count']} processing/packaging run(s) have quantity exceptions.", "url_name":"reports_operations"})
    if operations["open_processing_count"] or operations["open_packaging_count"]:
        alerts.append({"level":"info", "title":"Work in progress", "detail":f"{operations['open_processing_count']} processing and {operations['open_packaging_count']} packaging run(s) are open.", "url_name":"reports_operations"})
    if finance["outstanding_receivables"] > ZERO:
        alerts.append({"level":"warning", "title":"Receivables to review", "detail":"Outstanding confirmed packaged-sales balances require follow-up.", "url_name":"reports_finance"})
    if finance["unallocated_credit"] > ZERO:
        alerts.append({"level":"high", "title":"Payment reconciliation", "detail":"Payments exceed confirmed sold value on one or more releases.", "url_name":"reports_finance"})
    if finance.get("bulk_receivables_pending"):
        alerts.append({"level":"info", "title":"Bulk reporting incomplete", "detail":"Bulk batch-sale receivables are excluded until their payment linkage is implemented.", "url_name":"reports_finance"})
    return {
        "executive_consumption": consumption, "executive_sales": sales,
        "executive_finance": finance, "executive_operations": operations,
        "executive_alerts": alerts,
        "sales_comparison": _comparison(sales_value, previous_sales_value),
        "collections_comparison": _comparison(finance["collections_total"], previous_finance["collections_total"] if previous_finance else None),
        "expenses_comparison": _comparison(finance["expense_total"], previous_finance["expense_total"] if previous_finance else None),
        "consumption_comparison": _comparison(consumption["total_consumed_kg"], previous_consumption["total_consumed_kg"] if previous_consumption else None),
        "comparison_label": previous.label if previous else "No comparison for overall period",
        "executive_chart_labels": [row["date"] for row in finance["collection_days"]],
        "executive_chart_collections": [float(row["amount"]) for row in finance["collection_days"]],
        "executive_operations_labels": operations["trend_labels"],
        "executive_operations_input": operations["trend_processing_inputs"],
        "executive_operations_loss": operations["trend_processing_losses"],
    }
