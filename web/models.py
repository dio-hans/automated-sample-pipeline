from django.core.exceptions import ValidationError
from decimal import Decimal
from django.utils import timezone
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

class StockStage(models.TextChoices):
        GREEN = "green_received", "Green Coffee"
        ROASTED = "roasted", "Roasted Coffee"
        GROUND = "ground", "Ground Coffee"
        QUAKERS = "quakers", "Quakers"

class FermentationType(models.TextChoices):
    AEROBIC = "aerobic_fermentation", "Aerobic Fermentation"
    ANAEROBIC = "anaerobic_fermentation", "Anaerobic Fermentation"    
    NATURAL = "natural", "Natural"
    OTHER = "other", "Other"

    
class CoffeeStock(models.Model):

    batch_number = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
    )

    variety = models.ForeignKey(
        CoffeeVariety,
        on_delete=models.PROTECT,
        related_name="stock_batches",
    )

    coffee_type = models.CharField(
        max_length=20,
        choices=Coffee_type.choices,
    )

    

    received_date = models.DateField(
        null=True,
        blank=True,
    )

    supplier = models.CharField(
        max_length=250,
        blank=True,
    )

    source = models.CharField(
        max_length=250,
        blank=True,
    )

    grade = models.CharField(
        max_length=50,
        blank=True,
    )

    moisture_content = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )

    process = models.CharField(
        max_length=50,
        blank=True,
    )

    season_of_harvest = models.CharField(
        max_length=100,
        blank=True,
    )

    quantity_sorted_out = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    foreign_smell = models.CharField(
        max_length=50,
        blank=True,
    )

    foreign_matter = models.CharField(
        max_length=50,
        blank=True,
    )

    prints = models.CharField(
        max_length=50,
        blank=True,
    )

    physical_damages = models.CharField(
        max_length=50,
        blank=True,
    )

    fermentation_type = models.CharField(
        max_length=50,
        choices=FermentationType.choices,
        default=FermentationType.AEROBIC,
        blank=True
    )

    defects = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    quantity_after_sorting = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    checked_by = models.CharField(
        max_length=150,
        blank=True,
    )

    verified_by = models.CharField(
        max_length=250,
        blank=True,
    )

    delivered_by = models.CharField(
        max_length=150,
        blank=True,
    )

    car_number = models.CharField(
        max_length=50,
        blank=True,
    )

    received_by = models.CharField(
        max_length=150,
        blank=True,
    )

    reorder_level = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=50,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    # --------------------------------------------------
    # INVENTORY CALCULATIONS
    # --------------------------------------------------

    @property
    def quantity_received(self):
        return (
            self.movements
            .filter(movement_type="receipt")
            .aggregate(total=Sum("quantity"))["total"]
            or Decimal("0.00")
        )

    @property
    def variety_name(self):
        return self.variety.name

    def stage_quantity(self, stage):
        incoming = (
            self.movements
            .filter(to_stage=stage)
            .aggregate(total=Sum("quantity"))["total"]
            or Decimal("0.00")
        )

        outgoing = (
            self.movements
            .filter(from_stage=stage)
            .aggregate(total=Sum("quantity"))["total"]
            or Decimal("0.00")
        )

        return incoming - outgoing

    @property
    def quantity_green(self):
        return self.stage_quantity(
            StockStage.GREEN
        )

    @property
    def quantity_roasted(self):
        return self.stage_quantity(
            StockStage.ROASTED
        )

    @property
    def quantity_ground(self):
        return self.stage_quantity(
            StockStage.GROUND
        )

    @property
    def quantity_quakers(self):
        return self.stage_quantity(
            StockStage.QUAKERS
        )

    @property
    def quantity_available(self):
        """
        Total kg currently inside the kg-based inventory.

        This intentionally excludes packaged coffee because
        packaged coffee is tracked in PACKS.
        """

        return (
            self.quantity_green
            + self.quantity_roasted
            + self.quantity_ground
            + self.quantity_quakers
        )

    @property
    def is_low_stock(self):
        return (
            0 < self.quantity_available <= self.reorder_level
        )

    def __str__(self):
        return f"{self.batch_number} - {self.variety.name}"


