"""Branch-level consignment workflow. Existing inventory/release/audit tables are reused.

Only an approved audit produces billable units. Monetary collections remain solely
in PackSettlement and PaymentReceipt; a stock audit is NEVER a cash collection.
"""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ..models import (
    BranchStockLedger, CompanyBranch, ConsignmentInventory, ConsignmentInvoice,
    ConsignmentAuditAllocation, PackagedInventory, PackRelease, PackReturn,
    StockAudit, StockAuditItem,
)

ZERO = Decimal('0.00')


def calculate_variance(expected_shelf, expected_backroom, actual_shelf,
                       actual_backroom, shrinkage_shelf=0, shrinkage_backroom=0):
    numbers = (expected_shelf, expected_backroom, actual_shelf,
               actual_backroom, shrinkage_shelf, shrinkage_backroom)
    if any(not isinstance(n, int) or n < 0 for n in numbers):
        raise ValidationError('All stock counts must be non-negative whole numbers.')
    if actual_shelf > expected_shelf or actual_backroom > expected_backroom:
        raise ValidationError('Actual count exceeds recorded stock at a location. Record a shelf transfer or correct the opening stock first.')
    lost_shelf = expected_shelf - actual_shelf
    lost_backroom = expected_backroom - actual_backroom
    if shrinkage_shelf > lost_shelf or shrinkage_backroom > lost_backroom:
        raise ValidationError('Damaged/missing units cannot exceed the shortage at their location.')
    return {
        'sold_shelf': lost_shelf - shrinkage_shelf,
        'sold_backroom': lost_backroom - shrinkage_backroom,
        'sold': lost_shelf + lost_backroom - shrinkage_shelf - shrinkage_backroom,
        'shrinkage': shrinkage_shelf + shrinkage_backroom,
    }


def _consistent_ledger(inventory):
    """Never silently mix imported on-hand balances with an empty ledger."""
    for location, on_hand in (
        ('shelf', inventory.current_shelf_quantity),
        ('backroom', inventory.current_backroom_quantity),
    ):
        booked = (BranchStockLedger.objects.filter(
            branch=inventory.branch, product=inventory.product,
            stock_location=location,
        ).aggregate(qty=Sum('quantity'))['qty'] or 0)
        if booked != on_hand:
            raise ValidationError(
                f'{inventory.product}: {location} ledger ({booked}) differs from '
                f'on-hand ({on_hand}). Reconcile legacy opening stock before continuing.'
            )


def _block_pending_audit(branch):
    if StockAudit.objects.filter(branch=branch, status='pending_approval').exists():
        raise ValidationError('This branch has an audit awaiting approval. Approve or reject it before moving stock.')


def _assert_attributed_shrinkage(branch, product):
    """Legacy shrinkage with no release link could be rebilled later."""
    if BranchStockLedger.objects.filter(
        branch=branch, product=product, transaction_type='shrinkage',
        release__isnull=True,
    ).exists():
        raise ValidationError(
            f'{product}: an older shrinkage entry has no delivery allocation. '
            'Reconcile it before new audit billing or branch returns.'
        )


def _unbilled_release_quantity(release):
    """Packs physically eligible to be billed or returned; shrinkage is not saleable."""
    approved = sum(r.packs_returned for r in release.returns.all() if r.status == 'approved')
    pending = sum(r.packs_returned for r in release.returns.all() if r.status == 'pending_approval')
    written_off = -(BranchStockLedger.objects.filter(
        release=release, transaction_type='shrinkage',
    ).aggregate(total=Sum('quantity'))['total'] or 0)
    if written_off < 0:
        raise ValidationError(f'Display release #{release.pk} has invalid positive shrinkage entries.')
    available = release.packs_out - release.billable_packs - approved - pending - written_off
    if available < 0:
        raise ValidationError(
            f'Display release #{release.pk} has more billed/returned/written-off packs '
            'than were dispatched. Reconcile historical records.'
        )
    return available


