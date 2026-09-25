from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.db import models
from django.dispatch import receiver
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
    account_holder = models.OneToOneField(
        'AccountHolder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='company_profile'
    )
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


@receiver(post_save, sender=Company)
def ensure_company_has_account_holder(sender, instance, created, **kwargs):
    """Automatically creates an AccountHolder ledger whenever a Company is created."""
    if created or not instance.account_holder:
        account, _ = AccountHolder.objects.get_or_create(
            name=instance.name,
            defaults={
                "account_type": "customer",
                "is_active": True,
                "notes": f"Automated ledger account for company: {instance.name}"
            }
        )
        Company.objects.filter(pk=instance.pk).update(account_holder=account)


class CompanyBranch(models.Model):
    company = models.ForeignKey(
        Company, 
        on_delete=models.CASCADE, 
        related_name="branches"
    )
    branch_name = models.CharField(
        max_length=150, 
        help_text="e.g. Oasis Mall Branch, Village Mall Bugolobi, Lugogo Branch"
    )
    address = models.CharField(max_length=255, blank=True)
    contact_person = models.CharField(max_length=100, blank=True)
    phone_number = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)

    def get_stock_level(self, product, stock_location=None):
        """
        Computes exact current stock level from immutable ledger history.
        If stock_location is 'shelf', gets shelf stock.
        If stock_location is 'backroom', gets backroom stock.
        If None, returns total stock at branch.
        """
        qs = BranchStockLedger.objects.filter(branch=self, product=product)
        if stock_location:
            qs = qs.filter(stock_location=stock_location)
        
        result = qs.aggregate(total=models.Sum('quantity'))['total']
        return result or 0

    def __str__(self):
        return f"{self.company.name} - {self.branch_name}"


class BranchStockLedger(models.Model):
    LOCATION_CHOICES = (
        ("shelf", "Active Display Shelf"),
        ("backroom", "Backroom Warehouse"),
    )

    TRANSACTION_TYPES = (
        ("delivery", "Stock Delivered from Nonda Main Warehouse"),
        ("restock_shelf", "Moved from Backroom to Display Shelf"),
        ("audit_sale", "Confirmed Sold via Audit"),
        ("shrinkage", "Damaged / Expired / Missing Stock"),
        ("return", "Returned to Nonda Central Warehouse"),
    )

    branch = models.ForeignKey(
        CompanyBranch, 
        on_delete=models.CASCADE, 
        related_name="stock_ledger_entries"
    )
    product = models.ForeignKey('PackagedProduct', on_delete=models.PROTECT)
    stock_location = models.CharField(max_length=20, choices=LOCATION_CHOICES, default="shelf")
    transaction_type = models.CharField(max_length=30, choices=TRANSACTION_TYPES)
    
    # Positive for additions (deliveries), negative for deductions (sales/shrinkage)
    quantity = models.IntegerField(help_text="+ Quantity added, - Quantity removed")
    
    # Reference to relevant audit or release doc
    audit = models.ForeignKey('StockAudit', on_delete=models.SET_NULL, null=True, blank=True)
    release = models.ForeignKey('PackRelease', on_delete=models.SET_NULL, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    def __str__(self):
        return f"{self.branch} | {self.product} | {self.transaction_type}: {self.quantity}"


class ConsignmentInventory(models.Model):
    """
    Tracks real-time stock sitting at a specific branch on consignment.
    Mapped per Branch (not just Company) to support multi-branch supermarket chains.
    """
    branch = models.ForeignKey(
        CompanyBranch, 
        on_delete=models.CASCADE, 
        related_name="consignment_inventories",
        null=True,
        blank=True,
    )
    product = models.ForeignKey(
        'PackagedProduct', 
        on_delete=models.PROTECT, 
        related_name="consignment_inventories"
    )
    current_shelf_quantity = models.PositiveIntegerField(
        default=0,
        help_text="Units sitting on active retail shelf display."
    )
    current_backroom_quantity = models.PositiveIntegerField(
        default=0,
        help_text="Units sitting in branch store room."
    )
    last_audited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("branch", "product")
        verbose_name_plural = "Consignment Inventories"

    @property
    def total_consignment_stock(self):
        return self.current_shelf_quantity + self.current_backroom_quantity

    def __str__(self):
        return f"{self.branch} - {self.product}: {self.total_consignment_stock} units on site"


class StockAudit(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="audits",
    )
    branch = models.ForeignKey(
        "CompanyBranch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audits",
    )
    audited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
    )
    audit_date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default="")

    def __str__(self):
        target = self.branch or self.company
        return f"Audit for {target} on {self.audit_date.strftime('%Y-%m-%d')}"