class StockMovement(models.Model):

    MOVEMENT_TYPES = (
    ("receipt", "Stock Received"),
    ("sample", "Sample Taken"),

    ("roast_issue", "Issued for Roasting"),
    ("roast_return", "Roasted Coffee Received"),

    ("sort_input", "Issued for Sorting"),
    ("sort_good_output", "Good Coffee Received"),
    ("quaker_output", "Quakers Received"),

    ("grind_input", "Issued for Grinding"),
    ("grind_return", "Ground Coffee Received"),

    ("packaging_issue", "Issued for Packaging"),

    ("dispatch", "Dispatched"),
    ("loss", "Processing Loss"),
    ("adjustment", "Inventory Adjustment"),
)
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
        choices=StockStage.choices,
        null=True,
        blank=True,
    )

    to_stage = models.CharField(
        max_length=30,
        choices=StockStage.choices,
        null=True,
        blank=True,
    )

    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    reference = models.CharField(
        max_length=100,
        blank=True,
    )

    notes = models.TextField(
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"{self.stock.batch_number} - "
            f"{self.get_movement_type_display()} - "
            f"{self.quantity} kg"
        )

class ProcessingRun(models.Model):

    PROCESS_TYPES = (
        ("roasting", "Roasting"),
        ("sorting", "Sorting"),
        ("grinding", "Grinding"),
    )

    STATUS_CHOICES = (
        ("open", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    )

    stock = models.ForeignKey(
        CoffeeStock,
        on_delete=models.PROTECT,
        related_name="processing_runs",
    )

    process_type = models.CharField(
        max_length=20,
        choices=PROCESS_TYPES,
    )

    input_stage = models.CharField(
        max_length=30,
        choices=StockStage.choices,
    )

    output_stage = models.CharField(
        max_length=30,
        choices=StockStage.choices,
        null=True,
        blank=True,
    )

    input_quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    output_quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    secondary_output_stage = models.CharField(
        max_length=30,
        choices=StockStage.choices,
        null=True,
        blank=True,
    )

    secondary_output_quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    loss_quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="open",
    )

    issued_at = models.DateTimeField(
        auto_now_add=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processing_runs_issued",
    )

    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processing_runs_completed",
    )

    notes = models.TextField(
        blank=True,
    )

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return (
            f"{self.stock.batch_number} - "
            f"{self.get_process_type_display()} - "
            f"{self.input_quantity} kg"
        )

    @property
    def accounted_quantity(self):
        return (
            (self.output_quantity or Decimal("0.00"))
            + self.secondary_output_quantity
            + self.loss_quantity
        )

    @property
    def is_accounted_for(self):
        return self.accounted_quantity == self.input_quantity
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
        CASHIER = 'CASHIER', 'Cashier'
        ACCOUNTS = 'ACCOUNTS', 'Accounts (Legacy)'

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
    def is_cashier(self):
        return self.role in {self.Role.CASHIER, self.Role.ACCOUNTS}

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
Packaged Inventory Models â€” Nonda Commodities

This module adds the packaged-product layer on top of the existing
green â†’ roasted kg-based ledger. The flow is:

    Green beans (kg)
        â†“ roast
    Roasted beans (kg)
        â†“ grind (optional)
    Ground coffee (kg)          Roasted beans (kg)
        â†“ package                   â†“ package
    Packaged ground (packs)    Packaged beans (packs)
        â†“                            â†“
         Released to sales â† â†’ Returns

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


