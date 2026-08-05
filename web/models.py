from django.db import models
import uuid

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
    is_acquired_client = models.BooleanField(default= True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# inventory stock
class CoffeeStock (models.Model):
    coffee_type = models.CharField(max_length=20, choices=Coffee_type.choices)
    variety_name = models.CharField(max_length=150)
    washing_station = models.CharField(max_length=150)
    quantity_available = models.DecimalField(max_digits=8, default=0, decimal_places=2)
    reorder_level = models.DecimalField(max_digits=8, default=50, decimal_places=2)
    roast_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# sample
class Sample(models.Model):
    id = models.UUIDField(primary_key=True,  default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.Cascade)
    coffee_stock = models.ForeignKey(CoffeeStock, on_delete=models.RESTRICT)
    sample_weight = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    date_sent = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True)
    delivery_status = models.CharField(max_length=30, default='in_transit')  # in_transit, delivered, failed
    courier_name = models.CharField(max_length=50, default='DHL')
    tracking_number = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# follow up tracker
class Followup(models.Model):
    sample = models.OneToOneField(Sample, on_delete=models.CASCADE)
    dispatch_alert_sent_at = models.DateTimeField(blank=True)
    day_3_guide_sent_at = models.DateTimeField(null=True, blank=True)
    day_7_contract_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
    status = models.CharField(max_length=20, default='active')  # active, paused, cancelled
    signed_name = models.CharField(max_length=150, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    next_delivery_date = models.DateField(null=True, blank=True)

# --- 6. RECURRING SUPPLY LOG ---
class SupplyFulfillment(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE)
    coffee_stock = models.ForeignKey(CoffeeStock, on_delete=models.RESTRICT)
    volume_shipped_kg = models.DecimalField(max_digits=8, decimal_places=2)
    shipped_date = models.DateField(auto_now_add=True)
    invoice_number = models.CharField(max_length=50, unique=True)