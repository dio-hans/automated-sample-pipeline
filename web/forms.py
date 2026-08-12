from django import forms
from .models import Company, Coffee_stock, Sample

class CompanyForm(forms.models.ModelForm):
    class Meta:
        model = Company
        fields = '__all__'

class CoffeeStockForm(forms.ModelForm):
    class Meta:
        model = Coffee_stock
        fields = "__all__"

class SampleForm(forms.ModelForm):
    class Meta:
        model = Sample
        fields = (
            "company",
            "coffee_stock",
            "sample_weight",
            "courier_name",
            "tracking_number",
        )