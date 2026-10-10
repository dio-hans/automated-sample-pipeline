"""Finance report: collections come only from PaymentReceipt."""
from collections import defaultdict
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from ...models import (
    Expense,
    PackRelease,
    PaymentReceipt,
)
from ...models import PAYMENT_METHOD_CHOICES


ZERO = Decimal('0.00')
METHODS = dict(PAYMENT_METHOD_CHOICES)
def money(v): return Decimal(str(v)) if v is not None else ZERO
def ageing_bucket(days):
    if days <= 30: return '0–30 days'
    if days <= 60: return '31–60 days'
    if days <= 90: return '61–90 days'
    return '91+ days'

def get_finance_report(period):
    by_method = {key: ZERO for key in METHODS}
    daily = defaultdict(lambda: ZERO)
    total_collections = ZERO
    payment_count = 0
    qs = period.filter_datetime(PaymentReceipt.objects.all(), 'collected_at')
    for payment in qs.iterator():
        amount = money(payment.amount)
        if amount < 0:
            raise ValueError(f'Negative payment PaymentReceipt #{payment.pk}')
        method = payment.payment_method
        if method == "bank_transfer":
            method = "bank"
        when = payment.collected_at
        by_method[method] = by_method.get(method, ZERO) + amount
        daily[timezone.localtime(when).date().isoformat()] += amount
        total_collections += amount
        payment_count += 1
    expenses = period.filter_date(Expense.objects.all(), 'expense_date')
    expense_rows = list(expenses.values('category').annotate(total=Sum('amount')).order_by('-total'))
    labels = dict(Expense.CATEGORY_CHOICES)
    for row in expense_rows:
        row['label'] = labels.get(row['category'], row['category'])
        row['total'] = money(row['total'])
    total_expenses = sum((r['total'] for r in expense_rows), ZERO)
    releases = PackRelease.objects.select_related(
        'released_to',
        'company',
    ).prefetch_related(
        'settlements',
        'payments',
        'returns',
        'consignment_audit_allocations',
    )
    debtor_map = defaultdict(lambda: {'balance': ZERO, 'invoices': 0})
    age_map = {k: ZERO for k in ('0–30 days','31–60 days','61–90 days','91+ days')}
    unallocated_credit = ZERO
    as_of = timezone.localdate()
    for release in releases.iterator(chunk_size=300):
        settlements = list(release.settlements.all())
        receipts = list(release.payments.all())
        if release.purpose == 'display':
            allocations = list(release.consignment_audit_allocations.all())
            sold_value = sum(
                (
                    money(allocation.quantity) * money(allocation.unit_price)
                    for allocation in allocations
                ),
                ZERO,
            )
        else:
            sold_packs = sum(s.packs_sold for s in settlements)
            sold_value = money(sold_packs) * money(release.selling_price)
        paid = sum((money(p.amount) for p in receipts), ZERO)
        # Do not deduct returns again unless their relationship to confirmed sales is reconciled.
        balance = max(sold_value - paid, ZERO)
        if paid > sold_value: unallocated_credit += paid - sold_value
        if balance <= 0: continue
        debtor = str(release.released_to or release.company or 'Unassigned')
        debtor_map[debtor]['balance'] += balance
        debtor_map[debtor]['invoices'] += 1
        days = max((as_of - timezone.localtime(release.released_at).date()).days, 0)
        age_map[ageing_bucket(days)] += balance
    debtors = sorted(({'name': n, **v} for n,v in debtor_map.items()), key=lambda r:r['balance'], reverse=True)
    return {
        'collections_total':total_collections, 'payment_count':payment_count,
        'payment_methods':[{'key':k,'label':METHODS.get(k,k),'amount':v} for k,v in by_method.items()],
        'collection_days':[{'date':k,'amount':v} for k,v in sorted(daily.items())],
        'expense_total':total_expenses, 'expense_ranking':expense_rows,
        'cash_less_expenses':total_collections-total_expenses,
        'outstanding_receivables':sum((r['balance'] for r in debtors),ZERO),
        'debtors':debtors,'ageing':[{'label':k,'amount':v} for k,v in age_map.items()],
        'unallocated_credit':unallocated_credit,'bulk_receivables_pending':True,
        'ageing_basis':'Days since stock release; not contractual overdue days',
        'timestamp_warning':'Collections are dated by PaymentReceipt.collected_at.',
    }
