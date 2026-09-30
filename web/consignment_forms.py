from decimal import Decimal
from django import forms
from .models import CompanyBranch, PackagedProduct

INPUT = 'w-full h-9 rounded-md border border-[#D9DCD4] bg-white px-2.5 text-xs text-slate-800 focus:border-[#738061] focus:ring-1 focus:ring-[#738061] outline-none'


class BranchForm(forms.ModelForm):
    class Meta:
        model = CompanyBranch
        fields = ('branch_name', 'address', 'contact_person', 'phone_number', 'is_active')
        widgets = {
            'branch_name': forms.TextInput(attrs={'class': INPUT, 'placeholder': 'Branch name'}),
            'address': forms.TextInput(attrs={'class': INPUT}),
            'contact_person': forms.TextInput(attrs={'class': INPUT}),
            'phone_number': forms.TextInput(attrs={'class': INPUT}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 rounded border-slate-300'}),
        }


class ConsignmentDeliveryForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=PackagedProduct.objects.filter(is_active=True).select_related('blend', 'pack_size'),
        widget=forms.Select(attrs={'class': INPUT}),
    )
    shelf = forms.IntegerField(min_value=0, initial=0,
        widget=forms.NumberInput(attrs={'class': INPUT, 'min': '0'}))
    backroom = forms.IntegerField(min_value=0, initial=0,
        widget=forms.NumberInput(attrs={'class': INPUT, 'min': '0'}))
    unit_price = forms.DecimalField(min_value=Decimal('0.01'), max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': INPUT, 'min': '0.01', 'step': '0.01'}))
    notes = forms.CharField(required=False,
        widget=forms.Textarea(attrs={'class': INPUT, 'rows': '2', 'style': 'height:64px'}))

    def clean(self):
        data = super().clean()
        if (data.get('shelf') or 0) + (data.get('backroom') or 0) <= 0:
            self.add_error('shelf', 'Enter at least one pack for delivery.')
        return data


class ShelfTransferForm(forms.Form):
    product_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput())
    quantity = forms.IntegerField(min_value=1,
        widget=forms.NumberInput(attrs={'class': INPUT, 'min': '1', 'placeholder': 'Qty'}))


class BranchReturnForm(forms.Form):
    release_id = forms.IntegerField(min_value=1)
    location = forms.ChoiceField(choices=(('shelf', 'Shelf'), ('backroom', 'Backroom')))
    quantity = forms.IntegerField(min_value=1)


class AuditDecisionForm(forms.Form):
    action = forms.ChoiceField(choices=(('approve', 'Approve'), ('reject', 'Reject')))
    reason = forms.CharField(required=False, max_length=1000)
