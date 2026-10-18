from .models import AccountHolder, PackReturn, PaymentReceipt
from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory


from .models import (Company,
 PackRelease,
  PackagedProduct, PaymentReceipt,
   StockRequest,
PackagedInventory,
)

FIELD_CLASS = (
    "w-full rounded-lg border border-[#E4DECB] px-3 py-2.5 bg-white "
    "text-sm text-ink focus:outline-none focus:ring-2 focus:ring-rust/20 focus:border-rust"
)

# sales_forms.py
from django import forms
from django.core.exceptions import ValidationError
from .models import (
    AccountHolder,
    CoffeeStock,
    PAYMENT_METHOD_CHOICES,
    PackRelease,
    StockRequest,
    PackagedProduct,
    BulkStockRequestItem,
)

class AccountHolderForm(forms.ModelForm):
    class Meta:
        model = AccountHolder
        fields = ['name', 'account_type', 'system_user', 'phone_number', 'email', 'is_active', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none'}),
            'account_type': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none'}),
            'system_user': forms.Select(attrs={'class': 'w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none'}),
            'phone_number': forms.TextInput(attrs={'class': 'w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none'}),
            'email': forms.EmailInput(attrs={'class': 'w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'rounded border-slate-300 text-emerald-600 focus:ring-emerald-500 h-4 w-4'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none'}),
        }

class DirectPackReleaseForm(forms.Form):
    released_to = forms.ModelChoiceField(
        queryset=AccountHolder.objects.filter(is_active=True),
        required=True,
        widget=forms.Select(attrs={'class': 'w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none'})
    )
    product = forms.ModelChoiceField(
        queryset=PackagedProduct.objects.all(),
        required=True,
        widget=forms.Select(attrs={'class': 'w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none'})
    )
    packs_out = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none'})
    )
    selling_price = forms.DecimalField(
        max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none'})
    )

