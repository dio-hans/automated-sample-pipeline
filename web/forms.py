from decimal import Decimal
from django import forms
from .models import (
    AccountHolder, CoffeeStock, PackagedProduct, PackagingRun, 
PackRelease, PackReturn, PackSize
)
from .models import Blend

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

from django import forms
from django.contrib.auth.forms import AuthenticationForm

class UserLoginForm(AuthenticationForm):
    # Set your custom error message here
    error_messages = {
        'invalid_login': "Invalid credentials. Please check your username and password.",
        'inactive': "This account has been deactivated.",
    }

    # If you have custom styling on fields, keep it below
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'placeholder': 'Username'})
        self.fields['password'].widget.attrs.update({'placeholder': 'Enter your password'})

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


class PackSizeForm(forms.ModelForm):
    class Meta:
        model = PackSize
        fields = ["grams", "label", "is_sachet", "is_active"]
        widgets = {
            "grams": forms.NumberInput(attrs={
                "class": FIELD_CLASS,
                "step": "0.01",
                "min": "0.01",
                "placeholder": "e.g. 250",
            }),
            "label": forms.TextInput(attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. 250g",
            }),
            "is_sachet": forms.CheckboxInput(attrs={
                "class": "rounded text-rust focus:ring-rust",
            }),
            "is_active": forms.CheckboxInput(attrs={
                "class": "rounded text-rust focus:ring-rust",
            }),
        }



FIELD_CLASS = "block w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900 placeholder-gray-400 focus:border-rust focus:ring-rust sm:text-sm"

from decimal import Decimal
from django import forms
from django.db import transaction
from .models import Blend, PackSize, PackagedProduct

FIELD_CLASS = "block w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900 placeholder-gray-400 focus:border-rust focus:ring-rust sm:text-sm"

from decimal import Decimal
from django import forms
from .models import Blend, PackSize, PackagedProduct

FIELD_CLASS = (
    "w-full rounded-md border border-[#E4DECB] px-3 py-2.5 bg-white "
    "text-sm focus:outline-none focus:ring-2 focus:ring-rust focus:border-rust"
)