class StockAuditItem(models.Model):
    """
    Itemized audit line comparing expected ledger levels against physical counts
    across shelf and backroom locations.
    """
    audit = models.ForeignKey(StockAudit, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey('PackagedProduct', on_delete=models.PROTECT)
    
    # Detailed Expected Counts
    expected_shelf = models.PositiveIntegerField(default=0)
    expected_backroom = models.PositiveIntegerField(default=0)

    # Detailed Actual Physical Counts (Entered by Auditor)
    actual_shelf = models.PositiveIntegerField(default=0)
    actual_backroom = models.PositiveIntegerField(default=0)

    # Reconciled Quantities
    quantity_sold = models.PositiveIntegerField(
        default=0,
        help_text="Calculated units sold: (Expected Total - Actual Total - Shrinkage)"
    )
    shrinkage_quantity = models.PositiveIntegerField(
        default=0,
        help_text="Damaged, expired, or missing stock identified during audit"
    )

    selling_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    calculated_amount_due = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    @property
    def expected_total(self):
        return self.expected_shelf + self.expected_backroom

    @property
    def actual_total(self):
        return self.actual_shelf + self.actual_backroom

    def save(self, *args, **kwargs):
        # Automatically calculate quantity sold and billable debt
        net_unaccounted = self.expected_total - self.actual_total
        
        if net_unaccounted > self.shrinkage_quantity:
            self.quantity_sold = net_unaccounted - self.shrinkage_quantity
        else:
            self.quantity_sold = 0  # Prevents negative values on over-stock anomalies

        self.calculated_amount_due = self.quantity_sold * self.selling_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.audit.branch} | {self.product}: {self.quantity_sold} Sold"

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
    grams = models.PositiveIntegerField(choices=GRAM_CHOICES)
    label = models.CharField(max_length=30)  # "50g", "250g", etc.
    is_sachet = models.BooleanField(default=False)  # sachets = beans only
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["grams"]
        constraints = [
        models.UniqueConstraint(
            fields=("grams", "is_sachet"),
            name="unique_pack_size_type",
        ),
    ]

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
#    This is the sellaxble SKU.
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

    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
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