class StockRequestForm(forms.ModelForm):
    class Meta:
        model = StockRequest
        # 1. Added "destination_type" to fields
        fields = ("purpose", "destination_type", "company", "account_holder")
        widgets = {
            "purpose": forms.Select(attrs={"class": FIELD_CLASS}),
            "destination_type": forms.Select(attrs={"class": FIELD_CLASS}),  # 2. Widget added
            "company": forms.Select(attrs={"class": FIELD_CLASS}),
            "account_holder": forms.Select(attrs={"class": FIELD_CLASS}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.requesting_user = user
        self.fields["account_holder"].queryset = (
            AccountHolder.objects.filter(is_active=True).order_by("name")
        )
        self.fields["company"].queryset = (
            Company.objects.all().order_by("name")
        )
        self.fields["account_holder"].required = False
        self.fields["company"].required = False
        self.fields["account_holder"].empty_label = "— No dedicated account —"
        self.fields["company"].empty_label = "— No company —"

        self.sales_account_holder = None
        is_sales_requester = (
            getattr(user, "role", None) == "SALES"
            and not getattr(user, "is_superuser", False)
        )
        if is_sales_requester:
            self.sales_account_holder = (
                AccountHolder.objects
                .filter(system_user=user)
                .order_by("pk")
                .first()
            )
            for field_name, value in (
                ("purpose", "sale"),
                ("destination_type", "agent_float"),
            ):
                field = self.fields[field_name]
                field.initial = value
                field.disabled = True
                field.widget = forms.HiddenInput()
                self.initial[field_name] = value

            account_field = self.fields["account_holder"]
            account_field.queryset = (
                AccountHolder.objects.filter(pk=self.sales_account_holder.pk)
                if self.sales_account_holder
                else AccountHolder.objects.none()
            )
            account_field.initial = self.sales_account_holder
            account_field.disabled = True
            account_field.widget = forms.HiddenInput()
            self.initial["account_holder"] = self.sales_account_holder

    def clean(self):
        cleaned = super().clean()
        if (
            getattr(self.requesting_user, "role", None) == "SALES"
            and not getattr(self.requesting_user, "is_superuser", False)
        ):
            if not self.sales_account_holder:
                self.add_error(
                    None,
                    "Your user account is not linked to a salesperson account. "
                    "Ask an administrator to link it before requesting stock.",
                )
                return cleaned

            cleaned["purpose"] = "sale"
            cleaned["destination_type"] = "agent_float"
            cleaned["account_holder"] = self.sales_account_holder
            return cleaned

        destination = cleaned.get("destination_type")
        company = cleaned.get("company")
        account = cleaned.get("account_holder")

        if destination == "company" and not company:
            self.add_error(
                "company",
                "Select the company / restaurant receiving this stock.",
            )

        if destination == "agent_float" and not account:
            self.add_error(
                "account_holder",
                "Select the account responsible for this stock.",
            )

        return cleaned



class StockRequestItemForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=PackagedProduct.objects.none(),
        widget=forms.Select(attrs={"class": FIELD_CLASS}),
    )

    quantity_requested = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(
            attrs={
                "class": FIELD_CLASS,
                "min": "1",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # IMPORTANT:
        # Requests may be made even when current packaged stock is zero.
        # The request waits until stock becomes available.
        self.fields["product"].queryset = (
            PackagedProduct.objects
            .filter(is_active=True)
            .select_related(
                "blend",
                "pack_size",
            )
            .order_by(
                "blend__name",
                "pack_size__grams",
                "form",
            )
        )

    def label_from_instance(self, product):
        try:
            available = product.inventory.available
        except PackagedInventory.DoesNotExist:
            available = 0

        return (
            f"{product.blend.name} · "
            f"{product.pack_size.label} · "
            f"{product.get_form_display()} — "
            f"{available} currently in store"
        )

    
class BaseStockRequestItemFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        products = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            product = form.cleaned_data.get("product")
            if product is None:
                continue
            if product.pk in products:
                raise forms.ValidationError("Each product may appear only once in a request.")
            products.add(product.pk)


StockRequestItemFormSet = formset_factory(
    StockRequestItemForm,
    formset=BaseStockRequestItemFormSet,
    extra=1,
    max_num=20,
    validate_max=True,
    can_delete=True,
)


class BulkStockRequestItemForm(forms.Form):
    bulk_field_class = (
        "w-full rounded-md border border-slate-200 px-2.5 py-1.5 bg-white "
        "text-xs text-slate-800 focus:outline-none focus:ring-2 focus:ring-rust "
        "focus:border-rust"
    )

    stock = forms.ModelChoiceField(
        queryset=CoffeeStock.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": bulk_field_class}),
    )
    stage = forms.ChoiceField(
        choices=(("", "Select coffee stage"), *BulkStockRequestItem.STAGE_CHOICES),
        required=False,
        widget=forms.Select(attrs={"class": bulk_field_class}),
    )
    quantity_requested = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=10,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(
            attrs={"class": bulk_field_class, "min": "0.01", "step": "0.01"}
        ),
    )
    price_per_kg = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=12,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(
            attrs={"class": bulk_field_class, "min": "0.01", "step": "0.01"}
        ),
    )
    buyer_name = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": bulk_field_class,
                "placeholder": "Walk-in / buyer name (optional)",
            }
        ),
    )

    def __init__(self, *args, stock_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stock"].queryset = (
            stock_queryset
            if stock_queryset is not None
            else CoffeeStock.objects.select_related("variety").order_by("batch_number")
        )

    def clean(self):
        cleaned = super().clean()
        values = (
            cleaned.get("stock"),
            cleaned.get("stage"),
            cleaned.get("quantity_requested"),
            cleaned.get("price_per_kg"),
        )
        if any(value not in (None, "") for value in values) and not all(
            value not in (None, "") for value in values
        ):
            raise forms.ValidationError(
                "Complete the batch, stage, kilograms, and price for every bulk line."
            )
        return cleaned


class BaseBulkStockRequestItemFormSet(BaseFormSet):
    def __init__(self, *args, stock_queryset=None, **kwargs):
        self.stock_queryset = stock_queryset
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["stock_queryset"] = self.stock_queryset
        return kwargs

    def clean(self):
        super().clean()
        seen = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            row = form.cleaned_data
            if row.get("DELETE") or not row.get("stock") or not row.get("stage"):
                continue
            key = (row["stock"].pk, row["stage"])
            if key in seen:
                raise forms.ValidationError(
                    "Each batch and coffee stage may appear only once in a request."
                )
            seen.add(key)


BulkStockRequestItemFormSet = formset_factory(
    BulkStockRequestItemForm,
    formset=BaseBulkStockRequestItemFormSet,
    extra=1,
    max_num=20,
    validate_max=True,
    can_delete=True,
)


class BulkFulfillItemForm(forms.Form):
    item_id = forms.IntegerField(widget=forms.HiddenInput())
    issue_quantity = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={"class": FIELD_CLASS, "min": "0", "step": "0.01"}
        ),
    )
    price_per_kg = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={"class": FIELD_CLASS, "min": "0", "step": "0.01"}
        ),
    )


