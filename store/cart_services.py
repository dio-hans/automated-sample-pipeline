
from django.db import transaction
from django.db.models import F, Prefetch, Sum
from django.utils import timezone

from web.models import PackagedInventory

from .models import (
    StoreCart,
    StoreCartItem,
    StoreCartItem,
    StorePromotion,
    StoreProductContent,
)


class CartOperationError(Exception):
    """An expected shopping-cart validation error."""


def get_open_cart(request):
    """Return the open cart belonging to this visitor's session."""

    if not request.session.session_key:
        request.session.create()

    cart, _ = StoreCart.objects.get_or_create(
        session_key=request.session.session_key,
        status=StoreCart.Status.OPEN,
    )

    return cart


def sync_cart_count(request, cart):
    """Update the navigation count with the number of packs."""

    count = (
        cart.items.aggregate(total=Sum("quantity"))["total"]
        or 0
    )

    request.session["store_cart_quantity_count"] = count

    return count


def product_is_listed(product):
    """Check that the product is eligible for the online shop."""

    try:
        content = product.store_content
    except StoreProductContent.DoesNotExist:
        return False

    return bool(
        content.is_visible
        and product.is_active
        and product.blend.is_active
        and product.pack_size.is_active
    )


def get_available_quantity(product):
    """
    Read actual availability from Nonda's existing inventory ledger.
    This does not maintain a second inventory balance.
    """

    inventory, _ = PackagedInventory.objects.get_or_create(
        product=product
    )

    return max(inventory.available, 0)


def get_cart_items_queryset(cart):
    """Fetch cart data efficiently, including current promotions."""

    now = timezone.now()

    current_promotions = (
        StorePromotion.objects
        .filter(
            is_active=True,
            starts_at__lte=now,
            ends_at__gt=now,
            discounted_price__lt=F(
                "content__product__selling_price"
            ),
        )
        .order_by("-starts_at", "-pk")
    )

    return (
        StoreCartItem.objects
        .filter(cart=cart)
        .select_related(
            "product",
            "product__blend",
            "product__pack_size",
            "product__store_content",
        )
        .prefetch_related(
            Prefetch(
                "product__store_content__promotions",
                queryset=current_promotions,
                to_attr="current_promotions",
            )
        )
    )


def build_cart_summary(cart):
    """Calculate current prices and totals on the server."""

    rows = []
    subtotal = 0
    total_quantity = 0
    has_unavailable_products = False

    from decimal import Decimal

    for item in get_cart_items_queryset(cart):

        product = item.product

        try:
            content = product.store_content
        except StoreProductContent.DoesNotExist:
            content = None

        promotions = (
            getattr(content, "current_promotions", [])
            if content else []
        )

        promotion = promotions[0] if promotions else None

        unit_price = (
            promotion.discounted_price
            if promotion
            else product.selling_price
        )

        line_total = unit_price * item.quantity

        listed = product_is_listed(product)

        if not listed:
            has_unavailable_products = True

        rows.append({
            "item": item,
            "product": product,
            "content": content,
            "promotion": promotion,
            "unit_price": unit_price,
            "line_total": line_total,
            "is_listed": listed,
        })

        subtotal += line_total
        total_quantity += item.quantity

    return {
        "cart_rows": rows,
        "subtotal": subtotal,
        "total_quantity": total_quantity,
        "has_unavailable_products": has_unavailable_products,
    }


@transaction.atomic
def add_product_to_cart(cart, product, quantity):
    """Add packs to a cart, or increase the existing line quantity."""

    if not product_is_listed(product):
        raise CartOperationError(
            "This product is no longer available in the online shop."
        )

    locked_cart = (
        StoreCart.objects
        .select_for_update()
        .get(pk=cart.pk, status=StoreCart.Status.OPEN)
    )

    item = (
        StoreCartItem.objects
        .select_for_update()
        .filter(cart=locked_cart, product=product)
        .first()
    )

    existing_quantity = item.quantity if item else 0
    new_quantity = existing_quantity + quantity

    available = get_available_quantity(product)

    if available < 1:
        raise CartOperationError(
            "Sorry, this product is currently out of stock."
        )

    if new_quantity > available:
        raise CartOperationError(
            f"Only {available} packs are currently available. "
            f"You already have {existing_quantity} in your cart."
        )

    if item:
        item.quantity = new_quantity
        item.save(update_fields=["quantity", "updated_at"])
    else:
        StoreCartItem.objects.create(
            cart=locked_cart,
            product=product,
            quantity=quantity,
        )

    locked_cart.save(update_fields=["updated_at"])

    return locked_cart


@transaction.atomic
def update_cart_item(cart, item_id, quantity):
    """Update one item, ensuring it belongs to this visitor's cart."""

    locked_cart = (
        StoreCart.objects
        .select_for_update()
        .get(pk=cart.pk, status=StoreCart.Status.OPEN)
    )

    item = (
        StoreCartItem.objects
        .select_for_update()
        .select_related(
            "product",
            "product__blend",
            "product__pack_size",
            "product__store_content",
        )
        .get(pk=item_id, cart=locked_cart)
    )

    if not product_is_listed(item.product):
        raise CartOperationError(
            "This product is no longer published. "
            "Please remove it from your cart."
        )

    available = get_available_quantity(item.product)

    if quantity > available:
        raise CartOperationError(
            f"Only {available} packs are currently available."
        )

    item.quantity = quantity
    item.save(update_fields=["quantity", "updated_at"])

    locked_cart.save(update_fields=["updated_at"])

    return locked_cart


@transaction.atomic
def remove_cart_item(cart, item_id):
    """Remove an item belonging to this visitor's cart."""

    locked_cart = (
        StoreCart.objects
        .select_for_update()
        .get(pk=cart.pk, status=StoreCart.Status.OPEN)
    )

    item = StoreCartItem.objects.select_for_update().get(
        pk=item_id,
        cart=locked_cart,
    )

    item.delete()

    locked_cart.save(update_fields=["updated_at"])

    return locked_cart