class StockRequest(models.Model):
    DESTINATION_CHOICES = (
        ("company", "Company / Supermarket"),
        ("agent_float", "Sales Agent Stock / Float"),
        ("direct_restaurant", "Direct Restaurant / Café Sale"),
        ("event_display", "Event / Exhibition"),
    )

    PURPOSE_CHOICES = (
        ("sale", "For Sale"),
        ("display", "Display"),
        ("sampling", "Sampling"),
        ("event", "Event"),
        ("field_agent", "Field Agent Stock"),
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

    account_holder = models.ForeignKey(
        'AccountHolder', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="stock_requests",
        help_text="The individual/account responsible for taking and paying for this stock."
    )
    destination_type = models.CharField(
        max_length=30, 
        choices=DESTINATION_CHOICES, 
        default="agent_float"
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

    selling_price = models.DecimalField(
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
from decimal import Decimal
from django.db import models
from django.conf import settings


class AccountHolder(models.Model):
    TYPE_CHOICES = (
        ("field_agent", "Field Agent"),
        ("salesperson", "Salesperson"),
        ("customer", "Customer"),
        ("walk_in", "Walk-in Customer"),
        ("other", "Other"),
    )

    name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=30, blank=True)
    account_type = models.CharField(
        max_length=30,
        choices=TYPE_CHOICES,
        default="field_agent",
    )

    system_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="business_account",
    )

    is_active = models.BooleanField(default=True)
    email = models.EmailField(blank=True, null=True)

    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

from django.db import models
from django.conf import settings
from decimal import Decimal

class EventExecution(models.Model):
    """
    Tracks a specific pop-up event or concert execution.
    """
    STATUS_CHOICES = (
        ('draft', 'Draft / Planning'),
        ('dispatched', 'Stock Dispatched'),
        ('reconciled', 'Reconciled & Closed'),
    )

    name = models.CharField(max_length=200, help_text="e.g., Blankets & Wine September Edition")
    location = models.CharField(max_length=250, blank=True)
    event_date = models.DateField()
    lead_attendant = models.ForeignKey(
        'web.AccountHolder',
        on_delete=models.PROTECT,
        related_name="managed_events"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    
    # Financial Summaries (Calculated upon reconciliation)
    total_cash_collected = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_momo_collected = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True, default="")
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.event_date})"

    # --- NEW FINANCE-READY PROPERTIES ---
    
    @property
    def total_actual_revenue(self):
        """Total hard cash physically brought back by the attendant."""
        return self.total_cash_collected + self.total_momo_collected

    @property
    def total_expected_revenue(self):
        """Sum of expected sales calculated from individual item logs."""
        return sum(item.expected_revenue for item in self.items.all())

    @property
    def total_event_cogs(self):
        """Sum of the raw cost values of all inventory consumed at the event."""
        return sum(item.total_consumed_cost for item in self.items.all())

    @property
    def gross_profit(self):
        """Actual financial returns generated after accounting for ingredient costs."""
        return self.total_actual_revenue - self.total_event_cogs

    @property
    def revenue_variance(self):
        """
        Calculates cash discrepancies. 
        Negative values mean money went missing. Positive values mean an overage.
        """
        return self.total_actual_revenue - self.total_expected_revenue


class EventItemReconciliation(models.Model):
    """
    Tracks itemization per product issued for the event.
    """
    event = models.ForeignKey(EventExecution, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey('PackagedProduct', on_delete=models.PROTECT)

    # Issued from warehouse
    quantity_issued = models.PositiveIntegerField(default=0)
    
    # NEW FEATURE: Snapshot cost at dispatch time to protect against future price changes
    unit_cost_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=Decimal('0.00'),
        help_text="The raw production/purchase cost per pack for margin calculation"
    )

    # Reconciliation Breakdown
    quantity_sold_retail = models.PositiveIntegerField(default=0, help_text="Packs sold sealed to retail customers")
    unit_sale_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    quantity_brewed = models.PositiveIntegerField(default=0, help_text="Packs opened for cup sales/brewing")
    cups_sold = models.PositiveIntegerField(default=0, help_text="Total cups sold from brewed packs")
    cup_sale_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    quantity_sampled = models.PositiveIntegerField(default=0, help_text="Packs used for free samples / promo")
    quantity_returned = models.PositiveIntegerField(default=0, help_text="Sealed packs returned back to inventory")

    @property
    def expected_revenue(self):
        retail_rev = self.quantity_sold_retail * self.unit_sale_price
        cup_rev = self.cups_sold * self.cup_sale_price
        return retail_rev + cup_rev

    @property
    def total_accounted_packs(self):
        return self.quantity_sold_retail + self.quantity_brewed + self.quantity_sampled + self.quantity_returned

    @property
    def variance(self):
        """Discrepancy between stock taken out vs accounted for"""
        return self.quantity_issued - self.total_accounted_packs

    # --- NEW STRATEGIC COST TRACKING PROPERTIES ---

    @property
    def total_consumed_packs(self):
        """Packs that left the warehouse and were NOT returned."""
        return self.quantity_issued - self.quantity_returned

    @property
    def total_consumed_cost(self):
        """Financial cost value of inventory used up (sold, brewed, sampled, or lost)."""
        return self.total_consumed_packs * self.unit_cost_price


