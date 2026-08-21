from django.db import transaction
from django.utils import timezone

from ..models import Contract, Followup, Sample


@transaction.atomic
def create_followup_for_sample(sample):
    """Auto-create a follow-up tracker when a sample is dispatched."""
    followup, created = Followup.objects.get_or_create(sample=sample)
    if created:
        followup.dispatch_alert_sent_at = timezone.now()
        followup.save(update_fields=["dispatch_alert_sent_at"])
    return followup


@transaction.atomic
def mark_guide_sent(followup):
    followup.day_3_guide_sent_at = timezone.now()
    followup.save(update_fields=["day_3_guide_sent_at", "updated_at"])


@transaction.atomic
def mark_contract_sent(followup):
    followup.day_7_contract_sent_at = timezone.now()
    followup.save(update_fields=["day_7_contract_sent_at", "updated_at"])


@transaction.atomic
def convert_to_contract(*, followup, volume_per_cycle_kg, price_per_kg,
                        delivery_frequency, signed_name="", user=None):
    """Turn a followed-up sample into a signed contract."""
    contract = Contract.objects.create(
        company=followup.sample.company,
        sample=followup.sample,
        volume_per_cycle_kg=volume_per_cycle_kg,
        price_per_kg=price_per_kg,
        delivery_frequency=delivery_frequency,
        status="signed",
        signed_name=signed_name,
        signed_at=timezone.now(),
    )
    followup.is_converted_to_contract = True
    followup.save(update_fields=["is_converted_to_contract", "updated_at"])

    company = followup.sample.company
    company.pipeline_stage = "contract_signed"
    company.save(update_fields=["pipeline_stage", "updated_at"])

    return contract