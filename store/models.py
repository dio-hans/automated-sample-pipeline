from django.conf import settings
from django.db import models
from web.models import PackagedProduct
from django.core.exceptions import ValidationError
from django.utils import timezone


class StoreProductContent(models.Model):
    """
    Customer-facing presentation settings for an existing
    Nonda PackagedProduct.

    Does not duplicate product, pricing or inventory data.
    """

    product = models.OneToOneField(
        PackagedProduct,
        on_delete=models.CASCADE,
        related_name="store_content",
    )

    display_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="Optional customer-facing name.",
    )

    description = models.TextField(
        blank=True,
        help_text="Description displayed in the online shop.",
    )

    image = models.ImageField(
        upload_to="store/products/%Y/%m/",
        blank=True,
        null=True,
    )

    is_featured = models.BooleanField(default=False)

    is_visible = models.BooleanField(
        default=False,
        help_text="Publish this product on the customer storefront.",
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="store_content_updates",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "product__blend__name",
            "product__pack_size__grams",
        ]

        verbose_name = "Store Product Content"
        verbose_name_plural = "Store Product Content"

    def __str__(self):
        return f"Store presentation: {self.product}"


# promotions and discounts here

class StorePromotion(models.Model):
    """
    A time-limited promotional price for an existing
    product presentation.

    The original price remains in PackagedProduct.
    """

    content = models.ForeignKey(
        StoreProductContent,
        on_delete=models.CASCADE,
        related_name="promotions",
    )

    discounted_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()

    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="store_promotions_created",
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="store_promotions_updated",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["content", "is_active"],
                name="storepromo_content_active",
            ),
            models.Index(
                fields=["is_active", "starts_at", "ends_at"],
                name="storepromo_window_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="storepromo_valid_window",
            ),
            models.CheckConstraint(
                condition=models.Q(discounted_price__gt=0),
                name="storepromo_positive_price",
            ),
        ]

    def clean(self):
        errors = {}

        if (
            self.starts_at
            and self.ends_at
            and self.ends_at <= self.starts_at
        ):
            errors["ends_at"] = (
                "The end date must be later than the start date."
            )

        if self.content_id and self.discounted_price is not None:
            original_price = self.content.product.selling_price

            if self.discounted_price >= original_price:
                errors["discounted_price"] = (
                    "The promotional price must be lower than "
                    "the original product price."
                )

        if (
            self.is_active
            and self.content_id
            and self.starts_at
            and self.ends_at
        ):
            overlapping = StorePromotion.objects.filter(
                content_id=self.content_id,
                is_active=True,
                starts_at__lt=self.ends_at,
                ends_at__gt=self.starts_at,
            )

            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)

            if overlapping.exists():
                errors["starts_at"] = (
                    "This product already has another enabled "
                    "promotion that overlaps these dates."
                )

        if errors:
            raise ValidationError(errors)

    @property
    def status(self):
        now = timezone.now()

        if not self.is_active:
            return "Disabled"

        if self.discounted_price >= self.content.product.selling_price:
            return "Price needs review"

        if now < self.starts_at:
            return "Scheduled"

        if now >= self.ends_at:
            return "Expired"

        return "Live"

    def __str__(self):
        return (
            f"Promotion: {self.content.product} "
            f"at UGX {self.discounted_price}"
        )



