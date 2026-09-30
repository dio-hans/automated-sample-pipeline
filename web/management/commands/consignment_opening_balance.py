"""Explicitly bridge pre-existing legacy on-hand inventory to a signed stock ledger.

Only permitted if a product/branch has *no* ledger entries at all. No balance is changed.
This is an operator command; verify historical figures in person before --commit.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from ...models import CompanyBranch, ConsignmentInventory, BranchStockLedger


class Command(BaseCommand):
    help = 'Preview or explicitly post VERIFIED legacy branch opening balances.'

    def add_arguments(self, parser):
        parser.add_argument('--branch-id', type=int, required=True)
        parser.add_argument('--user-id', type=int, required=True)
        parser.add_argument('--commit', action='store_true', default=False)

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            user = get_user_model().objects.get(pk=options['user_id'])
            branch = CompanyBranch.objects.select_for_update().get(pk=options['branch_id'])
        except (get_user_model().DoesNotExist, CompanyBranch.DoesNotExist) as exc:
            raise CommandError(str(exc))
        inventories = ConsignmentInventory.objects.select_for_update().filter(branch=branch).select_related('product')
        pending = []
        for inv in inventories:
            if BranchStockLedger.objects.filter(branch=branch, product=inv.product).exists():
                self.stdout.write(f'SKIP {inv.product}: already has ledger entries; manual reconciliation required if mismatched.')
                continue
            for location, quantity in (
                ('shelf', inv.current_shelf_quantity),
                ('backroom', inv.current_backroom_quantity),
            ):
                if quantity:
                    pending.append((inv.product, location, quantity))
                    self.stdout.write(f'{branch}: {inv.product} {location} VERIFIED opening +{quantity}')
        if not options['commit']:
            self.stdout.write(self.style.WARNING('PREVIEW ONLY. Check physical stock and rerun with --commit after verification.'))
            return
        if not pending:
            self.stdout.write('No opening balances to record.')
            return
        for product, location, quantity in pending:
            BranchStockLedger.objects.create(
                branch=branch, product=product, stock_location=location,
                transaction_type='opening_balance', quantity=quantity, created_by=user,
            )
        self.stdout.write(self.style.SUCCESS(f'Posted {len(pending)} opening entries; on-hand figures were not changed.'))
