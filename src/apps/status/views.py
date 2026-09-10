from __future__ import annotations

import hmac

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from apps.accounts.permissions import permission_required, user_has_permission

from . import checks


@never_cache
def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def _token_ok(request) -> bool:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    token = settings.OBJEKTAKTE.get("READYZ_TOKEN") or ""
    if not header.startswith("Bearer ") or not token:
        return False
    return hmac.compare_digest(header[7:].strip(), token)


@never_cache
def readyz(request):
    user = getattr(request, "user", None)
    if not (_token_ok(request) or (user is not None and user.is_authenticated and user.is_admin)):
        return JsonResponse({"error": "nicht autorisiert"}, status=401)
    ok, report = checks.readiness()
    return JsonResponse({"ok": ok, **report}, status=200 if ok else 503)


@permission_required("status.read")
def status_page(request):
    ok, report = checks.readiness()
    return render(
        request,
        "status/page.html",
        {
            "ok": ok,
            "report": report,
            "heartbeats": checks.heartbeats(),
            "backup": checks.backup_status(),
            "can_operate": user_has_permission(request.user, "status.operate"),
            "image_tag": settings.OBJEKTAKTE.get("IMAGE_TAG", ""),
        },
    )