# 4. PACKAGED PRODUCT  (blend Ã— pack_size Ã— form)
#    This is the sellable SKU.
class PackagedProduct(models.Model):

    blend = models.ForeignKey(
        Blend,
        on_delete=models.PROTECT,
        related_name="products",
    )

    pack_size = models.ForeignKey(
        PackSize,
        on_delete=models.PROTECT,
        related_name="products",
    )

    form = models.CharField(
        max_length=10,
        choices=ProductForm.choices,
    )

    kg_per_pack = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=0,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("blend", "pack_size", "form"),
                name="unique_packaged_product",
            )
        ]

        ordering = [
            "blend__name",
            "pack_size__grams",
            "form",
        ]

    def clean(self):

        if self.pack_size.is_sachet:
            if self.form != ProductForm.BEANS:
                raise ValidationError(
                    "Sachets can only contain whole beans."
                )

    def save(self, *args, **kwargs):

        self.full_clean()

        self.kg_per_pack = (
            Decimal(self.pack_size.grams)
            / Decimal("1000")
        )

        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.blend.name} "
            f"{self.pack_size.label} "
            f"({self.form})"
        )


# 5. PACKAGING RUN
class PackagingRun(models.Model):

    STATUS_CHOICES = (
        ("open", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    )

    stock = models.ForeignKey(
        CoffeeStock,
        on_delete=models.PROTECT,
        related_name="packaging_runs",
    )

    product = models.ForeignKey(
        PackagedProduct,
        on_delete=models.PROTECT,
        related_name="packaging_runs",
    )

    source_stage = models.CharField(
        max_length=30,
        choices=(
            (StockStage.ROASTED, "Roasted Coffee"),
            (StockStage.GROUND, "Ground Coffee"),
        ),
    )

    # KG issued to the packing operation
    input_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    # Actual number of packs received back
    packs_produced = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    # Actual coffee represented by those packs
    coffee_used_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    # Difference between input and coffee in completed packs
    loss_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="open",
    )

    issued_at = models.DateTimeField(
        auto_now_add=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="packaging_runs_issued",
    )

    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="packaging_runs_completed",
    )

    notes = models.TextField(
        blank=True,
    )

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return (
            f"{self.product} â€” "
            f"{self.input_kg} kg"
        )

    @property
    def accounted_kg(self):
        return (
            self.coffee_used_kg
            + self.loss_kg
        )

    @property
    def is_accounted_for(self):
        return (
            self.status == "completed"
            and self.accounted_kg == self.input_kg
        )

    @property
    def expected_packs(self):
        if not self.product_id:
            return 0

        if not self.product.kg_per_pack:
            return 0

        return int(
            self.input_kg /
            self.product.kg_per_pack
        )
    
# 6. PACKAGED INVENTORY  (live stock per product)
#    packs_in - packs_released + packs_returned
class PackagedInventory(models.Model):

    product = models.OneToOneField(
        PackagedProduct,
        on_delete=models.PROTECT,
        related_name="inventory",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["product__blend__name"]

    def __str__(self):
        return f"{self.product}: {self.available} packs available"

    @property
    def packs_produced(self):
        return sum(
            (run.packs_produced or 0)
            for run in self.product.packaging_runs.filter(status="completed")
        )

    @property
    def packs_released(self):
        return sum(
            release.packs_out
            for release in self.product.releases.all()
        )

    @property
    def packs_returned(self):
        return sum(
            ret.packs_returned
            for release in self.product.releases.all()
            for ret in release.returns.all()
        )

    @property
    def available(self):
        return (
            self.packs_produced
            - self.packs_released
            + self.packs_returned
        )

# 7. PACK RELEASE  (store keeper â†’ salesperson)
class PackRelease(models.Model):

    STATUS_CHOICES = (
        ("released", "Released"),
        ("partially_returned", "Partially Returned"),
        ("fully_returned", "Fully Returned"),
    )

    product = models.ForeignKey(
        PackagedProduct,
        on_delete=models.PROTECT,
        related_name="releases",
    )

    request_item = models.ForeignKey(
        "StockRequestItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="releases",
    )

    released_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pack_releases",
    )

    packs_out = models.PositiveIntegerField()

    price_per_pack = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        default=Decimal('0.00'),)

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="released",
    )

    released_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pack_releases_created",
    )

    notes = models.TextField(
        blank=True,
        default=''

    )

    class Meta:
        ordering = ["-released_at"]

    def __str__(self):
        return (
            f"{self.product} Ã— "
            f"{self.packs_out} â†’ "
            f"{self.released_to}"
        )

    @property
    def packs_returned(self):
        return sum(
            ret.packs_returned
            for ret in self.returns.all()
        )

    @property
    def packs_outstanding(self):
        return (
            self.packs_out
            - self.packs_returned
        )

    @property
    def stock_value(self):
        return Decimal(self.packs_out) * self.price_per_pack

