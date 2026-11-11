from django.utils import timezone
from django.db import transaction
from django.shortcuts import render
from web.models import PackagedProduct
from django.db.models import F, Prefetch
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from web.models import PackagedProduct, User
from .forms import StoreProductContentForm, StorePromotionForm
from .models import StoreProductContent, StorePromotion
from .forms import StoreHomepageContentForm
from .models import StoreHomepageContent
from django.views.decorators.http import require_POST

from .forms import CartQuantityForm
from .models import StoreCart, StoreCartItem
from .cart_services import (
    get_open_cart,
    sync_cart_count,
    build_cart_summary,
    get_available_quantity,
    product_is_listed,
)



def home(request):
    homepage_content, _ = (
        StoreHomepageContent.objects.get_or_create(
            content_key="home"
        )
    )

    featured_products = (
        published_products()
        .filter(store_content__is_featured=True)[:6]
    )

    return render(
        request,
        "store/home.html",
        {
            "homepage_content": homepage_content,
            "featured_products": featured_products,
        },
    )


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




def active_packaged_products():
    return (
        PackagedProduct.objects
        .filter(
            is_active=True,
            blend__is_active=True,
            pack_size__is_active=True,
        )
        .select_related("blend", "pack_size", "inventory")
        .order_by(
            "blend__name",
            "pack_size__grams",
            "form",
        )
    )


# this is also for customer-facing pages and promotions

def published_products():
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
        active_packaged_products()
        .filter(store_content__is_visible=True)
        .select_related("store_content")
        .prefetch_related(
            Prefetch(
                "store_content__promotions",
                queryset=current_promotions,
                to_attr="current_promotions",
            )
        )
    )

# CUSTOMER-FACING PAGES


def home(request):
    homepage_content, _ = (
        StoreHomepageContent.objects.get_or_create(
            content_key="home"
        )
    )

    featured_products = list(
        published_products()
        .filter(store_content__is_featured=True)
        .order_by(
            "blend__name",
            "pack_size__grams",
            "form",
        )[:6]
    )

    # Use the first featured product that has an image
    # when the manager has not uploaded a separate hero image.
    hero_featured_product = next(
        (
            product for product in featured_products
            if product.store_content.image
        ),
        None,
    )

    return render(
        request,
        "store/home.html",
        {
            "homepage_content": homepage_content,
            "featured_products": featured_products,
            "hero_featured_product": hero_featured_product,
        },
    )



def product_list(request):
    products = published_products()

    return render(
        request,
        "store/products.html",
        {
            "products": products,
        },
    )


def product_detail(request, product_id):
    product = get_object_or_404(
        published_products(),
        pk=product_id,
    )

    return render(
        request,
        "store/product_detail.html",
        {
            "product": product,
        },
    )


# STORE MANAGEMENT PERMISSIONS

def store_manager_required(view_func):
    @wraps(view_func)
    @login_required(login_url="login")
    def wrapper(request, *args, **kwargs):
        user = request.user

        allowed = (
            user.is_superuser
            or user.role in (
                User.Role.ADMIN,
                User.Role.MANAGER,
            )
        )

        if not allowed:
            raise PermissionDenied(
                "You are not authorised to manage the online store."
            )

        return view_func(request, *args, **kwargs)

    return wrapper


# STORE PRODUCT MANAGEMENT

