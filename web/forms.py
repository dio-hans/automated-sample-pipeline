from django import forms
from .models import Company, CoffeeStock, Sample


class CompanyForm(forms.ModelForm):

    class Meta:
        model = Company
        fields = "__all__"

        widgets = {
            "name": forms.TextInput(attrs={
                "class": "w-full px-3 py-2.5 border border-[#E4DECB] rounded-md "
                         "bg-white text-sm focus:outline-none focus:ring-2 "
                         "focus:ring-rust focus:border-rust",
                "placeholder": "Enter company name",
            }),

            "country": forms.TextInput(attrs={
                "class": "w-full px-3 py-2.5 border border-[#E4DECB] rounded-md "
                         "bg-white text-sm focus:outline-none focus:ring-2 "
                         "focus:ring-rust focus:border-rust",
                "placeholder": "e.g. Uganda",
            }),

            "city": forms.TextInput(attrs={
                "class": "w-full px-3 py-2.5 border border-[#E4DECB] rounded-md "
                         "bg-white text-sm focus:outline-none focus:ring-2 "
                         "focus:ring-rust focus:border-rust",
                "placeholder": "e.g. Kampala",
            }),

            "contact_person": forms.TextInput(attrs={
                "class": "w-full px-3 py-2.5 border border-[#E4DECB] rounded-md "
                         "bg-white text-sm focus:outline-none focus:ring-2 "
                         "focus:ring-rust focus:border-rust",
                "placeholder": "Full name",
            }),

            "email": forms.EmailInput(attrs={
                "class": "w-full px-3 py-2.5 border border-[#E4DECB] rounded-md "
                         "bg-white text-sm focus:outline-none focus:ring-2 "
                         "focus:ring-rust focus:border-rust",
                "placeholder": "name@company.com",
            }),

            "phone_number": forms.TextInput(attrs={
                "class": "w-full px-3 py-2.5 border border-[#E4DECB] rounded-md "
                         "bg-white text-sm focus:outline-none focus:ring-2 "
                         "focus:ring-rust focus:border-rust",
                "placeholder": "+256...",
            }),

            "address": forms.Textarea(attrs={
                "class": "w-full px-3 py-2.5 border border-[#E4DECB] rounded-md "
                         "bg-white text-sm focus:outline-none focus:ring-2 "
                         "focus:ring-rust focus:border-rust",
                "rows": 3,
                "placeholder": "Company address",
            }),

            "is_acquired_client": forms.CheckboxInput(attrs={
                "class": "w-4 h-4 rounded border-[#E4DECB] "
                         "text-rust focus:ring-rust",
            }),

            "pipeline_stage": forms.Select(attrs={
                "class": "w-full px-3 py-2.5 border border-[#E4DECB] rounded-md "
                         "bg-white text-sm focus:outline-none focus:ring-2 "
                         "focus:ring-rust focus:border-rust",
            }),
        }


class CoffeeStockForm(forms.ModelForm):
    class Meta:
        model = CoffeeStock
        fields = (
            "coffee_type",
            "variety_name",
            "washing_station",
            "quantity_available",
            "reorder_level",
            "roast_date",
        )
        widgets = {
            "coffee_type": forms.Select(attrs={
                "class": "w-full rounded-md border border-[#E4DECB] px-3 py-2.5 bg-white focus:outline-none focus:ring-2 focus:ring-rust"
            }),

            "variety_name": forms.TextInput(attrs={
                "class": "w-full rounded-md border border-[#E4DECB] px-3 py-2.5 bg-white focus:outline-none focus:ring-2 focus:ring-rust",
                "placeholder": "e.g. Bugisu AA"
            }),

            "washing_station": forms.TextInput(attrs={
                "class": "w-full rounded-md border border-[#E4DECB] px-3 py-2.5 bg-white focus:outline-none focus:ring-2 focus:ring-rust",
                "placeholder": "e.g. Bugisu Washing Station"
            }),
            "quantity_available": forms.NumberInput(attrs={
                "class": "w-full rounded-md border border-[#E4DECB] px-3 py-2.5 bg-white focus:outline-none focus:ring-2 focus:ring-rust",
                "step": "0.01",
                "min": "0"
            }),

            "reorder_level": forms.NumberInput(attrs={
                "class": "w-full rounded-md border border-[#E4DECB] px-3 py-2.5 bg-white focus:outline-none focus:ring-2 focus:ring-rust",
                "step": "0.01",
                "min": "0"
            }),
            "roast_date": forms.DateInput(attrs={
                "class": "w-full rounded-md border border-[#E4DECB] px-3 py-2.5 bg-white focus:outline-none focus:ring-2 focus:ring-rust",
                "type": "date"
            }),
        }


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