class PackRelease(models.Model):
    STATUS_CHOICES = (
        ("released", "Released"),
        ("partially_returned", "Partially Returned"),
        ("fully_returned", "Fully Returned"),
        ("settled", "Paid / Settled"),
    )

    PAYMENT_METHOD_CHOICES = (
        ("cash", "Cash"),
        ("mobile_money", "Mobile Money"),
        ("bank", "Bank Transfer"),
        ("other", "Other"),
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
        AccountHolder,
        on_delete=models.PROTECT,
        related_name="pack_releases",
        null=True,
        blank=True,
    )

    packs_out = models.PositiveIntegerField()

    billable_packs = models.PositiveIntegerField(
    default=0,
    help_text=(
        "Units that have actually become billable. For normal sales this "
        "defaults to packs_out in the property below; for display/consignment "
        "this is increased by supermarket audits."
    ),
)


    company = models.ForeignKey(
        'Company',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pack_releases",
    )

    selling_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="released",
    )

    released_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pack_releases_created",
    )

    notes = models.TextField(blank=True, default="")

    purpose = models.CharField(
        max_length=20, 
        choices=StockRequest.PURPOSE_CHOICES, 
        default="sale"
    )

    @property
    def net_amount_due(self):
        """
        Calculates the net invoice value after accounting for approved return credits.
        """
        return max(self.gross_amount - self.return_credit, Decimal("0.00"))

    @property
    def total_amount_paid(self):
        """
        🎯 CUMULATIVE PAYMENT LEDGER SUM:
        Sums up all historical payment installments stored under the 
        new settlements ForeignKey relationship array securely.
        """
        return sum(settlement.amount_paid for settlement in self.settlements.all())

    @property
    def outstanding_balance(self):
        """
        The remaining unpaid debt value left on this delivery batch.
        """
        return max(self.net_amount_due - self.total_amount_paid, Decimal("0.00"))

    @property
    def packs_returned(self):
        """
        Calculates only the physical units brought back that a manager 
        has officially verified and APPROVED.
        """ 
        return sum(
            ret.packs_returned
            for ret in self.returns.all()
            if ret.status == "approved"
        )

    @property
    def packs_pending_return(self):
        """
        Tracks units currently sitting in the verification queue.
        This temporarily locks the packs so cashiers cannot log them 
        into a duplicate worksheet while waiting for approval.
        """
        return sum(
            ret.packs_returned
            for ret in self.returns.all()
            if ret.status == "pending_approval"
        )

        # Find these properties inside class PackRelease(models.Model) in web/models.py:

    @property
    def packs_sold(self):
        """
        Actual downstream sales are not currently tracked by PackRelease.

        A payment or settlement against this release does NOT mean
        the physical packs were sold by the account holder.
        """
        return 0

    @property
    def packs_outstanding(self):
        """Physical inventory packages still unreturned out in the field."""
        # 🎯 FIX: Changed self.billable_quantity to self.packs_out
        return max(self.packs_out - self.packs_returned, 0)


    @property
    def packs_returnable(self):
        """
        Physical packs from this release that can still be returned.

        Return eligibility is independent of payment.

        Released packs
            - approved returns
            - pending return requests
            = currently returnable packs
        """

        approved_returned = sum(
            ret.packs_returned
            for ret in self.returns.all()
            if ret.status == "approved"
        )

        pending_returned = sum(
            ret.packs_returned
            for ret in self.returns.all()
            if ret.status == "pending_approval"
        )

        return max(
            self.packs_out
            - approved_returned
            - pending_returned,
            0,
        )

    @property
    def return_credit(self):
        """
        Calculates financial relief deductions exclusively from 
        APPROVED return sheets to update outstanding billing accounts.
        """
        if self.purpose == "display":
            return Decimal("0.00")

        return sum(
            ret.financial_credit
            for ret in self.returns.all()
            if ret.status == "approved"
        )

    @property
    def billable_quantity(self):
        """
        🎯 COMPATIBILITY ALIAS:
        Maps legacy property name directly to packs_out to fix view/template errors.
        """
        return self.packs_out

    @property
    def gross_amount(self):
        """
        Calculates the total monetary value of this batch delivery 
        by multiplying physical units issued by the unit selling price.
        """
        # 🎯 FIX: Direct arithmetic calculation to restore the missing attribute
        return Decimal(self.packs_out) * self.selling_price