class PackagedProductBulkForm(forms.Form):
    blend_input = forms.CharField(
        max_length=255,
        label="Blend / Product Name",
        widget=forms.TextInput(
            attrs={
                "list": "blend-list",
                "class": FIELD_CLASS,
                "autocomplete": "off",
                "placeholder": "e.g. Kitiko Blend, Tendo, etc.",
            }
        ),
    )

    SIZE_CONFIG = {
        "sachet": {"label": "Sachet", "grams": 15, "is_sachet": True},
        "50g": {"label": "50g", "grams": 50, "is_sachet": False},
        "250g": {"label": "250g", "grams": 250, "is_sachet": False},
        "500g": {"label": "500g", "grams": 500, "is_sachet": False},
        "1kg": {"label": "1kg", "grams": 1000, "is_sachet": False},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.existing_blends = Blend.objects.filter(is_active=True).order_by("name")

        # Dynamically create checkboxes and inputs for the grid
        for key, conf in self.SIZE_CONFIG.items():
            # Checkbox for whether this size is offered
            self.fields[f"size_{key}"] = forms.BooleanField(
                required=False, 
                label=conf["label"],
                widget=forms.CheckboxInput(attrs={'class': 'w-4 h-4 rounded text-rust focus:ring-rust interaction-trigger'})
            )
            
            # Price inputs for variants
            forms_for_size = ["beans"] if conf["is_sachet"] else ["beans", "ground"]
            for form_type in forms_for_size:
                self.fields[f"price_{key}_{form_type}"] = forms.DecimalField(
                    required=False,
                    min_value=Decimal("0"),
                    max_digits=10,
                    decimal_places=2,
                    widget=forms.NumberInput(
                        attrs={
                            "class": FIELD_CLASS + " price-input",
                            "step": "100",  # Easy stepping for UGX increments
                            "placeholder": "Price (UGX)",
                            "data-size": key,
                            "data-type": form_type
                        }
                    ),
                )

    def save(self):
        """
        Creates or updates multiple PackagedProduct variants all at once.
        """
        blend_name = self.cleaned_data["blend_input"].strip()
        blend, _ = Blend.objects.get_or_create(name=blend_name, defaults={"is_active": True})

        for key, conf in self.SIZE_CONFIG.items():
            # Only save configurations where the size checkbox is ticked
            if self.cleaned_data.get(f"size_{key}"):
                if conf["is_sachet"]:
                    pack_size, _ = PackSize.objects.get_or_create(
                        is_sachet=True,
                        defaults={
                            "label": conf['label'],
                            "grams": conf["grams"],
                            "is_active": True
                        }
                    )
                else:
                    pack_size, _ = PackSize.objects.get_or_create(
                        grams=conf["grams"],
                        is_sachet=False,
                        defaults={
                            "label": conf["label"],
                            "is_active": True
                        }
                    )

                forms_for_size = ["beans"] if conf["is_sachet"] else ["beans", "ground"]

                for form_type in forms_for_size:
                    price = self.cleaned_data.get(f"price_{key}_{form_type}")
                    
                    if price is not None:
                        product, created = PackagedProduct.objects.get_or_create(
                            blend=blend,
                            pack_size=pack_size,
                            form=form_type,
                            defaults={"selling_price": price, "is_active": True},
                        )
                        if not created:
                            product.selling_price = price
                            product.is_active = True
                            product.save()
        return blend



class PackagingRunForm(forms.ModelForm):
    class Meta:
        model = PackagingRun
        fields = [
            "stock",
            "product",
            "source_stage",
            "input_kg",
            "packs_produced",
            "notes",
        ]
        widgets = {
            "stock": forms.Select(attrs={"class": FIELD_CLASS, "id": "id_stock"}),
            "product": forms.Select(attrs={"class": FIELD_CLASS, "id": "id_product"}),
            "source_stage": forms.Select(attrs={"class": FIELD_CLASS, "id": "id_source_stage"}),
            "input_kg": forms.NumberInput(attrs={"class": FIELD_CLASS, "step": "0.01", "min": "0.01"}),
            "packs_produced": forms.NumberInput(attrs={"class": FIELD_CLASS, "min": "1"}),
            "notes": forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 1. Customize Stock Dropdown Labels
        stock_choices = [("", "Select a batch stock source...")]
        for s in CoffeeStock.objects.all():
            stock_choices.append((s.id, f"{s.batch_number} - {s.variety_name}"))
        self.fields["stock"].choices = stock_choices

        # 2. Re-bind the Product Dropdown Choices to inject custom data parameters
        # We manually build custom choice objects so the template can loop over them easily
        self.available_products = PackagedProduct.objects.filter(is_active=True).select_related('blend', 'pack_size')


    def clean(self):
        cleaned = super().clean()

        # 1. Resolve selected stock from typed search text
        stock_text = cleaned.get("stock_search")
        if stock_text:
            # Assumes format "BATCH-XXXX - Variety"
            batch_num = stock_text.split(" - ")[0].strip()
            matched_stock = CoffeeStock.objects.filter(batch_number=batch_num).first()
            if matched_stock:
                cleaned["stock"] = matched_stock
            else:
                self.add_error("stock_search", "Selected batch stock source does not exist.")

        # 2. Resolve selected product from typed search text
        product_text = cleaned.get("product_search")
        if product_text:
            # Parses string "Kitiko Blend — 250g (Beans)"
            try:
                parts = [p.strip() for p in product_text.split("—")]
                blend_name = parts[0]
                
                # Split size from form variant block
                remainder = parts[1].split(" (")
                size_label = remainder[0].strip()
                form_variant = "beans" if "Beans" in remainder[1] else "ground"

                matched_product = PackagedProduct.objects.filter(
                    blend__name=blend_name,
                    pack_size__label=size_label,
                    form=form_variant
                ).first()

                if matched_product:
                    cleaned["product"] = matched_product
                else:
                    self.add_error("product_search", "Selected product variant does not exist.")
            except Exception:
                self.add_error("product_search", "Please select a valid option from the dropdown suggestion list.")

        # Keep original automated backend structural safeguards active
        product = cleaned.get("product")
        if product:
            if product.form == "beans":
                cleaned["source_stage"] = "roasted"
            elif product.form == "ground":
                cleaned["source_stage"] = "ground"

        return cleaned


class PackReleaseForm(forms.ModelForm):

    class Meta:
        model = PackRelease
        fields = [
            "product",
            "released_to",
            "packs_out",
            "selling_price",
            "notes",
        ]

        widgets = {
            "product": forms.Select(
                attrs={"class": FIELD_CLASS}
            ),

            "released_to": forms.Select(
                attrs={"class": FIELD_CLASS}
            ),

            "packs_out": forms.NumberInput(
                attrs={
                    "class": FIELD_CLASS,
                    "min": "1",
                }
            ),

            "selling_price": forms.NumberInput(
                attrs={
                    "class": FIELD_CLASS,
                    "min": "0",
                    "step": "0.01",
                }
            ),

            "notes": forms.Textarea(
                attrs={
                    "class": FIELD_CLASS,
                    "rows": 3,
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["released_to"].queryset = (
            AccountHolder.objects
            .filter(is_active=True)
            .order_by("name")
        )

        self.fields["released_to"].empty_label = (
            "Select account holder..."
        )

    def clean_packs_out(self):
        quantity = self.cleaned_data["packs_out"]

        if quantity <= 0:
            raise forms.ValidationError(
                "Release quantity must be greater than zero."
            )

        return quantity

    def clean_selling_price(self):
        price = self.cleaned_data["selling_price"]

        if price < 0:
            raise forms.ValidationError(
                "Selling price cannot be negative."
            )

        return price

# expenses form
from django import forms
from django.core.exceptions import ValidationError
from .models import Expense
from django import forms
from .models import Expense


class ExpenseForm(forms.ModelForm):

    class Meta:
        model = Expense
        fields = [
            "category",
            "amount",
            "expense_date",
            "notes",
        ]

        widgets = {

            "category": forms.Select(attrs={
                "class": "expense-input",
            }),

            "amount": forms.NumberInput(attrs={
                "class": "expense-input",
                "placeholder": "Enter amount in UGX",
                "min": "0",
                "step": "0.01",
            }),

            "expense_date": forms.DateInput(attrs={
                "class": "expense-input",
                "type": "date",
            }),

            "notes": forms.Textarea(attrs={
                "class": "expense-input",
                "rows": 3,
                "placeholder": "What was this expense for?",
            }),
        }

    def clean(self):

        cleaned_data = super().clean()

        category = cleaned_data.get("category")
        notes = cleaned_data.get("notes")

        if category == "others" and not notes:
            self.add_error(
                "notes",
                'Please provide details when selecting "Others".'
            )

        return cleaned_data