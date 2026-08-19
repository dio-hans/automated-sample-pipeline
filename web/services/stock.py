from django.db import transaction

from ..models import StockMovement


@transaction.atomic
def receive_stock(stock, quantity, user=None):
    """
    Record the initial receipt of coffee into green inventory.
    """

    movement = StockMovement.objects.create(
        stock=stock,
        movement_type="receipt",
        from_stage=None,
        to_stage="green_received",
        quantity=quantity,
        created_by=user,
    )

    return movement