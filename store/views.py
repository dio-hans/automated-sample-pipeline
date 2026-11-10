
from django.shortcuts import render
from web.models import PackagedProduct


def home(request):
    products = (
        PackagedProduct.objects
        .filter(
            is_active=True,
            blend__is_active=True,
            pack_size__is_active=True,
        )
        .select_related("blend", "pack_size")
        .order_by("blend__name", "pack_size__grams", "form")
    )

    return render(request, "store/home.html", {
        "featured_products": products[:6],
    })


def product_list(request):
    products = (
        PackagedProduct.objects
        .filter(
            is_active=True,
            blend__is_active=True,
            pack_size__is_active=True,
        )
        .select_related("blend", "pack_size")
        .order_by("blend__name", "pack_size__grams", "form")
    )

    return render(request, "store/products.html", {
        "products": products,
    })
