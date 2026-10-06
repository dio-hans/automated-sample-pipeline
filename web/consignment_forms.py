from django import forms

from .models import Company, CompanyBranch, PackagedProduct
from decimal import Decimal
from django import forms
from .models import CompanyBranch, PackagedProduct

INPUT = 'w-full h-9 rounded-md border border-[#D9DCD4] bg-white px-2.5 text-xs text-slate-800 focus:border-[#738061] focus:ring-1 focus:ring-[#738061] outline-none'



class ConsigneeCreateForm(forms.Form):
    """
    Enrol a consignee into the consignment system.

    Either select an existing Company record or create a new one.
    Every consignee starts with at least one physical branch.
    """

    existing_company = forms.ModelChoiceField(
        queryset=Company.objects.all().order_by("name"),
        required=False,
        label="Existing company",
        help_text="Use this if the supermarket/customer already exists in Nonda.",
    )

    name = forms.CharField(
        max_length=250,
        required=False,
        label="Consignee name",
    )

    country = forms.CharField(
        max_length=250,
        required=False,
        initial="Uganda",
    )

    city = forms.CharField(
        max_length=100,
        required=False,
    )

    contact_person = forms.CharField(
        max_length=100,
        required=False,
    )

    email = forms.EmailField(
        required=False,
    )

    phone_number = forms.CharField(
        max_length=20,
        required=False,
    )

    address = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 2}
        ),
    )

    branch_name = forms.CharField(
        max_length=150,
        label="First branch",
        help_text="Example: Lugogo, Oasis Mall, Main Branch",
    )

    branch_address = forms.CharField(
        max_length=255,
        required=False,
        label="Branch address",
    )

    branch_contact_person = forms.CharField(
        max_length=100,
        required=False,
        label="Branch contact person",
    )

    branch_phone = forms.CharField(
        max_length=30,
        required=False,
        label="Branch phone",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        input_class = (
            "w-full h-8 rounded-md border border-[#D8D2C2] "
            "bg-white px-2.5 text-xs text-slate-800 "
            "outline-none transition "
            "focus:border-[#5C7A5A] focus:ring-1 focus:ring-[#5C7A5A]/20"
        )

        select_class = (
            "w-full h-8 rounded-md border border-[#D8D2C2] "
            "bg-white px-2.5 text-xs text-slate-800 "
            "outline-none transition "
            "focus:border-[#5C7A5A] focus:ring-1 focus:ring-[#5C7A5A]/20"
        )

        textarea_class = (
            "w-full rounded-md border border-[#D8D2C2] "
            "bg-white px-2.5 py-2 text-xs text-slate-800 "
            "outline-none transition resize-none "
            "focus:border-[#5C7A5A] focus:ring-1 focus:ring-[#5C7A5A]/20"
        )

        for name, field in self.fields.items():

            if isinstance(field.widget, forms.Select):
                field.widget.attrs.update({
                    "class": select_class,
                })

            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs.update({
                    "class": textarea_class,
                    "rows": 2,
                })

            else:
                field.widget.attrs.update({
                    "class": input_class,
                })

        self.fields["existing_company"].widget.attrs.update({
            "class": select_class,
        })

        self.fields["name"].widget.attrs.update({
            "placeholder": "e.g. Capital Shoppers",
        })

        self.fields["city"].widget.attrs.update({
            "placeholder": "e.g. Kampala",
        })

        self.fields["contact_person"].widget.attrs.update({
            "placeholder": "Contact person's name",
        })

        self.fields["email"].widget.attrs.update({
            "placeholder": "email@example.com",
        })

        self.fields["phone_number"].widget.attrs.update({
            "placeholder": "+256...",
        })

        self.fields["address"].widget.attrs.update({
            "placeholder": "Main office / business address",
        })

        self.fields["branch_name"].widget.attrs.update({
            "placeholder": "e.g. Lugogo Branch",
        })

        self.fields["branch_address"].widget.attrs.update({
            "placeholder": "Branch location",
        })

        self.fields["branch_contact_person"].widget.attrs.update({
            "placeholder": "Branch contact",
        })

        self.fields["branch_phone"].widget.attrs.update({
            "placeholder": "+256...",
        })

    def clean(self):
        cleaned = super().clean()

        existing = cleaned.get("existing_company")

        if existing:
            return cleaned

        required_new = {
            "name": "Consignee name",
            "country": "Country",
            "city": "City",
            "contact_person": "Contact person",
            "email": "Email",
            "phone_number": "Phone number",
            "address": "Address",
        }

        for field_name, label in required_new.items():
            if not cleaned.get(field_name):
                self.add_error(
                    field_name,
                    f"{label} is required when creating a new consignee.",
                )

        return cleaned

    
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
