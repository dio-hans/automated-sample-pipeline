from dataclasses import dataclass
from datetime import datetime, timedelta
from django.core.exceptions import FieldDoesNotExist
from django.db.models import DateField, DateTimeField
from django.utils import timezone


@dataclass(frozen=True)
class DateRange:
    start: object
    end: object
    preset: str


def today_range(today):
    return today, today

def yesterday_range(today):
    y = today - timedelta(days=1)
    return y, y

def this_month_range(today):
    return today.replace(day=1), today

PRESETS = {
    "today": today_range,
    "yesterday": yesterday_range,
    "this_month": this_month_range,
}
DEFAULT_PRESET = "this_month"


def resolve_date_range(request):
    """Return DateRange. Custom ?start_date=&end_date= wins over ?preset=."""
    today = timezone.localdate()
    preset = request.GET.get("preset")
    start_s = request.GET.get("start_date")
    end_s = request.GET.get("end_date")

    if start_s and end_s:
        try:
            start = datetime.strptime(start_s, "%Y-%m-%d").date()
            end = datetime.strptime(end_s, "%Y-%m-%d").date()
            if start <= end:
                return DateRange(start, end, "custom")
        except ValueError:
            pass

    if preset in PRESETS:
        s, e = PRESETS[preset](today)
        return DateRange(s, e, preset)

    s, e = PRESETS[DEFAULT_PRESET](today)
    return DateRange(s, e, DEFAULT_PRESET)


def _field_kind(queryset, date_field):
    try:
        field = queryset.model._meta.get_field(date_field)
    except FieldDoesNotExist:
        raise ValueError(f"'{date_field}' is not a date field on {queryset.model.__name__}.")
    if isinstance(field, DateTimeField):
        return "datetime"
    if isinstance(field, DateField):
        return "date"
    raise ValueError(f"'{date_field}' must be a DateField or DateTimeField.")


def apply_date_filters(request, queryset, date_field="created_at"):
    """Apply the resolved range to any queryset. Returns (qs, preset, today, start, end)."""
    r = resolve_date_range(request)
    today = timezone.localdate()

    if r.start is None and r.end is None:
        return queryset, r.preset, today, None, None

    kind = _field_kind(queryset, date_field)
    if kind == "date":
        queryset = queryset.filter(**{f"{date_field}__gte": r.start, f"{date_field}__lte": r.end})
    else:
        queryset = queryset.filter(**{f"{date_field}__date__gte": r.start, f"{date_field}__date__lte": r.end})

    return queryset, r.preset, today, r.start, r.en