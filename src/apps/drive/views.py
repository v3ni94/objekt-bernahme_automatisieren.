"""Drive-Verbindung, Wurzelauflösung, Ordnerabgleich und Protokolle in der Oberfläche (M4 Schritte 1, 5, 6, 7)."""

from __future__ import annotations

from allauth.account.decorators import reauthentication_required
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import permission_required, user_has_permission
from apps.audit.services import record
from apps.config import store
from apps.drive import oauth
from apps.drive.adapter import DriveError
from apps.drive.models import DriveSyncAction, DriveSyncRun
from apps.drive.protocol import run_to_dict, write_json, write_xlsx
from apps.drive.reconcile import nfc
from apps.drive.tasks import reconcile_object_task
from apps.objects.models import ManagedObject


class RootPathForm(forms.Form):
    path = forms.CharField(
        label="Pfad ab Meine Ablage",
        initial="01_Unternehmen/Hausverwaltung Müller GmbH/01_Daten",
        max_length=500,
    )


class RootConfirmForm(forms.Form):
    folder_id = forms.CharField(label="Folder-ID des Wurzelordners", max_length=128)
    reason = forms.CharField(label="Begründung", required=False, max_length=255)


@permission_required("drive.connect")
def drive_admin(request):
    status = oauth.token_status()
    root_id = store.get("drive.root_folder_id")
    root = None
    adapter = None
    error = None
    if status.get("status") == "active":
        try:
            adapter = oauth.get_adapter()
            if adapter is not None and root_id:
                root = adapter.get(root_id)
        except (oauth.OAuthConfigError, DriveError) as exc:
            error = str(exc)
    return render(
        request,
        "drive/admin.html",
        {
            "status": status,
            "root_id": root_id,
            "root": root,
            "error": error,
            "path_form": RootPathForm(),
            "confirm_form": RootConfirmForm(),
            "account": oauth.account_email(),
            "scope": oauth.DRIVE_SCOPE,
            "candidates": request.session.pop("drive_root_candidates", None),
            "proof": oauth.oauth_proof_rows(),
            "days": oauth.days_without_reauthorization(),
        },
    )


@permission_required("drive.connect")
@reauthentication_required
def connect_start(request):
    try:
        url = oauth.build_authorization_url(request.session)
    except oauth.OAuthConfigError as exc:
        messages.error(request, str(exc))
        return redirect("drive_admin")
    return redirect(url)


@login_required
def connect_callback(request):
    if not user_has_permission(request.user, "drive.connect"):
        raise Http404
    error = request.GET.get("error")
    if error:
        messages.error(request, f"Google hat den Ablauf abgebrochen: {error}")
        return redirect("drive_admin")
    try:
        token = oauth.handle_callback(
            request.session,
            code=request.GET.get("code", ""),
            state=request.GET.get("state", ""),
            user=request.user,
            request=request,
        )
    except (oauth.OAuthRejected, oauth.OAuthConfigError) as exc:
        messages.error(request, f"Verbindung abgelehnt: {exc}")
        return redirect("drive_admin")
    messages.success(
        request, f"Google Drive verbunden als {token.account_email}. Jetzt den Wurzelordner bestätigen."
    )
    return redirect("drive_admin")


@permission_required("drive.connect")
@require_POST
def resolve_root(request):
    """Pfadsegmente ab Meine Ablage ablaufen (F 3.2); Treffer je Ebene zur Bestaetigung anzeigen."""
    form = RootPathForm(request.POST)
    if not form.is_valid():
        return redirect("drive_admin")
    adapter = oauth.get_adapter()
    if adapter is None:
        messages.error(request, "Keine Google-Verbindung.")
        return redirect("drive_admin")
    segments = [s for s in form.cleaned_data["path"].replace("\\", "/").split("/") if s.strip()]
    current = ["root"]
    trail = []
    try:
        for seg in segments:
            hits = []
            for parent in current:
                hits += [
                    c
                    for c in adapter.list_children(parent, folders_only=True)
                    if c.is_folder and nfc(c.name) == nfc(seg)
                ]
            if not hits:
                messages.error(request, f"Ordner {seg} nicht gefunden.")
                break
            trail.append({"segment": seg, "hits": [{"id": h.id, "name": h.name} for h in hits]})
            current = [h.id for h in hits]
    except DriveError as exc:
        messages.error(request, f"Drive-Fehler: {exc}")
    request.session["drive_root_candidates"] = trail
    return redirect("drive_admin")


