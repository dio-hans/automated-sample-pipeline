from .models import AccountHolder, PackReturn
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
from .models import AccountHolder, PackRelease, StockRequest, PackagedProduct

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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

    def clean(self):
        cleaned = super().clean()
        destination = cleaned.get("destination_type")
        company = cleaned.get("company")
        account = cleaned.get("account_holder")

        if destination in {"company", "direct_restaurant"} and not company:
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

        available_product_ids = []

        inventory_records = (
            PackagedInventory.objects
            .select_related("product__blend", "product__pack_size")
        )

        for inventory in inventory_records:
            if inventory.available > 0 and inventory.product.is_active:
                available_product_ids.append(inventory.product_id)

        self.fields["product"].queryset = (
            PackagedProduct.objects
            .filter(
                pk__in=available_product_ids,
                is_active=True,
            )
            .select_related("blend", "pack_size")
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
            f"{product.blend.name} · {product.pack_size.label} · "
            f"{product.get_form_display()} — {available} in store"
        )

class BaseStockRequestItemFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        products = set()
        valid_rows = 0
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            product = form.cleaned_data.get("product")
            if product is None:
                continue
            valid_rows += 1
            if product.pk in products:
                raise forms.ValidationError("Each product may appear only once in a request.")
            products.add(product.pk)
        if valid_rows == 0:
            raise forms.ValidationError("Add at least one packaged product.")


StockRequestItemFormSet = formset_factory(
    StockRequestItemForm,
    formset=BaseStockRequestItemFormSet,
    extra=1,
    max_num=20,
    validate_max=True,
)


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
