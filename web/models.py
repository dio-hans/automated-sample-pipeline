from decimal import Decimal
import uuid
from django.db import models
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db.models import Sum
from django.contrib.auth.models import AbstractUser


# --- 1. COMPANY DETAILS ---

class Coffee_type(models.TextChoices):
    ARABICA = 'arabica', 'Arabica'
    ROBUSTA = 'robusta', 'Robusta'


class Company(models.Model):
    name = models.CharField(max_length=250, unique=True)
    country = models.CharField(max_length=250, unique=False)
    city = models.CharField(max_length=100)
    contact_person = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20, unique=True)
    address = models.TextField()
    is_acquired_client = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    pipeline_stage = models.CharField(
        max_length=50, 
        choices=[
            ('new_lead', 'New Lead'),
            ('sample_sent', 'Sample Sent'),
            ('sample_received', 'Sample Received'),
            ('followup', 'Follow-up'),
            ('negotiation', 'Negotiation'),
            ('contract_signed', 'Contract Signed'),
            ('recurring_supply', 'Recurring Supply'),
            ('lost', 'Lost'),
        ],
        default='new_lead'
    )

    def __str__(self):
        return self.name


# --- 2. INVENTORY & STOCK ---

class CoffeeVariety(models.Model):
    name = models.CharField(max_length=150)

    default_coffee_type = models.CharField(
        max_length=20,
        choices=Coffee_type.choices
    )
    default_grade = models.CharField(max_length=50, blank=True)
    default_source = models.CharField(max_length=250, blank=True)
    default_process = models.CharField(max_length=50, blank=True)
    default_foreign_smell = models.CharField(
        max_length=50,
        default="None",
        blank=True
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    
class CoffeeStock(models.Model):
    STAGE_CHOICES = (
        ('green_received', 'Green Bean Received'),
        ('stored', 'Stored'),
        ('roasted', 'Roasted'),
        ('ground', 'Ground'),
        ('packaged', 'Packaged'),
    )

    batch_number = models.CharField(
        max_length=50,
        unique=True,
        blank=True
    )
    variety = models.ForeignKey(
        CoffeeVariety,
        on_delete=models.PROTECT,
        related_name="stock_batches"
    )
    coffee_type = models.CharField(
        max_length=20,
        choices=Coffee_type.choices
    )
    received_date = models.DateField(null=True, blank=True)
    supplier = models.CharField(max_length=250, blank=True)
    source = models.CharField(max_length=250, blank=True)
    grade = models.CharField(max_length=50, blank=True)
    moisture_content = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True
    )
    process = models.CharField(max_length=50, blank=True)
    season_of_harvest = models.CharField(max_length=100, blank=True)
    quantity_sorted_out = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True
    )
    foreign_smell = models.CharField(max_length=50, blank=True)
    foreign_matter = models.CharField(max_length=50, blank=True)
    prints = models.CharField(max_length=50, blank=True)
    physical_damages = models.CharField(max_length=10, blank=True)
    defects = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True
    )
    fermentation_type = models.CharField(max_length=50, blank=True)
    quantity_after_sorting = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True
    )

    checked_by = models.CharField(max_length=150, blank=True)
    verified_by = models.CharField(max_length=250, blank=True)
    delivered_by = models.CharField(max_length=150, blank=True)
    car_number = models.CharField(max_length=50, blank=True)
    received_by = models.CharField(max_length=150, blank=True)

    stage = models.CharField(
        max_length=30,
        choices=STAGE_CHOICES,
        default='green_received'
    )
    reorder_level = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=50
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def quantity_received(self):
        return self.movements.filter(
            movement_type='receipt'
        ).aggregate(
            total=Sum('quantity')
        )['total'] or Decimal('0.00')

    @property
    def variety_name(self):
        return self.variety.name

    def stage_quantity(self, stage):
        incoming = self.movements.filter(
            to_stage=stage
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0.00')

        outgoing = self.movements.filter(
            from_stage=stage
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0.00')

        return incoming - outgoing

    @property
    def quantity_available(self):
        incoming = self.movements.exclude(
            to_stage=None
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0.00')

        outgoing = self.movements.exclude(
            from_stage=None
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0.00')

        return incoming - outgoing

    @property
    def quantity_green(self):
        return self.stage_quantity('green_received')

    @property
    def quantity_roasted(self):
        return self.stage_quantity('roasted')

    @property
    def quantity_ground(self):
        return self.stage_quantity('ground')

    @property
    def quantity_packaged(self):
        return self.stage_quantity('packaged')

    @property
    def roast_date(self):
        movement = self.movements.filter(
            to_stage='roasted'
        ).order_by('created_at').first()
        return movement.created_at if movement else None

    @property
    def is_low_stock(self):
        return 0 < self.quantity_available <= self.reorder_level


    def __str__(self):
        return f"{self.batch_number} - {self.variety.name}"


class StockMovement(models.Model):
    MOVEMENT_TYPES = (
        ("receipt", "Stock Received"),
        ("sample", "Sample Taken"),
        ("roast_input", "Sent for Roasting"),
        ("roast_output", "Roasted Output"),
        ("grind_input", "Sent for Grinding"),
        ("grind_output", "Ground Output"),
        ("dispatch", "Dispatched"),
        ("loss", "Loss / Waste"),
        ("adjustment", "Inventory Adjustment"),
    )

    STAGE_CHOICES = CoffeeStock.STAGE_CHOICES

    stock = models.ForeignKey(
        CoffeeStock,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    movement_type = models.CharField(
        max_length=30,
        choices=MOVEMENT_TYPES,
    )
    from_stage = models.CharField(
        max_length=30,
        choices=STAGE_CHOICES,
        null=True,
        blank=True,
    )
    to_stage = models.CharField(
        max_length=30,
        choices=STAGE_CHOICES,
        null=True,
        blank=True,
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.stock.batch_number} - {self.get_movement_type_display()} - {self.quantity} kg"


# --- 3. SAMPLES & CRM ---

delivery_status_choices = [
    ('in_transit', 'In Transit'),
    ('delivered', 'Delivered'),
    ('failed', 'Failed')
]

class Sample(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    coffee_stock = models.ForeignKey(CoffeeStock, on_delete=models.RESTRICT)
    sample_weight = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    date_sent = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_status = models.CharField(
        max_length=30,
        choices=delivery_status_choices,
        default='in_transit'
    )
    courier_name = models.CharField(max_length=50, default='DHL')
    tracking_number = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.company.name} - {self.coffee_stock.variety.name} - {self.sample_weight}kg"


class Followup(models.Model):
    sample = models.OneToOneField(Sample, on_delete=models.CASCADE)
    dispatch_alert_sent_at = models.DateTimeField(null=True, blank=True)
    day_3_guide_sent_at = models.DateTimeField(null=True, blank=True)
    day_7_contract_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_converted_to_contract = models.BooleanField(default=False)

    def __str__(self):
        return f"Followup for Sample ID: {self.sample.id}"


class SampleFeedback(models.Model):
    sample = models.OneToOneField(Sample, on_delete=models.CASCADE)
    comments = models.TextField()
    rating = models.PositiveSmallIntegerField()
    interested_in_contract = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Feedback for Sample ID: {self.sample.id} - Rating: {self.rating}"


contract_signing_status_choices = [
    ('pending', 'Pending'),
    ('signed', 'Signed'),
    ('declined', 'Declined')
]

class Contract(models.Model):
    id = models.UUIDField(primary_key=True, editable=False, default=uuid.uuid4)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    sample = models.ForeignKey(Sample, on_delete=models.SET_NULL, null=True)
    volume_per_cycle_kg = models.DecimalField(max_digits=8, decimal_places=2)
    price_per_kg = models.DecimalField(max_digits=8, decimal_places=2)
    delivery_frequency = models.CharField(
        max_length=20, 
        choices=[
            ('daily', 'Daily'),
            ('weekly', 'Weekly'),
            ('monthly', 'Monthly'),  # Fixed spelling
        ]
    )     
    status = models.CharField(
        max_length=20,
        choices=contract_signing_status_choices,
        default='pending'
    )
    signed_name = models.CharField(max_length=150, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    next_delivery_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"Contract for {self.company.name} - {self.volume_per_cycle_kg}kg"


class SupplyFulfillment(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE)
    coffee_stock = models.ForeignKey(CoffeeStock, on_delete=models.RESTRICT)
    volume_shipped_kg = models.DecimalField(max_digits=8, decimal_places=2)
    shipped_date = models.DateField(auto_now_add=True)
    invoice_number = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return f"Supply Fulfillment for Contract ID: {self.contract.id} - {self.volume_shipped_kg}kg"


# --- 4. AUTHENTICATION & AUDITING ---

class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'System Admin'
        SALES = 'SALES', 'Sales Attendant'
        MANAGER = 'MANAGER', 'Store Manager'
        ACCOUNTS = 'ACCOUNTS', 'Accounts'

    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.SALES,
        db_index=True
    )
    contact = models.CharField(
        max_length=20, 
        unique=True, 
        blank=True, 
        null=True,
        help_text="Phone contact format: +256700000000"
    )
    employee_id = models.CharField(
        max_length=30, 
        unique=True, 
        blank=True, 
        null=True,
        help_text="Unique tracking code"
    )

    def __str__(self):
        return f"{self.username} - {self.get_role_display()}"

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def is_manager(self):
        return self.role == self.Role.MANAGER

    @property
    def is_sales(self):
        return self.role == self.Role.SALES

    @property
    def is_accounts(self):
        return self.role == self.Role.ACCOUNTS


class AuditLog(models.Model):
    ACTION_CHOICES = (
        ("create", "Created"),
        ("update", "Updated"),
        ("delete", "Deleted"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE
    )
    object_id = models.CharField(max_length=100)
    content_object = GenericForeignKey(
        "content_type",
        "object_id"
    )
    changes = models.JSONField(
        default=dict,
        blank=True
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        username = self.user.username if self.user else "System"
        return f"{username} - {self.action} - {self.content_type.model} - {self.timestamp}"

"""
Packaged Inventory Models — Nonda Commodities

This module adds the packaged-product layer on top of the existing
green → roasted kg-based ledger. The flow is:

    Green beans (kg)
        ↓ roast
    Roasted beans (kg)
        ↓ grind (optional)
    Ground coffee (kg)          Roasted beans (kg)
        ↓ package                   ↓ package
    Packaged ground (packs)    Packaged beans (packs)
        ↓                            ↓
         Released to sales ← → Returns

A PackagingRun converts roasted (or ground) kg into packs.
A PackRelease sends packs to a salesperson.
A PackReturn records packs coming back.

Add these to your models.py (or import from here).
"""

# 1. BLEND  (kitiko & mulondo, nyanja, tendo, ntanda31, + future)
class Blend(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

# 2. PACK SIZE  (50g, 250g, 500g, 1kg, sachet)
class PackSize(models.Model):
    GRAM_CHOICES = [
        (50, "50g"),
        (250, "250g"),
        (500, "500g"),
        (1000, "1kg"),
        (15, "Sachet (15g)"),  # sachets are beans-only, small
    ]
    grams = models.PositiveIntegerField(unique=True, choices=GRAM_CHOICES)
    label = models.CharField(max_length=30)  # "50g", "250g", etc.
    is_sachet = models.BooleanField(default=False)  # sachets = beans only
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["grams"]

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if self.grams == 15:
            self.is_sachet = True
        super().save(*args, **kwargs)


# 3. PRODUCT FORM  (beans vs ground)
class ProductForm(models.TextChoices):
    BEANS = "beans", "Beans"
    GROUND = "ground", "Ground"


# 4. PACKAGED PRODUCT  (blend × pack_size × form)
#    This is the sellable SKU.
class PackagedProduct(models.Model):
    blend = models.ForeignKey(Blend, on_delete=models.PROTECT, related_name="products")
    pack_size = models.ForeignKey(PackSize, on_delete=models.PROTECT, related_name="products")
    form = models.CharField(max_length=10, choices=ProductForm.choices)

    # Auto-calculated: how many kg of roasted coffee one pack consumes
    # (pack_size.grams / 1000). Set on save for quick reference.
    kg_per_pack = models.DecimalField(max_digits=8, decimal_places=4, default=0)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("blend", "pack_size", "form")
        ordering = ["blend__name", "pack_size__grams", "form"]

    def __str__(self):
        return f"{self.blend.name} {self.pack_size.label} ({self.form})"

    def save(self, *args, **kwargs):
        self.kg_per_pack = Decimal(self.pack_size.grams) / Decimal(1000)
        super().save(*args, **kwargs)


# 5. PACKAGING RUN
#    Converts roasted (or ground) kg → packs.
#    Draws from the CoffeeStock ledger's roasted/ground stage.
class PackagingRun(models.Model):
    stock = models.ForeignKey(
        "CoffeeStock",
        on_delete=models.PROTECT,
        related_name="packaging_runs",
    )
    product = models.ForeignKey(
        PackagedProduct,
        on_delete=models.PROTECT,
        related_name="packaging_runs",
    )

    # How many kg of roasted/ground coffee went IN
    input_kg = models.DecimalField(max_digits=10, decimal_places=2)
    # How many packs came OUT
    packs_produced = models.PositiveIntegerField()
    # Loss / waste (input_kg - packs_produced × kg_per_pack)
    loss_kg = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Source stage: 'roasted' for beans, 'ground' for ground coffee
    source_stage = models.CharField(max_length=20, default="roasted")

    run_date = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="packaging_runs",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-run_date"]

    def __str__(self):
        return f"{self.product} × {self.packs_produced} packs ({self.run_date:%d %b %Y})"


# 6. PACKAGED INVENTORY  (live stock per product)
#    packs_in - packs_released + packs_returned
class PackagedInventory(models.Model):
    product = models.ForeignKey(
        PackagedProduct,
        on_delete=models.PROTECT,
        related_name="inventory",
    )
    packs_in_stock = models.PositiveIntegerField(default=0)
    packs_released = models.PositiveIntegerField(default=0)
    packs_returned = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("product",)

    def __str__(self):
        return f"{self.product}: {self.available} packs available"

    @property
    def available(self):
        return self.packs_in_stock - self.packs_released + self.packs_returned


# 7. PACK RELEASE  (store keeper → salesperson)
class PackRelease(models.Model):
    STATUS_CHOICES = [
        ("released", "Released"),
        ("partially_returned", "Partially Returned"),
        ("fully_returned", "Fully Returned"),
        ("sold", "Sold"),
    ]

    product = models.ForeignKey(
        PackagedProduct,
        on_delete=models.PROTECT,
        related_name="releases",
    )
    # Who requested / received the packs (salesperson)
    released_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="pack_releases",
    )
    packs_out = models.PositiveIntegerField()
    packs_returned = models.PositiveIntegerField(default=0)
    packs_sold = models.PositiveIntegerField(default=0)  # confirmed sold

    # Money: packs_sold × price_per_pack
    price_per_pack = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
    )

    status = models.CharField(
        max_length=25, choices=STATUS_CHOICES, default="released",
    )
    released_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="pack_releases_created",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-released_at"]

    def __str__(self):
        return f"{self.product} × {self.packs_out} → {self.released_to}"

    @property
    def packs_outstanding(self):
        return self.packs_out - self.packs_returned - self.packs_sold

    @property
    def amount_due(self):
        return self.packs_sold * self.price_per_pack


# 8. PACK RETURN  (salesperson → store)
class PackReturn(models.Model):
    release = models.ForeignKey(
        PackRelease,
        on_delete=models.PROTECT,
        related_name="returns",
    )
    packs_returned = models.PositiveIntegerField()
    reason = models.CharField(max_length=200, blank=True)
    returned_at = models.DateTimeField(auto_now_add=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="pack_returns_received",
    )

    class Meta:
        ordering = ["-returned_at"]

    def __str__(self):
        return f"{self.packs_returned} packs returned from {self.release}"


# 9. ROASTED SACK SALE  (occasional bulk roasted sale)
class RoastedSackSale(models.Model):
    stock = models.ForeignKey(
        "CoffeeStock",
        on_delete=models.PROTECT,
        related_name="sack_sales",
    )
    buyer_name = models.CharField(max_length=200)  # could link to Company
    kg_sold = models.DecimalField(max_digits=10, decimal_places=2)
    price_per_kg = models.DecimalField(max_digits=10, decimal_places=2)
    sale_date = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="roasted_sack_sales",
    )

    class Meta:
        ordering = ["-sale_date"]

    def __str__(self):
        return f"{self.kg_sold} kg roasted → {self.buyer_name}"

    @property
    def total_amount(self):
        return self.kg_sold * self.price_per_kg