"""Nutzerverwaltung (Recht users.manage, Step-up TOTP vor jeder Aenderung)."""

from __future__ import annotations

from allauth.account.decorators import reauthentication_required
from allauth.mfa.models import Authenticator
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record

from .forms import UserCreateForm, UserRoleForm
from .models import User
from .permissions import permission_required


@permission_required("users.manage")
def user_list(request):
    users = User.objects.filter(deleted_at__isnull=True).select_related("role").order_by("email")
    totp_users = set(
        Authenticator.objects.filter(type=Authenticator.Type.TOTP).values_list("user_id", flat=True)
    )
    return render(request, "accounts/list.html", {"users": users, "totp_users": totp_users})


@permission_required("users.manage")
@reauthentication_required
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(
                data["email"],
                data["password"],
                role=data["role"],
                display_name=data["display_name"],
                created_by=request.user,
            )
            record(
                "user.create",
                entity_type="user",
                entity_id=user.pk,
                request=request,
                after={"email": user.email, "role": user.role.code},
            )
        messages.success(request, f"Nutzer {user.email} angelegt. TOTP wird beim ersten Login eingerichtet.")
        return redirect("user_list")
    return render(request, "accounts/create.html", {"form": form})


@permission_required("users.manage")
@reauthentication_required
@require_POST
def user_toggle(request, pk: int):
    user = get_object_or_404(User, pk=pk, deleted_at__isnull=True)
    if user.pk == request.user.pk:
        messages.error(request, "Das eigene Konto kann nicht gesperrt werden.")
        return redirect("user_list")
    before = user.status
    user.status = User.Status.DISABLED if user.status == User.Status.ACTIVE else User.Status.ACTIVE
    user.save(update_fields=["status", "updated_at"])
    record(
        "user.status",
        entity_type="user",
        entity_id=user.pk,
        request=request,
        before={"status": before},
        after={"status": user.status},
    )
    messages.success(request, f"{user.email}: Status {user.get_status_display()}.")
    return redirect("user_list")


@permission_required("users.manage")
@reauthentication_required
def user_role(request, pk: int):
    user = get_object_or_404(User, pk=pk, deleted_at__isnull=True)
    form = UserRoleForm(request.POST or None, initial={"role": user.role})
    if request.method == "POST" and form.is_valid():
        before = user.role.code
        user.role = form.cleaned_data["role"]
        user.save(update_fields=["role", "updated_at"])
        record(
            "user.role",
            entity_type="user",
            entity_id=user.pk,
            request=request,
            reason=form.cleaned_data.get("reason"),
            before={"role": before},
            after={"role": user.role.code},
        )
        messages.success(request, f"{user.email}: Rolle {user.role.name}.")
        return redirect("user_list")
    return render(request, "accounts/role.html", {"form": form, "target": user})


@permission_required("users.manage")
@reauthentication_required
@require_POST
def user_reset_totp(request, pk: int):
    """Zweiten Faktor zuruecksetzen; Identitaetspruefung erfolgt ausserhalb des Systems (docs/architektur.md 9.2)."""
    user = get_object_or_404(User, pk=pk, deleted_at__isnull=True)
    deleted, _ = Authenticator.objects.filter(user=user).delete()
    record(
        "auth.totp_reset",
        entity_type="user",
        entity_id=user.pk,
        request=request,
        reason=request.POST.get("reason", "")[:255],
        after={"authenticators_deleted": deleted},
    )
    messages.success(request, f"{user.email}: zweiter Faktor zurückgesetzt, Einrichtung beim nächsten Login.")
    return redirect("user_list")


@permission_required("users.manage")
@reauthentication_required
@require_POST
def user_unlock(request, pk: int):
    user = get_object_or_404(User, pk=pk, deleted_at__isnull=True)
    User.objects.filter(pk=user.pk).update(failed_login_count=0, locked_until=None, updated_at=timezone.now())
    record("user.unlock", entity_type="user", entity_id=user.pk, request=request)
    messages.success(request, f"{user.email}: Sperre aufgehoben.")
    return redirect("user_list")
