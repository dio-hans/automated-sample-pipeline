
def cart_summary(request):
    return {
        "store_cart_count": request.session.get(
            "store_cart_quantity_count",
            0,
        )
    }
