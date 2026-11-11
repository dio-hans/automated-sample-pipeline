
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .cart_services import (
    CartOperationError,
    add_product_to_cart,
    build_cart_summary,
    get_open_cart,
    remove_cart_item,
    sync_cart_count,
    update_cart_item,
)
from .forms import CartQuantityForm
from .models import StoreCartItem
from .views import published_products


def cart_view(request):
    cart = get_open_cart(request)

    summary = build_cart_summary(cart)
    sync_cart_count(request, cart)

    return render(
        request,
        "store/cart.html",
        {
            "cart": cart,
            **summary,
        },
    )


@require_POST
def cart_add(request, product_id):
    form = CartQuantityForm(request.POST)

    if not form.is_valid():
        messages.error(request, "Please enter a valid quantity.")
        return redirect(
            "store:product_detail",
            product_id=product_id,
        )

    product = get_object_or_404(
        published_products(),
        pk=product_id,
    )

    cart = get_open_cart(request)

    try:
        cart = add_product_to_cart(
            cart,
            product,
            form.cleaned_data["quantity"],
        )

        sync_cart_count(request, cart)

        messages.success(request, "Coffee added to your cart.")

    except CartOperationError as exc:
        messages.error(request, str(exc))

    return redirect("store:cart")


@require_POST
def cart_update(request, item_id):
    form = CartQuantityForm(request.POST)

    if not form.is_valid():
        messages.error(request, "Please enter a valid quantity.")
        return redirect("store:cart")

    cart = get_open_cart(request)

    try:
        cart = update_cart_item(
            cart,
            item_id,
            form.cleaned_data["quantity"],
        )

        messages.success(request, "Cart updated successfully.")

    except StoreCartItem.DoesNotExist as exc:
        raise Http404("Cart item not found.") from exc

    except CartOperationError as exc:
        messages.error(request, str(exc))

    sync_cart_count(request, cart)

    return redirect("store:cart")


@require_POST
def cart_remove(request, item_id):
    cart = get_open_cart(request)

    try:
        cart = remove_cart_item(cart, item_id)

        messages.success(
            request,
            "Product removed from your cart.",
        )

    except StoreCartItem.DoesNotExist as exc:
        raise Http404("Cart item not found.") from exc

    sync_cart_count(request, cart)

    return redirect("store:cart")
