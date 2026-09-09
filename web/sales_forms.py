from .models import PackReturn
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

class PackReturnCleanForm(forms.Form):

    packs_returned = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(
            attrs={
                "class": FIELD_CLASS,
                "min": "1",
            }
        ),
    )

    condition = forms.ChoiceField(
        choices=PackReturn.CONDITION_CHOICES,
        widget=forms.Select(
            attrs={"class": FIELD_CLASS}
        ),
    )

    disposition = forms.ChoiceField(
        choices=PackReturn.DISPOSITION_CHOICES,
        initial="accepted",
        widget=forms.Select(
            attrs={"class": FIELD_CLASS}
        ),
    )

    reason = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": FIELD_CLASS,
                "placeholder": "e.g. unsold stock returned",
            }
        ),
    )

    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": FIELD_CLASS,
                "rows": 3,
            }
        ),
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



class PaymentReceiptForm(forms.ModelForm):

    class Meta:
        model = PaymentReceipt
        fields = [
            "amount",
            "method",
            "payment_reference",
            "notes",
        ]

        widgets = {
            "amount": forms.NumberInput(
                attrs={
                    "class": FIELD_CLASS,
                    "min": "0.01",
                    "step": "0.01",
                }
            ),

            "method": forms.Select(
                attrs={"class": FIELD_CLASS}
            ),

            "payment_reference": forms.TextInput(
                attrs={
                    "class": FIELD_CLASS,
                    "placeholder": "MoMo / bank transaction reference",
                }
            ),

            "notes": forms.Textarea(
                attrs={
                    "class": FIELD_CLASS,
                    "rows": 3,
                    "placeholder": "Optional payment note",
                }
            ),
        }

    def clean_amount(self):
        amount = self.cleaned_data["amount"]

        if amount <= Decimal("0.00"):
            raise forms.ValidationError(
                "Payment amount must be greater than zero."
            )

        return amount

    