
from django import forms
from django.core.exceptions import ValidationError

from .models import (
    StoreProductContent,
    StorePromotion,
    StoreHomepageContent,
)


class StoreProductContentForm(forms.ModelForm):
    class Meta:
        model = StoreProductContent
        fields = [
            "display_name",
            "description",
            "image",
            "is_featured",
            "is_visible",
        ]

        widgets = {
            "display_name": forms.TextInput(attrs={
                "class": "store-field",
                "placeholder": "Name shown to customers",
            }),
            "description": forms.Textarea(attrs={
                "class": "store-field",
                "rows": 5,
                "placeholder": "Describe this coffee for customers...",
            }),
            "image": forms.ClearableFileInput(attrs={
                "class": "store-file",
                "accept": "image/jpeg,image/png,image/webp",
            }),
            "is_featured": forms.CheckboxInput(attrs={
                "class": "store-checkbox",
            }),
            "is_visible": forms.CheckboxInput(attrs={
                "class": "store-checkbox",
            }),
        }

        labels = {
            "display_name": "Customer-facing product name",
            "description": "Product description",
            "image": "Product photograph",
            "is_featured": "Feature this product on the homepage",
            "is_visible": "Publish this product on the online shop",
        }

    def clean_image(self):
        image = self.cleaned_data.get("image")

        if image and image.size > 5 * 1024 * 1024:
            raise ValidationError(
                "Image size cannot exceed 5 MB."
            )

        return image

# store promotions forms

from django import forms
from django.db.models import Q

from .models import StoreProductContent, StorePromotion


class StorePromotionForm(forms.ModelForm):

    starts_at = forms.DateTimeField(
        input_formats=[
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S",
        ],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={
                "type": "datetime-local",
                "class": "store-field",
            },
        ),
    )

    ends_at = forms.DateTimeField(
        input_formats=[
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S",
        ],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={
                "type": "datetime-local",
                "class": "store-field",
            },
        ),
    )

    class Meta:
        model = StorePromotion
        fields = [
            "content",
            "discounted_price",
            "starts_at",
            "ends_at",
            "is_active",
        ]

        widgets = {
            "content": forms.Select(
                attrs={"class": "store-field"}
            ),
            "discounted_price": forms.NumberInput(
                attrs={
                    "class": "store-field",
                    "min": "0.01",
                    "step": "0.01",
                    "placeholder": "Enter promotional price",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={"class": "store-checkbox"}
            ),
        }

        labels = {
            "content": "Select published product",
            "discounted_price": "Promotional price (UGX)",
            "starts_at": "Promotion starts",
            "ends_at": "Promotion ends",
            "is_active": "Enable this promotion",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        eligible_products = Q(
            is_visible=True,
            product__is_active=True,
            product__blend__is_active=True,
            product__pack_size__is_active=True,
        )

        # Keep the existing product selectable when editing,
        # even if it has subsequently been hidden.
        if self.instance.pk:
            eligible_products |= Q(
                pk=self.instance.content_id
            )

        self.fields["content"].queryset = (
            StoreProductContent.objects
            .filter(eligible_products)
            .select_related(
                "product__blend",
                "product__pack_size",
            )
            .order_by(
                "product__blend__name",
                "product__pack_size__grams",
            )
        )

        self.fields["content"].label_from_instance = (
            lambda item: (
                f"{item.display_name or item.product.blend.name} — "
                f"{item.product.pack_size.label} — "
                f"{item.product.get_form_display()}"
            )
        )

        # A promotion remains attached to its original product
        # after creation.
        if self.instance.pk:
            self.fields["content"].disabled = True



class StoreHomepageContentForm(forms.ModelForm):

    class Meta:
        model = StoreHomepageContent

        fields = [
            "hero_eyebrow",
            "hero_title_line",
            "hero_title_emphasis",
            "hero_description",
            "hero_image",
            "hero_image_alt",
            "hero_visual_label",
            "hero_visual_reference",
            "intro_text",
            "intro_subtext",
            "collection_eyebrow",
            "collection_title",
            "story_eyebrow",
            "story_title_line",
            "story_title_emphasis",
            "story_description",
            "find_eyebrow",
            "find_title",
            "find_description",
        ]

        widgets = {
            field: forms.TextInput(attrs={
                "class": "store-field",
            })
            for field in [
                "hero_eyebrow",
                "hero_title_line",
                "hero_title_emphasis",
                "hero_image_alt",
                "hero_visual_label",
                "hero_visual_reference",
                "intro_text",
                "intro_subtext",
                "collection_eyebrow",
                "collection_title",
                "story_eyebrow",
                "story_title_line",
                "story_title_emphasis",
                "find_eyebrow",
                "find_title",
            ]
        }

        labels = {
            "hero_eyebrow": "Hero small heading",
            "hero_title_line": "Hero main heading",
            "hero_title_emphasis": "Hero emphasized heading",
            "hero_description": "Hero description",
            "hero_image": "Homepage banner photograph",
            "hero_image_alt": "Banner image description",
            "hero_visual_label": "Banner image label",
            "hero_visual_reference": "Banner image reference",
            "intro_text": "Intro strip main text",
            "intro_subtext": "Intro strip secondary text",
            "collection_eyebrow": "Collection small heading",
            "collection_title": "Collection main heading",
            "story_eyebrow": "Our Story small heading",
            "story_title_line": "Our Story main heading",
            "story_title_emphasis": "Our Story emphasized heading",
            "story_description": "Our Story description",
            "find_eyebrow": "Find Nonda small heading",
            "find_title": "Find Nonda main heading",
            "find_description": "Find Nonda description",
        }

        widgets = {
            **widgets,
            "hero_description": forms.Textarea(attrs={
                "class": "store-field",
                "rows": 4,
            }),
            "story_description": forms.Textarea(attrs={
                "class": "store-field",
                "rows": 4,
            }),
            "find_description": forms.Textarea(attrs={
                "class": "store-field",
                "rows": 4,
            }),
            "hero_image": forms.ClearableFileInput(attrs={
                "class": "store-file",
                "accept": "image/jpeg,image/png,image/webp",
            }),
        }

    def clean_hero_image(self):
        image = self.cleaned_data.get("hero_image")

        if image and image.size > 5 * 1024 * 1024:
            raise forms.ValidationError(
                "The homepage photograph cannot exceed 5 MB."
            )

        return image



class CartQuantityForm(forms.Form):

    quantity = forms.IntegerField(
        min_value=1,
        required=True,
        widget=forms.NumberInput(attrs={
            "min": 1,
            "step": 1,
            "class": "store-field",
        }),
        error_messages={
            "required": "Enter a quantity.",
            "invalid": "Enter a valid whole number.",
            "min_value": "Quantity must be at least 1.",
        },
    )


class CartQuantityForm(forms.Form):
    quantity = forms.IntegerField(
        min_value=1,
        required=True,
        widget=forms.NumberInput(attrs={
            "min": 1,
            "step": 1,
            "class": "store-field",
        }),
    )