class StoreHomepageContent(models.Model):
    """
    Editable customer-facing homepage content.

    One record is maintained for the Nonda homepage.
    """

    content_key = models.CharField(
        max_length=20,
        unique=True,
        default="home",
        editable=False,
    )

    # Main hero banner
    hero_eyebrow = models.CharField(
        max_length=150,
        default="ROOTED IN AFRICA. MADE TO BE SHARED.",
    )
    hero_title_line = models.CharField(
        max_length=150,
        default="Make room for",
    )
    hero_title_emphasis = models.CharField(
        max_length=150,
        default="good coffee.",
    )
    hero_description = models.TextField(
        default=(
            "Discover Nonda's carefully crafted coffee blends. "
            "Find your favourite, choose your pack and enjoy "
            "good coffee wherever life takes you."
        )
    )
    hero_image = models.ImageField(
        upload_to="store/pages/home/%Y/%m/",
        blank=True,
        null=True,
    )
    hero_image_alt = models.CharField(
        max_length=200,
        default="Nonda Coffee collection",
    )
    hero_visual_label = models.CharField(
        max_length=100,
        default="THE NONDA COLLECTION",
    )
    hero_visual_reference = models.CharField(
        max_length=50,
        default="01 / COFFEE",
    )

    # Intro strip
    intro_text = models.CharField(
        max_length=200,
        default="Good coffee is worth sharing.",
    )
    intro_subtext = models.CharField(
        max_length=200,
        default="Discover the Nonda collection",
    )

    # Featured products heading
    collection_eyebrow = models.CharField(
        max_length=100,
        default="THE COLLECTION",
    )
    collection_title = models.CharField(
        max_length=200,
        default="Find your everyday coffee.",
    )

    # Our Story
    story_eyebrow = models.CharField(
        max_length=100,
        default="THE NONDA WAY",
    )
    story_title_line = models.CharField(
        max_length=150,
        default="More than coffee.",
    )
    story_title_emphasis = models.CharField(
        max_length=150,
        default="A moment worth keeping.",
    )
    story_description = models.TextField(
        default=(
            "Every blend has a story. We bring carefully selected "
            "coffee and thoughtful craftsmanship together to make "
            "every cup worth enjoying."
        )
    )

    # Find Nonda
    find_eyebrow = models.CharField(
        max_length=100,
        default="FIND NONDA",
    )
    find_title = models.CharField(
        max_length=200,
        default="Your favourite coffee could be closer than you think.",
    )
    find_description = models.TextField(
        default=(
            "Discover supermarkets and stores stocking Nonda coffee. "
            "Find a branch near you and get your favourite blend."
        )
    )


    # Hero call-to-action
    hero_button_text = models.CharField(
        max_length=80,
        default="Explore our coffee",
    )

    # Featured collection link
    collection_link_text = models.CharField(
        max_length=80,
        default="View all products",
    )

    # Why Nonda section
    why_eyebrow = models.CharField(
        max_length=100,
        default="WHY NONDA",
    )

    why_title = models.CharField(
        max_length=200,
        default="Coffee for the moments that bring us together.",
    )

    why_description = models.TextField(
        default=(
            "Find a Nonda coffee to suit your taste, your routine "
            "and the moments you enjoy sharing."
        ),
    )

    why_first_title = models.CharField(
        max_length=100,
        default="Find your preferred coffee",
    )

    why_first_description = models.TextField(
        default=(
            "Explore our available blends, coffee formats "
            "and pack sizes."
        ),
    )

    why_second_title = models.CharField(
        max_length=100,
        default="Made for everyday moments",
    )

    why_second_description = models.TextField(
        default=(
            "Bring Nonda into your morning routine, your working day "
            "and the moments in between."
        ),
    )

    why_third_title = models.CharField(
        max_length=100,
        default="Good coffee, shared",
    )

    why_third_description = models.TextField(
        default=(
            "Enjoy your favourite Nonda coffee with family, "
            "friends and colleagues."
        ),
    )


    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="store_homepage_updates",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Store Homepage Content"
        verbose_name_plural = "Store Homepage Content"

    def __str__(self):
        return "Nonda Homepage Content"



class StoreCart(models.Model):

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CONVERTED = "converted", "Converted to order"

    session_key = models.CharField(
        max_length=40,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["session_key"],
                condition=models.Q(status="open"),
                name="store_one_open_cart_per_session",
            ),
        ]

    def __str__(self):
        return f"Cart {self.pk} ({self.status})"


class StoreCartItem(models.Model):

    cart = models.ForeignKey(
        StoreCart,
        on_delete=models.CASCADE,
        related_name="items",
    )

    product = models.ForeignKey(
        PackagedProduct,
        on_delete=models.PROTECT,
        related_name="store_cart_items",
    )

    quantity = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "product"],
                name="store_cart_unique_product",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="store_cart_quantity_at_least_one",
            ),
        ]

    def __str__(self):
        return f"{self.product} × {self.quantity}"