BulkFulfillFormSet = formset_factory(
    BulkFulfillItemForm,
    extra=0,
    max_num=100,
    validate_max=True,
)


class BulkSalePaymentForm(forms.Form):
    amount = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={"class": FIELD_CLASS, "min": "0.01", "step": "0.01"}
        ),
    )
    payment_method = forms.ChoiceField(choices=PAYMENT_METHOD_CHOICES)
    payment_reference = forms.CharField(max_length=100, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class FulfillItemForm(forms.Form):
    item_id = forms.IntegerField(widget=forms.HiddenInput())
    issue_quantity = forms.IntegerField(
        min_value=0,
        widget=forms.NumberInput(attrs={"class": FIELD_CLASS, "min": "0"}),
    )
    selling_price = forms.DecimalField(
        min_value=0,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": FIELD_CLASS, "min": "0", "step": "0.01"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "Optional issue note"}),
    )


FIELD_CLASS = (
    "w-full rounded-md border border-line px-3 py-2.5 bg-white text-sm "
    "focus:outline-none focus:ring-2 focus:ring-rust focus:border-rust transition duration-150"
)

class PackReturnForm(forms.ModelForm):
    class Meta:
        model = PackReturn
        fields = ['packs_returned', 'condition', 'disposition', 'reason', 'notes']
        widgets = {
            'packs_returned': forms.NumberInput(attrs={'class': FIELD_CLASS, 'min': '1'}),
            'condition': forms.Select(attrs={'class': FIELD_CLASS}),
            'disposition': forms.Select(attrs={'class': FIELD_CLASS}),
            'reason': forms.TextInput(attrs={'class': FIELD_CLASS, 'placeholder': 'e.g. unsold stock returned'}),
            'notes': forms.Textarea(attrs={'class': FIELD_CLASS, 'rows': 3}),
        }

    def __init__(self, *args, release=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = release

    def clean_packs_returned(self):
        packs_returned = self.cleaned_data.get('packs_returned')
        if self.release and packs_returned:
            if packs_returned > self.release.packs_outstanding:
                raise forms.ValidationError(
                    f"Cannot return more than the outstanding {self.release.packs_outstanding} packs."
                )
        return packs_returned


class SettlementForm(forms.Form):
    packs_sold = forms.IntegerField(
        min_value=0,
        widget=forms.NumberInput(attrs={"class": FIELD_CLASS, "min": "0"}),
    )
    amount_paid = forms.DecimalField(
        min_value=0,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": FIELD_CLASS, "min": "0", "step": "0.01"}),
    )
    payment_method = forms.ChoiceField(
        choices=(
            ("cash", "Cash"),
            ("mobile_money", "Mobile Money"),
            ("bank", "Bank Transfer"),
            ("other", "Other"),
        ),
        widget=forms.Select(attrs={"class": FIELD_CLASS}),
    )
    payment_reference = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "Receipt / transaction reference"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 3}),
    )



FIELD_CLASS = (
    "w-full rounded-lg border border-slate-200 px-3 py-2.5 bg-white "
    "text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-orange-200 "
    "focus:border-orange-400"
)


class SingleItemReturnForm(forms.Form):
    release_id = forms.IntegerField(widget=forms.HiddenInput())
    packs_returned = forms.IntegerField(
        min_value=0,
        required=True,
        widget=forms.NumberInput(attrs={"class": FIELD_CLASS, "min": "0", "step": "1"}),
    )
    condition = forms.ChoiceField(
        choices=(
            ("good", "Good / Resalable"),
            ("damaged", "Damaged"),
            ("opened", "Opened"),
            ("expired", "Expired"),
            ("other", "Other"),
        ),
        initial="good",
        widget=forms.Select(attrs={"class": FIELD_CLASS}),
    )

class PaymentReceiptForm(forms.ModelForm):
    class Meta:
        model = PaymentReceipt

        fields = [
            "amount",
            "payment_method",
            "payment_reference",
            "notes",
        ]

        widgets = {
            "amount": forms.NumberInput(
                attrs={
                    "class": "form-input",
                    "min": "0.01",
                    "step": "0.01",
                }
            ),
            "payment_method": forms.Select(
                attrs={
                    "class": "form-select",
                }
            ),
            "payment_reference": forms.TextInput(
                attrs={
                    "class": "form-input",
                    "placeholder": "MoMo / bank / receipt reference",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-textarea",
                    "rows": 3,
                }
            ),
        }