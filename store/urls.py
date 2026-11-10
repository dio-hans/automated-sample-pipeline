
from django.urls import path

from . import views
from . import cart_views

app_name = "store"

urlpatterns = [
    path("", views.home, name="home"),
    path("products/", views.product_list, name="products"),

    # Store management
    path("manage/", views.manage_products, name="manage_products"),
    path("manage/product/<int:product_id>/",views.edit_product, name="edit_product"),
    path("manage/promotions/",views.manage_promotions,name="manage_promotions",),
    path("manage/promotions/new/",views.promotion_create,name="promotion_create",),
    path("manage/promotions/<int:promotion_id>/edit/",views.promotion_edit,name="promotion_edit",),

    # this is to edit the home page content
    path("manage/homepage/",views.manage_homepage,name="manage_homepage",),

    # this is the custmer facing home page
    # Product detail view
    path("products/<int:product_id>/",views.product_detail, name="product_detail",),

    # cart urls

    path("cart/", views.cart_view, name="cart"),

    path("cart/add/<int:product_id>/",views.cart_add,name="cart_add", ),

    path("cart/item/<int:item_id>/update/",views.cart_update,name="cart_update",),

    path("cart/item/<int:item_id>/remove/",views.cart_remove,name="cart_remove",),

    path("cart/", cart_views.cart_view, name="cart"),

    path("cart/add/<int:product_id>/",cart_views.cart_add,name="cart_add",),

    path("cart/item/<int:item_id>/update/",cart_views.cart_update,name="cart_update", ),

    path("cart/item/<int:item_id>/remove/",cart_views.cart_remove,name="cart_remove",),

]
