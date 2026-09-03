from decimal import Decimal
from django import forms
from .models import (
    CoffeeStock, PackagedProduct, PackagingRun, 
    PackRelease, PackReturn, Blend, PackSize
)

from .models import Company, Sample, Contract
from .services.intake import generate_batch_number, resolve_variety
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth import get_user_model

User = get_user_model()

# Registration Form
User = get_user_model()

class UserRegistrationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('username', 'email', 'contact', 'employee_id', 'role')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({
                'class': 'w-full px-3 py-2 border border-slate-300 rounded-xl text-xs focus:outline-none focus:ring-2 focus:ring-blue-500'
            })


# Login Form
class UserLoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({
            'placeholder': 'Enter your username',
            'class': 'w-full px-3 py-2 border border-slate-300 rounded-xl text-xs'
        })
        self.fields['password'].widget.attrs.update({
            'placeholder': 'Enter your password',
            'class': 'w-full px-3 py-2 border border-slate-300 rounded-xl text-xs'
        })

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
    """
    Edit an existing batch. The variety is typed by name: an existing name
    re-points the batch at that definition, a new one creates it.
    """

    variety_name = forms.CharField(
        max_length=150,
        label="Name of material",
        widget=forms.TextInput(attrs={
            "class": FIELD_CLASS,
            "list": "variety_list",
            "autocomplete": "off",
            "placeholder": "e.g. Bugisu AA (Sipi Falls)",
        })
    )

    quantity_sorted_out = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0'),
        required=False,
        initial=Decimal('0'),
        label="Quantity Sorted Out (kg)",
        widget=forms.NumberInput(attrs={
            "class": FIELD_CLASS,
            "step": "0.01",
            "min": "0",
            "placeholder": "e.g. 12",
        })
    )

    class Meta:
        model = CoffeeStock
        fields = (
            "coffee_type",
            "received_date",
            "supplier",
            "source",
            "grade",
            "moisture_content",
            "fermentation_type",
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

            "fermentation_type": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. Aerobic fermentation",
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
                "readonly": "readonly",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance.pk:
            self.fields["variety_name"].initial = self.instance.variety.name

    def clean_variety_name(self):
        name = self.cleaned_data["variety_name"].strip()

        if not name:
            raise forms.ValidationError("Enter the name of the material.")

        return name

    def save(self, commit=True):
        stock = super().save(commit=False)

        variety, _ = resolve_variety(
            self.cleaned_data["variety_name"],
            self.cleaned_data,
        )

        stock.variety = variety

        if not stock.batch_number:
            stock.batch_number = generate_batch_number(
                variety,
                stock.received_date,
            )

        if commit:
            stock.save()

        return stock


class CoffeeStockIntakeForm(CoffeeStockForm):
    """
    Receive coffee. Quantity is never written to the batch directly Ã¢â‚¬â€ it is
    posted as a receipt StockMovement so the ledger stays the source of truth.
    """

    quantity_received = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=True,
        label="Quantity received (kg)",
        widget=forms.NumberInput(attrs={
            "class": FIELD_CLASS,
            "step": "0.01",
            "min": "0",
            "placeholder": "e.g. 500",
        })
    )

    def batch_data(self):
        """Cleaned batch attributes, without the intake-only fields."""

        return {
            field: self.cleaned_data[field]
            for field in self.Meta.fields
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

# ... your existing CompanyForm, CoffeeStockForm, CoffeeStockIntakeForm, SampleForm ...
# (keep them as-is)


class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = ("volume_per_cycle_kg", "price_per_kg", "delivery_frequency", "signed_name")
        widgets = {
            "volume_per_cycle_kg": forms.NumberInput(attrs={"class": FIELD_CLASS, "step": "0.01"}),
            "price_per_kg": forms.NumberInput(attrs={"class": FIELD_CLASS, "step": "0.01"}),
            "delivery_frequency": forms.Select(attrs={"class": FIELD_CLASS}),
            "signed_name": forms.TextInput(attrs={"class": FIELD_CLASS}),
        }



class ProcessingIssueForm(forms.Form):
    PROCESS_CHOICES = [
        ("roasting", "Roasting"),
        ("sorting", "Sorting"),
        ("grinding", "Grinding"),
    ]

    process_type = forms.ChoiceField(
        choices=PROCESS_CHOICES,
        widget=forms.Select(attrs={"class": FIELD_CLASS}),
    )
    input_quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={
            "class": FIELD_CLASS,
            "step": "0.01",
            "min": "0.01",
        }),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": FIELD_CLASS,
            "rows": 2,
            "placeholder": "Optional processing note",
        }),
    )


