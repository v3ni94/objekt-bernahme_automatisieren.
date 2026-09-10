"""Admin-Bereich Konfiguration: Liste je Kategorie, Bearbeitung mit Step-up (docs/architektur.md 9.2)."""

from __future__ import annotations

from allauth.account.decorators import reauthentication_required
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render

from apps.accounts.permissions import permission_required, user_has_permission

from . import store
from .forms import SettingForm


@login_required
def settings_list(request):
    category = request.GET.get("kategorie") or store.categories()[0]
    if category not in store.categories():
        raise Http404
    rows = []
    for key, entry in store.catalog().items():
        if entry["category"] != category:
            continue
        rows.append({"key": key, "entry": entry, "value": store.get(key)})
    return render(
        request,
        "config/list.html",
        {
            "categories": store.categories(),
            "category": category,
            "rows": rows,
            "can_edit": user_has_permission(request.user, "settings.write"),
            "retention_hint": category == "retention",
        },
    )


@permission_required("settings.write")
@reauthentication_required
def setting_edit(request, key: str):
    if key not in store.catalog():
        raise Http404
    current = store.get(key)
    if request.method == "POST":
        form = SettingForm(key, current, request.POST)
        if form.is_valid():
            store.set(
                key,
                form.cleaned_data["value"],
                user=request.user,
                reason=form.cleaned_data.get("reason"),
                request=request,
            )
            messages.success(request, f"{key} gespeichert.")
            return redirect(f"/verwaltung/konfiguration/?kategorie={store.catalog()[key]['category']}")
    else:
        form = SettingForm(key, current)
    return render(
        request,
        "config/edit.html",
        {"form": form, "key": key, "entry": store.catalog()[key], "current": current},
    )