# 8. PACK RETURN  (salesperson â†’ store)
class PackReturn(models.Model):

    release = models.ForeignKey(
        PackRelease,
        on_delete=models.PROTECT,
        related_name="returns",
    )

    packs_returned = models.PositiveIntegerField()

    reason = models.CharField(
        max_length=200,
        blank=True,
    )

    returned_at = models.DateTimeField(
        default=timezone.now
    )

    notes = models.TextField(max_length=150)

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pack_returns_received",
    )

    class Meta:
        ordering = ["-returned_at"]

    def __str__(self):
        return (
            f"{self.packs_returned} packs returned "
            f"from {self.release}"
        )


# 9. INTERNAL SALES STOCK REQUEST
class StockRequest(models.Model):
    PURPOSE_CHOICES = (
        ("sale", "For Sale"),
        ("display", "Display"),
        ("sampling", "Sampling"),
        ("event", "Event"),
        ("other", "Other"),
    )

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("partially_fulfilled", "Partially Fulfilled"),
        ("fulfilled", "Fulfilled"),
        ("cancelled", "Cancelled"),
    )

    request_number = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="stock_requests",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_requests",
    )
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default="sale")
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default="pending")
    notes = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"Request {str(self.request_number)[:8]} — {self.requested_by}"

    @property
    def short_number(self):
        return str(self.request_number).split("-")[0].upper()

    @property
    def is_fully_issued(self):
        items = list(self.items.all())
        return bool(items) and all(item.outstanding_quantity == 0 for item in items)


class StockRequestItem(models.Model):
    request = models.ForeignKey(
        StockRequest,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        PackagedProduct,
        on_delete=models.PROTECT,
        related_name="stock_request_items",
    )
    quantity_requested = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("request", "product"),
                name="unique_stock_request_product",
            )
        ]
        ordering = ["id"]

    def __str__(self):
        return f"{self.product} × {self.quantity_requested}"

    @property
    def quantity_issued(self):
        return sum(release.packs_out for release in self.releases.all())

    @property
    def outstanding_quantity(self):
        return max(self.quantity_requested - self.quantity_issued, 0)


class PackSettlement(models.Model):
    PAYMENT_CHOICES = (
        ("cash", "Cash"),
        ("mobile_money", "Mobile Money"),
        ("bank", "Bank Transfer"),
        ("other", "Other"),
    )

    STATUS_CHOICES = (
        ("partial", "Partially Paid"),
        ("cleared", "Cleared"),
    )

    release = models.OneToOneField(
        PackRelease,
        on_delete=models.PROTECT,
        related_name="settlement",
    )
    packs_sold = models.PositiveIntegerField(default=0)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default="cash")
    payment_reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="partial")
    cleared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pack_settlements",
    )
    cleared_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-cleared_at"]

    @property
    def amount_due(self):
        return Decimal(self.packs_sold) * self.release.price_per_pack

    @property
    def balance(self):
        return max(self.amount_due - self.amount_paid, Decimal("0.00"))

    @property
    def is_cleared(self):
        return self.status == "cleared" and self.balance == 0


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
        return f"{self.kg_sold} kg roasted â†’ {self.buyer_name}"

    @property
    def total_amount(self):
        return self.kg_sold * self.price_per_kg
