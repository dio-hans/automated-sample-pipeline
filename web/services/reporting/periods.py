from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.utils import timezone


@dataclass(frozen=True)
class ReportPeriod:
    preset: str
    start_date: object | None
    end_date: object | None
    start_dt: datetime | None
    end_dt: datetime | None
    label: str

    def filter_datetime(self, queryset, field_name):
        if self.start_dt is None or self.end_dt is None:
            return queryset

        return queryset.filter(
            **{
                f"{field_name}__gte": self.start_dt,
                f"{field_name}__lt": self.end_dt,
            }
        )

    def filter_date(self, queryset, field_name):
        if self.start_date is None or self.end_date is None:
            return queryset

        return queryset.filter(
            **{
                f"{field_name}__gte": self.start_date,
                f"{field_name}__lte": self.end_date,
            }
        )


def resolve_report_period(request):
    today = timezone.localdate()

    preset = request.GET.get("period", "this_month").strip()

    if preset == "today":
        start_date = today
        end_date = today
        label = "Today"

    elif preset == "yesterday":
        start_date = today - timedelta(days=1)
        end_date = start_date
        label = "Yesterday"

    elif preset == "this_week":
        start_date = today - timedelta(days=today.weekday())
        end_date = today
        label = "This Week"

    elif preset == "this_month":
        start_date = today.replace(day=1)
        end_date = today
        label = "This Month"

    elif preset == "overall":
        return ReportPeriod(
            preset="overall",
            start_date=None,
            end_date=None,
            start_dt=None,
            end_dt=None,
            label="Overall",
        )

    elif preset == "custom":
        try:
            start_date = datetime.strptime(
                request.GET.get("start_date", ""),
                "%Y-%m-%d",
            ).date()

            end_date = datetime.strptime(
                request.GET.get("end_date", ""),
                "%Y-%m-%d",
            ).date()

            if start_date > end_date:
                start_date, end_date = end_date, start_date

            label = (
                f"{start_date.strftime('%d %b %Y')} – "
                f"{end_date.strftime('%d %b %Y')}"
            )

        except (TypeError, ValueError):
            start_date = today.replace(day=1)
            end_date = today
            preset = "this_month"
            label = "This Month"

    else:
        start_date = today.replace(day=1)
        end_date = today
        preset = "this_month"
        label = "This Month"

    start_dt = timezone.make_aware(
        datetime.combine(start_date, time.min)
    )

    end_dt = timezone.make_aware(
        datetime.combine(
            end_date + timedelta(days=1),
            time.min,
        )
    )

    return ReportPeriod(
        preset=preset,
        start_date=start_date,
        end_date=end_date,
        start_dt=start_dt,
        end_dt=end_dt,
        label=label,
    )