class ProcessingCompleteForm(forms.Form):
    output_quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        widget=forms.NumberInput(attrs={
            "class": FIELD_CLASS,
            "step": "0.01",
            "min": "0",
        }),
    )
    quaker_quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        initial=Decimal("0.00"),
        widget=forms.NumberInput(attrs={
            "class": FIELD_CLASS,
            "step": "0.01",
            "min": "0",
        }),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": FIELD_CLASS,
            "rows": 2,
            "placeholder": "Optional completion note",
        }),
    )

    def clean(self):
        cleaned = super().clean()
        output = cleaned.get("output_quantity")
        quakers = cleaned.get("quaker_quantity") or Decimal("0.00")
        if output is not None and quakers > output:
            # This is not generally a valid sorting rule; the service performs
            # the authoritative input-balance check. Keep the form permissive
            # for roasting/grinding and let the service validate the run.
            pass
        return cleaned


class PackagedProductForm(forms.ModelForm):
    class Meta:
        model = PackagedProduct
        fields = ['blend', 'form', 'pack_size', 'is_active']
        widgets = {
            'blend': forms.Select(attrs={'class': 'w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-rust/20'}),
            'form': forms.Select(attrs={'class': 'w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-rust/20'}),
            'pack_size': forms.Select(attrs={'class': 'w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-rust/20'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'rounded text-rust focus:ring-rust'}),
        }


class PackagingRunForm(forms.ModelForm):
    class Meta:
        model = PackagingRun
        fields = ["stock", "product", "source_stage", "input_kg", "packs_produced", "notes"]
        widgets = {
            "stock": forms.Select(attrs={"class": FIELD_CLASS}),
            "product": forms.Select(attrs={"class": FIELD_CLASS}),
            "source_stage": forms.Select(attrs={"class": FIELD_CLASS}),
            "input_kg": forms.NumberInput(attrs={
                "class": FIELD_CLASS,
                "step": "0.01",
                "min": "0.01",
            }),
            "packs_produced": forms.NumberInput(attrs={
                "class": FIELD_CLASS,
                "min": "1",
            }),
            "notes": forms.Textarea(attrs={
                "class": FIELD_CLASS,
                "rows": 3,
            }),
        }

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        source_stage = cleaned.get("source_stage")

        if product and source_stage:
            expected_stage = "ground" if product.form == "ground" else "roasted"
            if source_stage != expected_stage:
                raise forms.ValidationError(
                    f"{product.get_form_display()} products must be packaged from "
                    f"{expected_stage} coffee."
                )

        return cleaned


class PackReleaseForm(forms.ModelForm):
    class Meta:
        model = PackRelease
        fields = ['product', 'released_to', 'packs_out', 'price_per_pack', 'notes']
        widgets = {
            'product': forms.Select(attrs={'class': 'w-full px-3 py-2 border rounded-lg'}),
            "released_to": forms.Select(attrs={"class": FIELD_CLASS}),
            'packs_out': forms.NumberInput(attrs={'class': 'w-full px-3 py-2 border rounded-lg', 'min': '1'}),
            'price_per_pack': forms.NumberInput(attrs={'class': 'w-full px-3 py-2 border rounded-lg', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'w-full px-3 py-2 border rounded-lg', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["released_to"].queryset = User.objects.filter(
            role=User.Role.SALES,
            is_active=True,
        ).order_by("username")


class PackReturnForm(forms.ModelForm):
    class Meta:
        model = PackReturn
        fields = ['release', 'packs_returned', 'reason', 'notes', 'returned_at', 'received_by']
        widgets = {
            'release': forms.Select(attrs={'class': 'w-full px-3 py-2 border rounded-lg'}),
            'packs_returned': forms.NumberInput(attrs={'class': 'w-full px-3 py-2 border rounded-lg', 'min': '1'}),
            'reason': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border rounded-lg'}),
            'notes': forms.Textarea(attrs={'class': 'w-full px-3 py-2 border rounded-lg', 'rows': 3}),
            'returned_at': forms.DateTimeInput(attrs={'class': 'w-full px-3 py-2 border rounded-lg',            'type': 'datetime-local'
                }),
        }