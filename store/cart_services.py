from web.models import PackagedInventory
from decimal import Decimal
from django.db.models import F, Prefetch, Sum
from django.utils import timezone

from .models import StoreCart, StoreCartItem, StorePromotion


def get_open_cart(request):
    """
    Get or create the open cart associated with this visitor's
    Django session.
    """

    if not request.session.session_key:
        request.session.create()

    cart, _ = StoreCart.objects.get_or_create(
        session_key=request.session.session_key,
        status=StoreCart.Status.OPEN,
    )

    return cart


def sync_cart_count(request, cart):
    """
    Store a lightweight item count in the session for the header.
    Cart contents themselves remain in the database.
    """

    count = (
        cart.items.aggregate(total=Sum("quantity"))["total"]
        or 0
    )

    request.session["store_cart_quantity_count"] = count

    return count


def cart_items_queryset(cart):
    """
    Retrieve cart items and current promotions efficiently.
    """

    now = timezone.now()

    active_promotions = (
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
                queryset=active_promotions,
                to_attr="current_promotions",
            )
        )
    )


def build_cart_summary(cart):
    """
    Calculate every line total and the cart subtotal on the
    server. Never use a price submitted by the browser.
    """

    rows = []
    subtotal = Decimal("0.00")
    total_quantity = 0
    has_unavailable_products = False

    for item in cart_items_queryset(cart):

        product = item.product
        content = getattr(product, "store_content", None)

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

        is_listed = bool(
            content
            and content.is_visible
            and product.is_active
            and product.blend.is_active
            and product.pack_size.is_active
        )

        if not is_listed:
            has_unavailable_products = True

        subtotal += line_total
        total_quantity += item.quantity

        rows.append({
            "item": item,
            "product": product,
            "content": content,
            "promotion": promotion,
            "unit_price": unit_price,
            "line_total": line_total,
            "is_listed": is_listed,
        })

    return {
        "cart_rows": rows,
        "subtotal": subtotal,
        "total_quantity": total_quantity,
        "has_unavailable_products": has_unavailable_products,
    }


def get_available_quantity(product):
    """
    Read availability from Nonda's existing packaged inventory.
    The cart does not create or maintain separate stock records.
    """

    try:
        return max(product.inventory.available, 0)
    except PackagedInventory.DoesNotExist:
        return 0


def product_is_listed(product):
    content = getattr(product, "store_content", None)

    return bool(
        content
        and content.is_visible
        and product.is_active
        and product.blend.is_active
        and product.pack_size.is_active
    )