@permission_required("drive.connect")
@reauthentication_required
@require_POST
def confirm_root(request):
    form = RootConfirmForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Folder-ID fehlt.")
        return redirect("drive_admin")
    folder_id = form.cleaned_data["folder_id"].strip()
    adapter = oauth.get_adapter()
    if adapter is not None:
        try:
            node = adapter.get(folder_id)
        except DriveError as exc:
            messages.error(request, f"Drive-Fehler: {exc}")
            return redirect("drive_admin")
        if node is None or not node.is_folder or node.trashed:
            messages.error(request, "Die ID gehört zu keinem aktiven Ordner.")
            return redirect("drive_admin")
    store.set(
        "drive.root_folder_id",
        folder_id,
        user=request.user,
        reason=form.cleaned_data.get("reason") or "Wurzel bestätigt",
        request=request,
    )
    messages.success(request, "Wurzelordner gespeichert.")
    return redirect("drive_admin")


@permission_required("objects.write")
@require_POST
def object_reconcile(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    dry_run = request.POST.get("mode", "dry") != "execute"
    if oauth.token_status().get("status") != "active":
        messages.error(request, "Keine Google-Verbindung; Ordnerabgleich nicht möglich.")
        return redirect("object_detail", pk=obj.pk)
    if not store.get("drive.root_folder_id"):
        # Ohne Wurzel wuerde der Lauf sofort mit ReconcileError scheitern; der Hinweis fuehrt zur Einrichtung.
        messages.error(
            request,
            "Der Wurzelordner ist noch nicht bestätigt. Unter Google Drive den Pfad auflösen und die Wurzel "
            "bestätigen, danach den Ordnerabgleich starten.",
        )
        return redirect("drive_admin")
    reconcile_object_task.delay(obj.pk, dry_run, request.user.pk, "manual")
    record(
        "drive.reconcile",
        entity_type="object",
        entity_id=obj.pk,
        object_id=obj.pk,
        request=request,
        after={"dry_run": dry_run},
    )
    messages.success(
        request, f"Ordnerabgleich {'als Probelauf' if dry_run else 'zur Ausführung'} eingereiht."
    )
    return redirect("sync_run_list", pk=obj.pk)


@login_required
def sync_run_list(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    runs = DriveSyncRun.objects.filter(object=obj).order_by("-started_at")[:100]
    return render(
        request,
        "drive/runs.html",
        {
            "object": obj,
            "runs": runs,
            "can_write": user_has_permission(request.user, "objects.write"),
            "connected": oauth.token_status().get("status") == "active",
        },
    )


@login_required
def sync_run_detail(request, pk: int):
    run = get_object_or_404(DriveSyncRun.objects.select_related("object"), pk=pk)
    actions = DriveSyncAction.objects.filter(sync_run=run).order_by("seq_no")
    summary = run.summary or {}
    return render(
        request,
        "drive/run.html",
        {
            "run": run,
            "object": run.object,
            "actions": actions,
            "notes": summary.get("notes", {}),
            "categories": summary.get("categories", {}),
            "hints": summary.get("hints", []),
            "inventory": summary.get("inventory", {}),
            "reviews": summary.get("reviews", []),
        },
    )


@login_required
def sync_run_json(request, pk: int):
    run = get_object_or_404(DriveSyncRun, pk=pk)
    return JsonResponse(
        run_to_dict(run), json_dumps_params={"ensure_ascii": False, "indent": 2, "default": str}
    )


@login_required
def sync_run_export(request, pk: int, fmt: str):
    run = get_object_or_404(DriveSyncRun, pk=pk)
    if fmt == "json":
        path = write_json(run)
    elif fmt == "xlsx":
        path = write_xlsx(run)
    else:
        raise Http404
    if not str(path.resolve()).startswith(str(settings.OBJEKTAKTE["DATA_DIR"].resolve())):
        raise Http404
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)
