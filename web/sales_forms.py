from django import forms
from django.forms import BaseFormSet, formset_factory

from .models import Company, PackRelease, PackagedProduct, StockRequest

FIELD_CLASS = (
    "w-full rounded-lg border border-[#E4DECB] px-3 py-2.5 bg-white "
    "text-sm text-ink focus:outline-none focus:ring-2 focus:ring-rust/20 focus:border-rust"
)


class StockRequestForm(forms.ModelForm):
    class Meta:
        model = StockRequest
        fields = ("purpose", "company", "notes")
        widgets = {
            "purpose": forms.Select(attrs={"class": FIELD_CLASS}),
            "company": forms.Select(attrs={"class": FIELD_CLASS}),
            "notes": forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].required = False
        self.fields["company"].queryset = Company.objects.order_by("name")


class StockRequestItemForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=PackagedProduct.objects.filter(is_active=True).select_related("blend", "pack_size"),
        widget=forms.Select(attrs={"class": FIELD_CLASS}),
    )
    quantity_requested = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={"class": FIELD_CLASS, "min": "1"}),
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
    price_per_pack = forms.DecimalField(
        min_value=0,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": FIELD_CLASS, "min": "0", "step": "0.01"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "Optional issue note"}),
    )


class PackReturnCleanForm(forms.Form):
    packs_returned = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={"class": FIELD_CLASS, "min": "1"}),
    )
    reason = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "e.g. unsold stock returned"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 3}),
    )


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
