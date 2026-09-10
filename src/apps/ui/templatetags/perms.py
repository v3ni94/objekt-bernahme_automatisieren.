from django import template

from apps.accounts.permissions import user_has_permission

register = template.Library()


@register.filter
def has_perm(user, code: str) -> bool:
    try:
        return user_has_permission(user, code)
    except ValueError:
        return False


@register.filter
def get_item(mapping, key):
    try:
        if key in mapping:
            return mapping[key]
        return mapping.get(str(key), [])
    except (AttributeError, TypeError):
        return []


@register.filter
def get_index(sequence, index):
    try:
        return sequence[int(index)]
    except (IndexError, ValueError, TypeError):
        return {}
