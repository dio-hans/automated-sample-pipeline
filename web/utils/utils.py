from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.exceptions import FieldDoesNotExist
from django.db.models import DateField, DateTimeField
from django.utils import timezone


# DATE RANGE
@dataclass(frozen=True)
class DateRange:
    """
    Represents a resolved date range.

    start:
        First date included in the range.

    end:
        Last date included in the range.

    preset:
        The preset that produced the range.
    """

    start: object
    end: object
    preset: str

# PRESET CALCULATIONS
def today_range(today):
    return today, today


def yesterday_range(today):
    yesterday = today - timedelta(days=1)
    return yesterday, yesterday


def this_week_range(today):
    start = today - timedelta(days=today.weekday())
    return start, today


def last_week_range(today):
    start_of_this_week = today - timedelta(days=today.weekday())
    end = start_of_this_week - timedelta(days=1)
    start = end - timedelta(days=6)

    return start, end


def this_month_range(today):
    return today.replace(day=1), today


def last_month_range(today):
    first_day_this_month = today.replace(day=1)
    end = first_day_this_month - timedelta(days=1)
    start = end.replace(day=1)

    return start, end


def this_year_range(today):
    return today.replace(month=1, day=1), today


def last_year_range(today):
    start = today.replace(year=today.year - 1, month=1, day=1)
    end = today.replace(month=1, day=1) - timedelta(days=1)

    return start, end


def all_time_range(today):
    return None, None

# AVAILABLE PRESETS

PRESETS = {
    "today": today_range,
    "yesterday": yesterday_range,
    "this_week": this_week_range,
    "last_week": last_week_range,
    "this_month": this_month_range,
    "last_month": last_month_range,
    "this_year": this_year_range,
    "last_year": last_year_range,
    "all": all_time_range,
}

DEFAULT_PRESET = "this_month"

# RESOLVE THE REQUESTED DATE RANGE

def resolve_date_range(request):
    """
    Resolve the date range requested through the URL.

    Supported preset URLs:

        ?preset=today
        ?preset=yesterday
        ?preset=this_week
        ?preset=last_week
        ?preset=this_month
        ?preset=last_month
        ?preset=this_year
        ?preset=last_year
        ?preset=all

    Supported custom range:

        ?start_date=2026-08-01&end_date=2026-08-20

    Custom date ranges take precedence over presets.

    Returns:
        DateRange(start, end, preset)
    """

    today = timezone.localdate()

    preset = request.GET.get("preset")

    start_string = request.GET.get("start_date")
    end_string = request.GET.get("end_date")

    # CUSTOM DATE RANGE
 
    if start_string and end_string:
        try:
            start_date = datetime.strptime(
                start_string,
                "%Y-%m-%d",
            ).date()

            end_date = datetime.strptime(
                end_string,
                "%Y-%m-%d",
            ).date()

            # Do not allow an impossible range.
            if start_date <= end_date:
                return DateRange(
                    start=start_date,
                    end=end_date,
                    preset="custom",
                )

        except ValueError:
            # Invalid dates fall through to the preset/default.
            pass

    # PRESET

    if preset in PRESETS:
        start_date, end_date = PRESETS[preset](today)

        return DateRange(
            start=start_date,
            end=end_date,
            preset=preset,
        )

    # DEFAULT

    start_date, end_date = PRESETS[DEFAULT_PRESET](today)

    return DateRange(
        start=start_date,
        end=end_date,
        preset=DEFAULT_PRESET,
    )

# DETERMINE THE MODEL'S DATE FIELD TYPE

def _get_date_field_type(queryset, date_field):
    """
    Determine whether the supplied model field is a DateField
    or DateTimeField.

    This prevents the filtering system from assuming that every
    date field is a DateTimeField.
    """

    try:
        field = queryset.model._meta.get_field(date_field)

    except FieldDoesNotExist:
        raise ValueError(
            f"'{date_field}' is not a valid field on "
            f"{queryset.model.__name__}."
        )

    if isinstance(field, DateTimeField):
        return "datetime"

    if isinstance(field, DateField):
        return "date"

    raise ValueError(
        f"'{date_field}' on {queryset.model.__name__} "
        f"must be a DateField or DateTimeField."
    )


# ============================================================
# APPLY DATE FILTER
# ============================================================

def apply_date_filters(
    request,
    queryset,
    date_field="created_at",
):
    """
    Apply the requested date range to any Django queryset.

    Works with both:

        DateField
        DateTimeField

    Returns:

        filtered_queryset
        preset
        today
        start_date
        end_date
    """

    date_range = resolve_date_range(request)

    today = timezone.localdate()

    # --------------------------------------------------------
    # ALL TIME
    # --------------------------------------------------------

    if date_range.start is None and date_range.end is None:
        return (
            queryset,
            date_range.preset,
            today,
            None,
            None,
        )

    # --------------------------------------------------------
    # DETERMINE FIELD TYPE
    # --------------------------------------------------------

    field_type = _get_date_field_type(
        queryset,
        date_field,
    )

    # --------------------------------------------------------
    # DATE FIELD
    # --------------------------------------------------------

    if field_type == "date":

        queryset = queryset.filter(
            **{
                f"{date_field}__gte": date_range.start,
                f"{date_field}__lte": date_range.end,
            }
        )

    # --------------------------------------------------------
    # DATETIME FIELD
    # --------------------------------------------------------

    elif field_type == "datetime":

        queryset = queryset.filter(
            **{
                f"{date_field}__date__gte": date_range.start,
                f"{date_field}__date__lte": date_range.end,
            }
        )

    return (
        queryset,
        date_range.preset,
        today,
        date_range.start,
        date_range.end,
    )