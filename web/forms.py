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

FIELD_CLASS = (
    "w-full rounded-md border border-[#E4DECB] px-3 py-2.5 bg-white "
    "text-sm focus:outline-none focus:ring-2 focus:ring-rust focus:border-rust"
)

class CoffeeStockForm(forms.ModelForm):

    quantity_received = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0,
        required=True,
        widget=forms.NumberInput(attrs={
            "class": FIELD_CLASS,
            "step": "0.01",
            "min": "0",
            "placeholder": "e.g. 500"
        })
    )

    class Meta:
        model = CoffeeStock
        fields = (
            "coffee_type",
            "variety",
            "received_date",
            "supplier",
            "source",
            "grade",
            "moisture_content",
            "process",
            "season_of_harvest",
            "foreign_smell",
            "foreign_matter",
            "prints",
            "physical_damages",
            "defects",
            "quantity_after_sorting",
            "checked_by",
            "verified_by",
            "delivered_by",
            "car_number",
            "received_by",
            "reorder_level",
        )

        widgets = {
            "coffee_type": forms.Select(attrs={"class": FIELD_CLASS}),

            "variety": forms.Select(attrs={
                "class": FIELD_CLASS
            }),

            "received_date": forms.DateInput(attrs={
                "class": FIELD_CLASS,
                "type": "date",
            }),

            "supplier": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. Darling Coffee Uganda",
            }),

            "source": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. Mbale / Bulambuli",
            }),

            "grade": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. AA, AB",
            }),

            "moisture_content": forms.NumberInput(attrs={
                "class": FIELD_CLASS,
                "step": "0.1",
                "min": "0",
            }),

            "process": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. Natural process",
            }),

            "season_of_harvest": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. 2025/26",
            }),

            "foreign_smell": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. None",
            }),

            "foreign_matter": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. None",
            }),

            "prints": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. None",
            }),

            "physical_damages": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "Yes / No",
            }),

            "defects": forms.NumberInput(attrs={
                "class": FIELD_CLASS,
                "step": "0.01",
                "min": "0",
            }),

            "quantity_after_sorting": forms.NumberInput(attrs={
                "class": FIELD_CLASS,
                "step": "0.01",
                "min": "0",
            }),

            "checked_by": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "Name",
            }),

            "verified_by": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "Names",
            }),

            "delivered_by": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "Name",
            }),

            "car_number": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. UAB 123X",
            }),

            "received_by": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "Names",
            }),

            "reorder_level": forms.NumberInput(attrs={
                "class": FIELD_CLASS,
                "step": "0.01",
                "min": "0",
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