@transaction.atomic
def record_consignment_delivery(*, branch, product, shelf, backroom, unit_price, user, notes=''):
    """Issue main-warehouse stock exactly once; ledger and branch balance in one transaction."""
    shelf, backroom, unit_price = int(shelf), int(backroom), Decimal(str(unit_price))
    if shelf < 0 or backroom < 0 or not (shelf + backroom) or unit_price <= ZERO:
        raise ValidationError('Enter a positive pack quantity and positive unit price.')
    branch = CompanyBranch.objects.select_for_update().select_related('company').get(pk=branch.pk)
    if not branch.is_active:
        raise ValidationError('Inactive branches cannot receive stock.')
    if not branch.company.account_holder_id:
        raise ValidationError('This company needs an account holder before dispatch.')
    _block_pending_audit(branch)
    warehouse = PackagedInventory.objects.select_for_update().filter(product=product).first()
    if not warehouse or warehouse.available < shelf + backroom:
        raise ValidationError('Insufficient packaged stock in the central warehouse.')
    inventory, _ = ConsignmentInventory.objects.get_or_create(branch=branch, product=product)
    inventory = ConsignmentInventory.objects.select_for_update().get(pk=inventory.pk)
    _consistent_ledger(inventory)
    release = PackRelease.objects.create(
        product=product, branch=branch, company=branch.company,
        released_to=branch.company.account_holder, purpose='display',
        packs_out=shelf + backroom, billable_packs=0, selling_price=unit_price,
        created_by=user, notes=notes,
    )
    for location, quantity in (('shelf', shelf), ('backroom', backroom)):
        if quantity:
            BranchStockLedger.objects.create(
                branch=branch, product=product, stock_location=location,
                transaction_type='delivery', quantity=quantity,
                release=release, created_by=user,
            )
    inventory.current_shelf_quantity += shelf
    inventory.current_backroom_quantity += backroom
    inventory.save(update_fields=['current_shelf_quantity', 'current_backroom_quantity'])
    return release


@transaction.atomic
def transfer_to_shelf(*, branch, product, quantity, user):
    quantity = int(quantity)
    if quantity <= 0:
        raise ValidationError('Transfer quantity must be positive.')
    branch = CompanyBranch.objects.select_for_update().get(pk=branch.pk)
    _block_pending_audit(branch)
    inventory = ConsignmentInventory.objects.select_for_update().get(branch=branch, product=product)
    _consistent_ledger(inventory)
    if inventory.current_backroom_quantity < quantity:
        raise ValidationError('Not enough backroom stock to restock the shelf.')
    for location, delta in (('backroom', -quantity), ('shelf', quantity)):
        BranchStockLedger.objects.create(
            branch=branch, product=product, stock_location=location,
            transaction_type='restock_shelf', quantity=delta, created_by=user,
        )
    inventory.current_backroom_quantity -= quantity
    inventory.current_shelf_quantity += quantity
    inventory.save(update_fields=['current_backroom_quantity', 'current_shelf_quantity'])
    return inventory


@transaction.atomic
def submit_branch_audit(*, branch, user, rows, notes=''):
    """Capture a snapshot. No ledger, invoice, stock or debt is changed yet."""
    branch = CompanyBranch.objects.select_for_update().select_related('company').get(pk=branch.pk)
    _block_pending_audit(branch)
    inventories = list(ConsignmentInventory.objects.select_for_update()
                       .filter(branch=branch).select_related('product'))
    if not inventories:
        raise ValidationError('This branch has no stock to audit.')
    by_product = {int(r['product_id']): r for r in rows}
    if len(by_product) != len(rows) or set(by_product) != {i.product_id for i in inventories}:
        raise ValidationError('Audit every SKU at this branch exactly once.')
    validated = []
    for inv in inventories:
        _consistent_ledger(inv)
        row = by_product[inv.product_id]
        try:
            actual_shelf = int(row['actual_shelf'])
            actual_backroom = int(row['actual_backroom'])
            shrinkage_shelf = int(row['shrinkage_shelf'])
            shrinkage_backroom = int(row['shrinkage_backroom'])
            price = Decimal(str(row['selling_price']))
        except (TypeError, ValueError, ArithmeticError):
            raise ValidationError(f'Invalid count or price for {inv.product}.')
        delta = calculate_variance(
            inv.current_shelf_quantity, inv.current_backroom_quantity,
            actual_shelf, actual_backroom, shrinkage_shelf, shrinkage_backroom,
        )
        if (delta['sold'] and price <= ZERO) or price < ZERO:
            raise ValidationError(f'Enter a positive invoice price for {inv.product}.')
        validated.append((inv, actual_shelf, actual_backroom,
                          shrinkage_shelf, shrinkage_backroom, price))
    audit = StockAudit.objects.create(
        company=branch.company, branch=branch, audited_by=user,
        status='pending_approval', notes=notes,
    )
    for inv, shelf, backroom, shrink_shelf, shrink_backroom, price in validated:
        StockAuditItem.objects.create(
            audit=audit, product=inv.product,
            expected_shelf=inv.current_shelf_quantity,
            expected_backroom=inv.current_backroom_quantity,
            actual_shelf=shelf, actual_backroom=backroom,
            shrinkage_shelf=shrink_shelf, shrinkage_backroom=shrink_backroom,
            shrinkage_quantity=shrink_shelf + shrink_backroom,
            selling_price=price,
        )
    return audit


