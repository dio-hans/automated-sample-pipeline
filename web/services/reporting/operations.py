"""R5 operational reporting. Completed runs are dated by completion; open WIP is live."""
from collections import defaultdict
from decimal import Decimal
from django.utils import timezone
from django.db.models import Count, Sum
from ...models import ProcessingRun, PackagingRun, StockMovement

ZERO = Decimal('0.00')

def dec(value):
    return Decimal(str(value)) if value is not None else ZERO

def pct(numerator, denominator):
    return (numerator * 100 / denominator).quantize(Decimal('0.1')) if denominator else ZERO

def get_operations_report(period):
    processing = period.filter_datetime(
        ProcessingRun.objects.filter(status='completed').select_related('stock__variety'), 'completed_at'
    )
    by_process = defaultdict(lambda: {'runs': 0, 'input_kg': ZERO, 'primary_kg': ZERO,
                                      'secondary_kg': ZERO, 'loss_kg': ZERO, 'unaccounted_kg': ZERO})
    totals = {'runs': 0, 'input_kg': ZERO, 'primary_kg': ZERO,
              'secondary_kg': ZERO, 'loss_kg': ZERO, 'unaccounted_kg': ZERO}
    exceptions = []
    trend = defaultdict(lambda: {'completed_runs': 0, 'processing_input_kg': ZERO,
                                 'processing_loss_kg': ZERO, 'packaging_packs': 0})
    for run in processing.iterator():
        input_kg, primary, secondary, loss = map(dec, (
            run.input_quantity, run.output_quantity, run.secondary_output_quantity, run.loss_quantity))
        variance = input_kg - primary - secondary - loss
        for row in (by_process[run.process_type], totals):
            row['runs'] += 1
            row['input_kg'] += input_kg
            row['primary_kg'] += primary
            row['secondary_kg'] += secondary
            row['loss_kg'] += loss
            row['unaccounted_kg'] += variance
        if variance != ZERO or min(input_kg, primary, secondary, loss) < ZERO:
            exceptions.append({'kind': 'Processing', 'id': run.pk, 'batch': run.stock.batch_number,
                               'variance_kg': variance, 'date': run.completed_at})
        if run.completed_at:
            day = timezone.localtime(run.completed_at).date().isoformat()
            trend[day]['completed_runs'] += 1
            trend[day]['processing_input_kg'] += input_kg
            trend[day]['processing_loss_kg'] += loss
    process_rows = []
    for key in ('roasting', 'sorting', 'grinding'):
        row = dict(by_process[key])
        row.update(name=key.title(), yield_pct=pct(row['primary_kg'] + row['secondary_kg'], row['input_kg']),
                   loss_pct=pct(row['loss_kg'], row['input_kg']))
        process_rows.append(row)

    packaging = period.filter_datetime(
        PackagingRun.objects.filter(status='completed').select_related('stock', 'product'), 'completed_at'
    )
    pack = {'runs': 0, 'input_kg': ZERO, 'coffee_used_kg': ZERO, 'loss_kg': ZERO,
            'packs_produced': 0, 'unaccounted_kg': ZERO}
    for run in packaging.iterator():
        input_kg, used, loss = map(dec, (run.input_kg, run.coffee_used_kg, run.loss_kg))
        variance = input_kg - used - loss
        pack['runs'] += 1
        pack['input_kg'] += input_kg
        pack['coffee_used_kg'] += used
        pack['loss_kg'] += loss
        pack['packs_produced'] += run.packs_produced or 0
        pack['unaccounted_kg'] += variance
        if variance != ZERO or min(input_kg, used, loss) < ZERO:
            exceptions.append({'kind': 'Packaging', 'id': run.pk, 'batch': run.stock.batch_number,
                               'variance_kg': variance, 'date': run.completed_at})
        if run.completed_at:
            day = timezone.localtime(run.completed_at).date().isoformat()
            trend[day]['packaging_packs'] += run.packs_produced or 0
    pack['yield_pct'] = pct(pack['coffee_used_kg'], pack['input_kg'])
    pack['loss_pct'] = pct(pack['loss_kg'], pack['input_kg'])
    totals['yield_pct'] = pct(totals['primary_kg'] + totals['secondary_kg'], totals['input_kg'])
    totals['loss_pct'] = pct(totals['loss_kg'], totals['input_kg'])

    movements = period.filter_datetime(StockMovement.objects.all(), 'created_at')
    movement_rows = [{'movement_type': row['movement_type'], 'label': dict(StockMovement.MOVEMENT_TYPES).get(
        row['movement_type'], row['movement_type']), 'count': row['count'], 'kg': row['kg'] or ZERO}
        for row in movements.values('movement_type').annotate(
            count=Count('pk'),
            kg=Sum('quantity')).order_by('movement_type')]
    # Do not sum movement categories together: a batch can move through multiple stages.
    open_processing = ProcessingRun.objects.filter(status='open').select_related('stock').order_by('issued_at')
    open_packaging = PackagingRun.objects.filter(status='open').select_related('stock', 'product').order_by('issued_at')
    return {
        'processing': totals, 'process_rows': process_rows, 'packaging': pack,
        'movement_rows': movement_rows, 'movement_count': sum(x['count'] for x in movement_rows),
        'open_processing_count': open_processing.count(), 'open_packaging_count': open_packaging.count(),
        'open_processing': list(open_processing[:20]), 'open_packaging': list(open_packaging[:20]),
        'exceptions': exceptions[:50], 'exception_count': len(exceptions),
        'trend_rows': [{'date': day, **values} for day, values in sorted(trend.items())],
        'trend_labels': sorted(trend),
        'trend_processing_inputs': [float(trend[d]['processing_input_kg']) for d in sorted(trend)],
        'trend_processing_losses': [float(trend[d]['processing_loss_kg']) for d in sorted(trend)],
    }