class PaymentReceipt(models.Model):

    PAYMENT_METHOD_CHOICES = (
        ("cash", "Cash"),
        ("mobile_money", "Mobile Money"),
        ("bank", "Bank Transfer"),
        ("other", "Other"),
    )

    release = models.ForeignKey(
        PackRelease,
        on_delete=models.PROTECT,
        related_name="payments",
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
    )

    payment_reference = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    collected_at = models.DateTimeField(
        auto_now_add=True,
    )

    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments_collected",
    )

    notes = models.TextField(
        blank=True,
        default="",
    )

    class Meta:
        ordering = ["-collected_at"]

    def __str__(self):
        return (
            f"{self.release.released_to} — "
            f"{self.amount} — "
            f"{self.get_method_display()}"
        )
    # 8. PACK RETURN  (salesperson â†’ store)
from django.conf import settings
from django.db import models


from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone

class PackReturn(models.Model):
    RETURN_STATUS_CHOICES = (
        ("pending_approval", "Pending Approval"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    )

    CONDITION_CHOICES = (
        ("good", "Good / Resalable"),
        ("damaged", "Damaged"),
        ("opened", "Opened"),
        ("expired", "Expired"),
        ("other", "Other"),
    )

    DISPOSITION_CHOICES = (
        ("accepted", "Accepted Return"),
    )

    release = models.ForeignKey(
        "PackRelease",
        on_delete=models.PROTECT,
        related_name="returns",
    )
    packs_returned = models.PositiveIntegerField()
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES, default="good")
    disposition = models.CharField(max_length=30, choices=DISPOSITION_CHOICES, default="accepted")
    status = models.CharField(max_length=30, choices=RETURN_STATUS_CHOICES, default="pending_approval", db_index=True)
    
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="pack_returns_submitted")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="pack_returns_approved")
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    reason = models.CharField(max_length=200, blank=True)
    returned_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True, default="")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="pack_returns_received")

    class Meta:
        ordering = ["-returned_at"]

    def __str__(self):
        return f"{self.packs_returned} packs returned from {self.release}"

    @property
    def counts_against_release(self):
        return self.status == "approved"

    @property
    def returns_to_stock(self):
        return self.status == "approved"

    @property
    def financial_credit(self):
        """Calculates value of this specific return slip once approved."""
        if self.status == "approved":
            return Decimal(self.packs_returned) * self.release.selling_price
        return Decimal("0.00")


# 9. INTERNAL SALES STOCK REQUEST

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

    release = models.ForeignKey(
        PackRelease,
        on_delete=models.PROTECT,
        related_name="settlements", 
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

    def __str__(self):
        return (
            f"UGX {self.amount_paid:,.0f} — "
            f"{self.release.recipient_name} — "
            f"{self.get_payment_method_display()}"
        )


    @property
    def balance_after_payment(self):
        return self.release.outstanding_balance

    @property
    def is_cleared(self):
        return self.balance_after_payment <= Decimal("0.00")



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

# expenses page
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone

class Expense(models.Model):
    CATEGORY_CHOICES = (
        ('rent', 'Rent'),
        ('electricity', 'Electricity'),
        ('maintenance', 'Maintenance'),
        ('salaries', 'Salaries'),
        ('logistics', 'Logistics & Transport'),
        ('staff_food', 'Staff Food'),
        ('others', 'Others'),
    )

    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    expense_date = models.DateField(default=timezone.now, help_text="Date the expense was incurred")
    notes = models.TextField(
        blank=True, 
        null=True, 
        help_text="Detailed note (Required if category is 'Others')"
    )
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="logged_expenses"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-expense_date', '-created_at']

    def __str__(self):
        return f"{self.get_category_display()} - UGX {self.amount:,.0f} ({self.expense_date})"