@transaction.atomic
def approve_branch_audit(*, audit_id, manager):
    """FIFO bill unbilled branch-linked releases and post each movement once."""
    audit = StockAudit.objects.select_for_update().select_related('branch', 'company').get(pk=audit_id)
    if audit.status != 'pending_approval':
        raise ValidationError('Only a pending audit can be approved.')
    if audit.branch_id is None:
        raise ValidationError('Legacy company-only audits must be assigned to a branch and reviewed before approval.')
    branch = CompanyBranch.objects.select_for_update().get(pk=audit.branch_id)
    items = list(audit.items.select_related('product').order_by('pk'))
    if not items:
        raise ValidationError('Cannot approve an empty audit.')
    total = ZERO
    for item in items:
        inv = ConsignmentInventory.objects.select_for_update().get(branch=branch, product=item.product)
        _consistent_ledger(inv)
        if (inv.current_shelf_quantity != item.expected_shelf or
                inv.current_backroom_quantity != item.expected_backroom):
            raise ValidationError(f'{item.product}: inventory changed since submission. Reject and submit a fresh audit.')
        delta = calculate_variance(
            item.expected_shelf, item.expected_backroom,
            item.actual_shelf, item.actual_backroom,
            item.shrinkage_shelf, item.shrinkage_backroom,
        )
        if (item.quantity_sold != delta['sold'] or
                item.shrinkage_quantity != delta['shrinkage'] or
                item.calculated_amount_due != delta['sold'] * item.selling_price):
            raise ValidationError(f'{item.product}: audit line was edited after submission. Re-submit a verified snapshot.')
        # Sale and shrinkage MUST both be allocated to real delivered stock.
        # Otherwise the next audit could bill units already written off as damaged.
        _assert_attributed_shrinkage(branch, item.product)
        remaining = delta['sold']
        releases = list(PackRelease.objects.select_for_update().filter(
            branch=branch, product=item.product, purpose='display'
        ).prefetch_related('returns').order_by('released_at', 'pk'))
        for release in releases:
            if release.returns.filter(status='pending_approval').exists():
                raise ValidationError(
                    f'Display release #{release.pk} has a pending return. '
                    'Resolve it before approving this audit.'
                )
        for release in releases:
            if remaining == 0:
                break
            unbilled = _unbilled_release_quantity(release)
            if not unbilled:
                continue
            if release.settlements.filter(packs_sold__gt=0).exists():
                raise ValidationError(
                    f'Display release #{release.pk} already carries sold packs in settlements. '
                    'Reconcile historical records before audit billing to prevent duplicate sales.'
                )
            if release.selling_price != item.selling_price:
                raise ValidationError(
                    f'{item.product}: this delivery is priced at UGX {release.selling_price} '
                    f'but the audit uses UGX {item.selling_price}. Use a consistent price or reconcile '
                    'the separate delivery prices before approval.'
                )
            allocated = min(unbilled, remaining)
            ConsignmentAuditAllocation.objects.create(
                audit_item=item, release=release, quantity=allocated,
                unit_price=release.selling_price,
            )
            release.billable_packs += allocated
            release.save(update_fields=['billable_packs'])
            remaining -= allocated
        if remaining:
            raise ValidationError(
                f'{item.product}: {remaining} confirmed sold pack(s) lack an unbilled '
                'branch-linked delivery. Reconcile delivery records first.'
            )
        # Sold units are traceable through ConsignmentAuditAllocation, not PackSettlement.
        for location, sold in (
            ('shelf', delta['sold_shelf']), ('backroom', delta['sold_backroom']),
        ):
            if sold:
                BranchStockLedger.objects.create(
                    branch=branch, product=item.product, audit=audit,
                    stock_location=location, transaction_type='audit_sale',
                    quantity=-sold, created_by=manager,
                )
        # Shrinkage also consumes a delivery's remaining unbilled allocation.
        # The existing BranchStockLedger.release FK records this without new tables.
        for location, loss in (
            ('shelf', item.shrinkage_shelf), ('backroom', item.shrinkage_backroom),
        ):
            loss_remaining = loss
            for release in releases:
                if not loss_remaining:
                    break
                available = _unbilled_release_quantity(release)
                if not available:
                    continue
                taken = min(available, loss_remaining)
                BranchStockLedger.objects.create(
                    branch=branch, product=item.product, audit=audit, release=release,
                    stock_location=location, transaction_type='shrinkage',
                    quantity=-taken, created_by=manager,
                )
                loss_remaining -= taken
            if loss_remaining:
                raise ValidationError(
                    f'{item.product}: {loss_remaining} shrinkage pack(s) could not be '
                    'traced to an unbilled delivery. Reconcile this branch first.'
                )
        inv.current_shelf_quantity = item.actual_shelf
        inv.current_backroom_quantity = item.actual_backroom
        inv.last_audited_at = timezone.now()
        inv.save(update_fields=['current_shelf_quantity', 'current_backroom_quantity', 'last_audited_at'])
        total += item.calculated_amount_due
    audit.status = 'approved'
    audit.approved_by = manager
    audit.approved_at = timezone.now()
    audit.rejection_reason = ''
    audit.save(update_fields=['status', 'approved_by', 'approved_at', 'rejection_reason'])
    invoice = ConsignmentInvoice.objects.create(audit=audit, total_amount=total)
    return invoice


