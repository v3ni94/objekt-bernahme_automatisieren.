from django import template

from apps.accounts.permissions import user_has_permission

register = template.Library()


@register.filter
def has_perm(user, code: str) -> bool:
    try:
        return user_has_permission(user, code)
    except ValueError:
        return False