@store_manager_required
def manage_products(request):
    products = active_packaged_products()

    paginator = Paginator(products, 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    product_ids = [
        product.pk for product in page_obj.object_list
    ]

    content_records = StoreProductContent.objects.filter(
        product_id__in=product_ids
    )

    content_by_product = {
        content.product_id: content
        for content in content_records
    }

    product_rows = [
        {
            "product": product,
            "content": content_by_product.get(product.pk),
        }
        for product in page_obj.object_list
    ]

    return render(
        request,
        "store/manage_products.html",
        {
            "product_rows": product_rows,
            "page_obj": page_obj,
        },
    )


@store_manager_required
def edit_product(request, product_id):
    product = get_object_or_404(
        active_packaged_products(),
        pk=product_id,
    )

    # New products from Nonda Pipeline receive a presentation
    # record only when a manager opens them for editing.
    # is_visible remains False by default.
    content, created = StoreProductContent.objects.get_or_create(
        product=product
    )

    if request.method == "POST":
        form = StoreProductContentForm(
            request.POST,
            request.FILES,
            instance=content,
        )

        if form.is_valid():
            content = form.save(commit=False)
            content.updated_by = request.user
            content.save()

            messages.success(
                request,
                "Store product presentation saved successfully.",
            )

            return redirect("store:manage_products")

    else:
        form = StoreProductContentForm(instance=content)

    return render(
        request,
        "store/edit_product.html",
        {
            "product": product,
            "form": form,
        },
    )


@store_manager_required
def manage_promotions(request):
    promotions = (
        StorePromotion.objects
        .select_related(
            "content__product__blend",
            "content__product__pack_size",
            "created_by",
            "updated_by",
        )
        .order_by("-created_at")
    )

    paginator = Paginator(promotions, 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "store/manage_promotions.html",
        {
            "promotions": page_obj.object_list,
            "page_obj": page_obj,
        },
    )


@store_manager_required
def promotion_create(request):
    if request.method == "POST":
        with transaction.atomic():

            content_id = request.POST.get("content", "")

            # Serialise promotion creation for the same product.
            # This helps prevent simultaneous overlapping offers.
            if content_id.isdigit():
                StoreProductContent.objects.select_for_update().filter(
                    pk=int(content_id)
                ).first()

            form = StorePromotionForm(request.POST)

            if form.is_valid():
                promotion = form.save(commit=False)
                promotion.created_by = request.user
                promotion.updated_by = request.user
                promotion.save()

                messages.success(
                    request,
                    "Promotion created successfully.",
                )

                return redirect("store:manage_promotions")

    else:
        form = StorePromotionForm()

    return render(
        request,
        "store/promotion_form.html",
        {
            "form": form,
            "page_heading": "Create promotion",
        },
    )


@store_manager_required
def promotion_edit(request, promotion_id):
    if request.method == "POST":
        with transaction.atomic():

            promotion = get_object_or_404(
                StorePromotion.objects.select_for_update(),
                pk=promotion_id,
            )

            StoreProductContent.objects.select_for_update().get(
                pk=promotion.content_id
            )

            form = StorePromotionForm(
                request.POST,
                instance=promotion,
            )

            if form.is_valid():
                promotion = form.save(commit=False)
                promotion.updated_by = request.user
                promotion.save()

                messages.success(
                    request,
                    "Promotion updated successfully.",
                )

                return redirect("store:manage_promotions")

    else:
        promotion = get_object_or_404(
            StorePromotion.objects.select_related(
                "content__product__blend",
                "content__product__pack_size",
            ),
            pk=promotion_id,
        )

        form = StorePromotionForm(instance=promotion)

    return render(
        request,
        "store/promotion_form.html",
        {
            "form": form,
            "promotion": promotion,
            "page_heading": "Edit promotion",
        },
    )

# changing the content on the home page view here;

@store_manager_required
def manage_homepage(request):
    homepage_content, _ = (
        StoreHomepageContent.objects.get_or_create(
            content_key="home"
        )
    )

    if request.method == "POST":
        with transaction.atomic():
            homepage_content = (
                StoreHomepageContent.objects
                .select_for_update()
                .get(pk=homepage_content.pk)
            )

            form = StoreHomepageContentForm(
                request.POST,
                request.FILES,
                instance=homepage_content,
            )

            if form.is_valid():
                content = form.save(commit=False)
                content.updated_by = request.user
                content.save()

                messages.success(
                    request,
                    "Homepage content updated successfully.",
                )

                return redirect("store:manage_homepage")

    else:
        form = StoreHomepageContentForm(
            instance=homepage_content
        )

    return render(
        request,
        "store/manage_homepage.html",
        {
            "form": form,
            "homepage_content": homepage_content,
        },
    )


# cart views here below;

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
    product = get_object_or_404(
        published_products(),
        pk=product_id,
    )

    form = CartQuantityForm(request.POST)

    if not form.is_valid():
        messages.error(request, "Please enter a valid quantity.")
        return redirect(
            "store:product_detail",
            product_id=product_id,
        )

    quantity_to_add = form.cleaned_data["quantity"]
    cart = get_open_cart(request)

    with transaction.atomic():

        locked_cart = (
            StoreCart.objects
            .select_for_update()
            .get(pk=cart.pk)
        )

        item = (
            StoreCartItem.objects
            .select_for_update()
            .filter(
                cart=locked_cart,
                product=product,
            )
            .first()
        )

        existing_quantity = item.quantity if item else 0
        new_quantity = existing_quantity + quantity_to_add

        available = get_available_quantity(product)

        if available <= 0:
            messages.error(
                request,
                "Sorry, this product is currently out of stock.",
            )

        elif new_quantity > available:
            messages.error(
                request,
                f"Only {available} packs are currently available. "
                f"You already have {existing_quantity} in your cart.",
            )

        else:
            if item:
                item.quantity = new_quantity
                item.save(
                    update_fields=["quantity", "updated_at"]
                )
            else:
                StoreCartItem.objects.create(
                    cart=locked_cart,
                    product=product,
                    quantity=quantity_to_add,
                )

            locked_cart.save(update_fields=["updated_at"])
            sync_cart_count(request, locked_cart)

            messages.success(
                request,
                "Product added to your cart.",
            )

    return redirect("store:cart")


@require_POST
def cart_update(request, item_id):
    cart = get_open_cart(request)

    form = CartQuantityForm(request.POST)

    if not form.is_valid():
        messages.error(request, "Enter a valid quantity.")
        return redirect("store:cart")

    new_quantity = form.cleaned_data["quantity"]

    with transaction.atomic():

        locked_cart = (
            StoreCart.objects
            .select_for_update()
            .get(pk=cart.pk)
        )

        item = get_object_or_404(
            StoreCartItem.objects
            .select_for_update()
            .select_related(
                "product__blend",
                "product__pack_size",
                "product__inventory",
                "product__store_content",
            ),
            pk=item_id,
            cart=locked_cart,
        )

        if not product_is_listed(item.product):
            messages.error(
                request,
                "This product is no longer available in the online shop. "
                "Please remove it from your cart.",
            )

        else:
            available = get_available_quantity(item.product)

            if new_quantity > available:
                messages.error(
                    request,
                    f"Only {available} packs are currently available.",
                )

            else:
                item.quantity = new_quantity
                item.save(
                    update_fields=["quantity", "updated_at"]
                )

                locked_cart.save(update_fields=["updated_at"])

                messages.success(
                    request,
                    "Cart quantity updated.",
                )

        sync_cart_count(request, locked_cart)

    return redirect("store:cart")


@require_POST
def cart_remove(request, item_id):
    cart = get_open_cart(request)

    with transaction.atomic():

        locked_cart = (
            StoreCart.objects
            .select_for_update()
            .get(pk=cart.pk)
        )

        item = get_object_or_404(
            StoreCartItem.objects.select_for_update(),
            pk=item_id,
            cart=locked_cart,
        )

        item.delete()

        locked_cart.save(update_fields=["updated_at"])
        sync_cart_count(request, locked_cart)

    messages.success(request, "Product removed from your cart.")

    return redirect("store:cart")
