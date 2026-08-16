from django.db import models
import uuid
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

# Create your models here.
#company details

class Coffee_type(models.TextChoices):
    ARABICA = 'arabica', 'Arabica'
    ROBUSTA =   'robusta', 'Robusta'
class Company(models.Model):
    name = models.CharField(max_length=250, unique=True)
    country = models.CharField(max_length=250, unique=False)
    city = models.CharField(max_length=100)
    contact_person = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20, unique=True)
    address = models.TextField()
    is_acquired_client = models.BooleanField(default= True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    pipeline_stage = models.CharField(max_length=50, 
        choices = [
            ('new_lead', 'New Lead'),
            ('sample_sent', 'Sample Sent'),
            ('sample_received', 'Sample Received'),
            ('followup', 'Follow-up'),
            ('negotiation', 'Negotiation'),
            ('contract_signed', 'Contract Signed'),
            ('recurring_supply', 'Recurring Supply'),
            ('lost', 'Lost'),
        ],
        default='new_lead'    )  # initial_contact, sample_sent, followup, contract_signed, recurring_supply

    def __str__(self):
        return self.name

# inventory stock
class CoffeeStock(models.Model):
    coffee_type = models.CharField(max_length=20, choices=Coffee_type.choices)
    variety_name = models.CharField(max_length=150)
    washing_station = models.CharField(max_length=150)
    quantity_available = models.DecimalField(max_digits=8, default=0, decimal_places=2)
    reorder_level = models.DecimalField(max_digits=8, default=50, decimal_places=2)
    roast_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.variety_name} - {self.washing_station} - {self.coffee_type}"



    # sample
delivery_status_choices = [
    ('in_transit', 'In Transit'),
    ('delivered', 'Delivered'),
    ('failed', 'Failed')
    ]
class Sample(models.Model):
    id = models.UUIDField(primary_key=True,  default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    coffee_stock = models.ForeignKey(CoffeeStock, on_delete=models.RESTRICT)
    sample_weight = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    date_sent = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True)
    delivery_status = models.CharField(max_length=30,
         choices=delivery_status_choices,
        default='in_transit')  # in_transit, delivered, failed
    courier_name = models.CharField(max_length=50, default='DHL')
    tracking_number = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.company.name} - {self.coffee_stock.variety_name} - {self.sample_weight}kg"

# follow up tracker
class Followup(models.Model):
    sample = models.OneToOneField(Sample, on_delete=models.CASCADE)
    dispatch_alert_sent_at = models.DateTimeField(blank=True)
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
    rating = models.PositiveSmallIntegerField()  # Assuming a rating scale of 1-5
    interested_in_contract  = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Feedback for Sample ID: {self.sample.id} - Rating: {self.rating}"
contract_signing_status_choices = [
    ('pending', 'Pending'),
    ('signed', 'Signed'),
    ('declined', 'Declined')]
class Contract(models.Model):
    id = models.UUIDField(primary_key=True, editable=False, default=uuid.uuid4)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    sample = models.ForeignKey(Sample, on_delete=models.SET_NULL, null=True)
    volume_per_cycle_kg = models.DecimalField(max_digits=8, decimal_places=2)
    price_per_kg = models.DecimalField(null=False, max_digits=8,decimal_places=2)
    delivery_frequency = models.CharField(max_length=20, choices=[
        ('daily', 'daily'),
        ('weekly', 'weekly'),
        ('montly', 'monthly'),
                ])     
    status = models.CharField(max_length=20,
        choices=contract_signing_status_choices,
        default='pending')  # active, paused, cancelled
    signed_name = models.CharField(max_length=150, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    next_delivery_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"Contract for {self.company.name} - {self.sample.coffee_stock.variety_name} - {self.volume_per_cycle_kg}kg"

# --- 6. RECURRING SUPPLY LOG ---
class SupplyFulfillment(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE)
    coffee_stock = models.ForeignKey(CoffeeStock, on_delete=models.RESTRICT)
    volume_shipped_kg = models.DecimalField(max_digits=8, decimal_places=2)
    shipped_date = models.DateField(auto_now_add=True)
    invoice_number = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return f"Supply Fulfillment for Contract ID: {self.contract.id} - {self.volume_shipped_kg}kg"

class AuditLog(models.Model):

    ACTION_CHOICES = [
        ("create", "Created"),
        ("update", "Updated"),
        ("delete", "Deleted"),
    ]

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

    object_id = models.CharField(
        max_length=100
    )

    content_object = GenericForeignKey(
        "content_type",
        "object_id"
    )

    changes = models.JSONField(
        default=dict,
        blank=True
    )

    timestamp = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        username = self.user.username if self.user else "System"
        return f"{username} - {self.action} - {self.content_type.model} - {self.timestamp}"