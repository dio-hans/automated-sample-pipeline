"""R4 finance: PackSettlement is the sole collection ledger.

Never infer a sale from PackRelease.packs_out. Bulk receivables remain excluded
until CoffeeBatchSale's current schema and payment linkage are confirmed.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from ...models import Expense, PackRelease, PackSettlement

ZERO = Decimal('0.00')
METHODS = dict(PackSettlement.PAYMENT_CHOICES)

def money(value):
    return Decimal(str(value)) if value is not None else ZERO

def ageing_bucket(days):
    if days <= 30: return '0–30 days'
    if days <= 60: return '31–60 days'
    if days <= 90: return '61–90 days'
    return '91+ days'

def get_finance_report(period):
    # cleared_at is auto_now in the supplied model. Edits can redate historical payments.
    payments = period.filter_datetime(PackSettlement.objects.all(), 'cleared_at')
    by_method = {key: ZERO for key in METHODS}
    daily = defaultdict(lambda: ZERO)
    total_collections = ZERO
    payment_count = 0
    for payment in payments.iterator():
        amount = money(payment.amount_paid)
        if amount < 0:
            raise ValueError(f'Negative settlement #{payment.pk} requires reconciliation')
        by_method[payment.payment_method] = by_method.get(payment.payment_method, ZERO) + amount
        daily[timezone.localtime(payment.cleared_at).date().isoformat()] += amount
        total_collections += amount
        payment_count += 1

    expenses = period.filter_date(Expense.objects.all(), 'expense_date')
    expense_rows = list(expenses.values('category').annotate(total=Sum('amount')).order_by('-total'))
    labels = dict(Expense.CATEGORY_CHOICES)
    for row in expense_rows:
        row['label'] = labels.get(row['category'], row['category'])
        row['total'] = money(row['total'])
    total_expenses = sum((row['total'] for row in expense_rows), ZERO)

    # Debtors are LIVE balances, deliberately not restricted to selected period.
    # A release is stock movement; only settlement.packs_sold establishes sold value.
    # Approved returns can only reduce sold receivables up to the sold amount.
    releases = PackRelease.objects.select_related('released_to', 'company').prefetch_related('settlements', 'returns')
    debtor_map = defaultdict(lambda: {'balance': ZERO, 'invoices': 0})
    age_map = {key: ZERO for key in ('0–30 days','31–60 days','61–90 days','91+ days')}
    unallocated_credit = ZERO
    as_of = timezone.localdate()
    for release in releases.iterator(chunk_size=300):
        settlements = list(release.settlements.all())
        sold_packs = sum(s.packs_sold for s in settlements)
        sold_value = money(sold_packs) * money(release.selling_price)
        paid = sum((money(s.amount_paid) for s in settlements), ZERO)
        # Returns are tracked separately; the source does not establish whether
        # returned packs had previously been counted in packs_sold.
        # Do NOT subtract return_credit from confirmed sold value automatically.
        balance = max(sold_value - paid, ZERO)
        if paid > sold_value:
            unallocated_credit += paid - sold_value
        if balance <= 0:
            continue
        debtor = str(release.released_to or release.company or 'Unassigned')
        debtor_map[debtor]['balance'] += balance
        debtor_map[debtor]['invoices'] += 1
        # Release date is a provisional proxy, NOT contractual overdue date.
        days = max((as_of - timezone.localtime(release.released_at).date()).days, 0)
        age_map[ageing_bucket(days)] += balance
    debtors = sorted(({'name': name, **values} for name, values in debtor_map.items()), key=lambda x:x['balance'], reverse=True)
    return {
        'collections_total': total_collections,
        'payment_count': payment_count,
        'payment_methods': [{'key': k, 'label': METHODS.get(k, k), 'amount': v} for k,v in by_method.items()],
        'collection_days': [{'date': k, 'amount': v} for k,v in sorted(daily.items())],
        'expense_total': total_expenses, 'expense_ranking': expense_rows,
        'cash_less_expenses': total_collections - total_expenses,
        'outstanding_receivables': sum((r['balance'] for r in debtors), ZERO),
        'debtors': debtors, 'ageing': [{'label': k, 'amount': v} for k,v in age_map.items()],
        'unallocated_credit': unallocated_credit,
        'bulk_receivables_pending': True,
        'ageing_basis': 'Days since stock release; not contractual overdue days',
        'timestamp_warning': 'PackSettlement.cleared_at uses auto_now: edits may change historical payment dates.',
    }
