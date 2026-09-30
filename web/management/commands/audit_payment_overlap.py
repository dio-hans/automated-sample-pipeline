"""Read-only legacy review: possible duplicate payment entries across TWO tables.

Matches are only *candidates*. Verify cashbook/mobile-money statement before correction.
"""
from datetime import timedelta
from django.core.management.base import BaseCommand
from ...models import PackSettlement, PaymentReceipt


class Command(BaseCommand):
    help = 'Show possible overlapping PackSettlement and PaymentReceipt payments without changing data.'

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=int, default=24)

    def handle(self, *args, **options):
        hours = max(options['hours'], 0)
        count = 0
        for payment in PaymentReceipt.objects.select_related('release').order_by('pk').iterator():
            candidates = PackSettlement.objects.filter(
                release_id=payment.release_id,
                amount_paid=payment.amount,
                payment_method=payment.method,
                cleared_at__range=(payment.collected_at - timedelta(hours=hours),
                                   payment.collected_at + timedelta(hours=hours)),
            )
            reference = (payment.payment_reference or '').strip().casefold()
            for settlement in candidates:
                other_reference = (settlement.payment_reference or '').strip().casefold()
                if reference and other_reference and reference != other_reference:
                    continue
                count += 1
                self.stdout.write(
                    f'POSSIBLE overlap | release={payment.release_id} '
                    f'receipt={payment.pk} settlement={settlement.pk} '
                    f'amount={payment.amount} method={payment.method} '
                    f'ref={reference or other_reference or "(none)"}'
                )
        self.stdout.write(f'Found {count} possible overlaps. No records were modified.')
