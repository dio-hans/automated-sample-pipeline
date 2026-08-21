from django import template
from ..utils.util import PRESETS, DEFAULT_PRESET

register = template.Library()


@register.inclusion_tag("pipeline/preset_bar.html", takes_context=True)
def preset_bar(context):
    request = context["request"]
    current = request.GET.get("preset") or DEFAULT_PRESET
    start = request.GET.get("start_date", "")
    end = request.GET.get("end_date", "")
    return {
        "presets": list(PRESETS.keys()),
        "current": current,
        "start_date": start,
        "end_date": end,
        "is_custom": bool(start and end),
    }