@transaction.atomic
def reject_branch_audit(*, audit_id, manager, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Give a reason for rejecting this audit.')
    audit = StockAudit.objects.select_for_update().get(pk=audit_id)
    if audit.status != 'pending_approval':
        raise ValidationError('Only a pending audit can be rejected.')
    audit.status = 'rejected'
    audit.rejection_reason = reason
    audit.reviewed_by = manager
    audit.reviewed_at = timezone.now()
    audit.save(update_fields=['status', 'rejection_reason', 'reviewed_by', 'reviewed_at'])
    return audit


@transaction.atomic
def record_good_branch_return(*, branch, release, location, quantity, manager):
    """Manager-verifies sealed unsold stock returned to central warehouse."""
    quantity = int(quantity)
    if location not in ('shelf', 'backroom') or quantity <= 0:
        raise ValidationError('Enter a valid location and positive quantity.')
    branch = CompanyBranch.objects.select_for_update().get(pk=branch.pk)
    _block_pending_audit(branch)
    release = PackRelease.objects.select_for_update().get(pk=release.pk)
    if release.branch_id != branch.pk or release.purpose != 'display':
        raise ValidationError('Release does not belong to this consignment branch.')
    inv = ConsignmentInventory.objects.select_for_update().get(branch=branch, product=release.product)
    _consistent_ledger(inv)
    current = inv.current_shelf_quantity if location == 'shelf' else inv.current_backroom_quantity
    if quantity > current:
        raise ValidationError('Return exceeds the physical stock in this location.')
    _assert_attributed_shrinkage(branch, release.product)
    if quantity > _unbilled_release_quantity(release):
        raise ValidationError('Return exceeds the unbilled, unreturned, non-damaged units of this delivery.')
    pack_return = PackReturn.objects.create(
        release=release, packs_returned=quantity, condition='good',
        status='approved', disposition='accepted', approved_by=manager,
        received_by=manager, approved_at=timezone.now(),
        reason=f'Unbilled consignment return from {branch}',
    )
    BranchStockLedger.objects.create(
        branch=branch, product=release.product, stock_location=location,
        transaction_type='return', quantity=-quantity, release=release,
        created_by=manager,
    )
    if location == 'shelf':
        inv.current_shelf_quantity -= quantity
        inv.save(update_fields=['current_shelf_quantity'])
    else:
        inv.current_backroom_quantity -= quantity
        inv.save(update_fields=['current_backroom_quantity'])
    return pack_return
