from django import template

register = template.Library()


@register.filter
def cf_options_list(cf):
    """Return stripped choice options for a custom field dict."""
    if not isinstance(cf, dict):
        return []
    existing = cf.get("options_list")
    if isinstance(existing, (list, tuple)) and existing:
        return list(existing)
    raw = cf.get("options") or ""
    return [o.strip() for o in str(raw).split(",") if o.strip()]
