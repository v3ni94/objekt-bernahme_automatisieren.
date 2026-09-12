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


# ---------------------------------------------------------------- Bestand aus Drive uebernehmen (11.09.2026)
@permission_required("objects.write")
def object_takeover(request, pk: int):
    """Vorhandene Ordnerstruktur durchsehen (Wurzelordner abwaerts oder beliebiger Ordner per ID oder Link) und
    Dateien in das Objekt uebernehmen: ausgewaehlte Dateien, alle Dateien des Ordners oder mit Unterordnern.
    Die Dateien bleiben in Drive und werden von der Verarbeitung in die neue Struktur verschoben."""
    from apps.drive import takeover

    obj = get_object_or_404(ManagedObject.active, pk=pk)
    adapter = oauth.get_adapter()
    root_id = store.get("drive.root_folder_id")
    if adapter is None:
        messages.error(request, "Keine Google-Verbindung. Der Admin verbindet Google Drive unter Verwaltung.")
        return redirect("document_list", pk=obj.pk)
    ref = request.POST.get("folder") if request.method == "POST" else request.GET.get("ordner")
    folder_id = takeover.parse_folder_ref(ref or "")
    if ref and not folder_id:
        messages.warning(
            request, "Eingabe nicht als Ordner-ID oder Drive-Link erkannt; Wurzelordner wird gezeigt."
        )
    folder_id = folder_id or root_id
    if not folder_id:
        messages.error(request, "Kein Startordner: Wurzelordner setzen oder eine Ordner-ID eingeben.")
        return redirect("document_list", pk=obj.pk)
    try:
        folder = adapter.get(folder_id)
        if folder is None or not folder.is_folder or folder.trashed:
            messages.error(request, "Der angegebene Ordner wurde in Drive nicht gefunden.")
            return redirect("object_takeover", pk=obj.pk)
        if request.method == "POST" and request.POST.get("mode"):
            return _takeover_register(request, obj, adapter, folder)
        folders, files = takeover.list_folder(adapter, folder_id)
        crumbs = takeover.breadcrumb(adapter, folder, root_id)
    except DriveError as exc:
        messages.error(request, f"Drive-Fehler: {exc}")
        return redirect("document_list", pk=obj.pk)
    return render(
        request,
        "drive/takeover.html",
        {
            "object": obj,
            "folder": folder,
            "crumbs": crumbs,
            "folders": folders,
            "files": files,
            "selectable": sum(1 for f in files if f.selectable),
            "is_root": folder.id == root_id,
            "limit": takeover.max_files(),
        },
    )


def _takeover_register(request, obj, adapter, folder):
    from apps.drive import takeover

    mode = request.POST.get("mode")
    limit = takeover.max_files()
    try:
        if mode == "selected":
            ids = set(request.POST.getlist("files"))
            if not ids:
                messages.warning(request, "Keine Datei ausgewählt.")
                return redirect(f"{request.path}?ordner={folder.id}")
            entries = takeover.collect_files(adapter, folder, recursive=False, limit=limit, only_ids=ids)
        elif mode in ("folder", "recursive"):
            entries = takeover.collect_files(adapter, folder, recursive=(mode == "recursive"), limit=limit)
        else:
            messages.error(request, "Unbekannte Aktion.")
            return redirect(f"{request.path}?ordner={folder.id}")
    except takeover.TakeoverError as exc:
        messages.error(request, str(exc))
        return redirect(f"{request.path}?ordner={folder.id}")
    if not entries:
        messages.warning(request, f"Im Ordner „{folder.name}“ liegen keine Dateien.")
        return redirect(f"{request.path}?ordner={folder.id}")
    try:
        result = takeover.register_files(
            obj, entries, source_folder=folder, user=request.user, request=request
        )
    except takeover.TakeoverError as exc:
        messages.error(request, str(exc))
        return redirect(f"{request.path}?ordner={folder.id}")
    if result.registered:
        messages.success(
            request,
            f"{result.registered} Datei(en) aus „{folder.name}“ in Objekt {obj.object_number} übernommen; "
            f"Lauf {result.run_id} verarbeitet sie und legt sie in der neuen Struktur ab. "
            "Die Quellordner bleiben bestehen, es wird nichts gelöscht.",
        )
    else:
        messages.info(request, "Keine neue Datei übernommen.")
    for note in result.notes:
        messages.info(request, note)
    return redirect(f"{request.path}?ordner={folder.id}")


# ---------------------------------------------------------------- Altbestand-Tabelle (11.09.2026)
def _altbestand_zurueck():
    return redirect("takeover_sources")


@permission_required("objects.write")
def takeover_sources(request):
    """Tabelle der Quellordner aus der bisherigen Ablage: aufnehmen (Links einfuegen), Namen und Objektnummern
    aufloesen, je Zeile „Aufarbeiten“ (alle Dateien samt Unterordnern in das Zielobjekt) oder entfernen."""
    from apps.drive import takeover
    from apps.drive.models import TakeoverSource, TakeoverStatus

    rows = takeover.sorted_sources(TakeoverSource.objects.select_related("object"))
    counts = {
        "total": len(rows),
        "unresolved": sum(1 for r in rows if r.resolved_at is None),
        "open": sum(1 for r in rows if r.object_id is None),
        "done": sum(1 for r in rows if r.status == TakeoverStatus.DONE),
        "failed": sum(1 for r in rows if r.status == TakeoverStatus.FAILED),
    }
    return render(
        request,
        "drive/takeover_sources.html",
        {
            "rows": rows,
            "counts": counts,
            "connected": oauth.token_status().get("status") == "active",
            "limit": takeover.max_files(),
        },
    )


@permission_required("objects.write")
@require_POST
def takeover_source_add(request):
    from apps.drive import takeover

    refs = takeover.parse_folder_refs(request.POST.get("links", ""))
    if not refs:
        messages.warning(request, "Keine Ordner-ID oder kein Drive-Link erkannt.")
        return _altbestand_zurueck()
    created, existing = takeover.add_sources(refs, user=request.user)
    record(
        "drive.takeover_source_add",
        entity_type="takeover_source",
        request=request,
        after={"created": len(created), "existing": existing},
    )
    resolved = failed = 0
    adapter = oauth.get_adapter()
    if adapter is not None and created:
        try:
            for src in created:
                if takeover.resolve_source(src, adapter):
                    resolved += 1
                else:
                    failed += 1
        except DriveError as exc:
            messages.warning(request, f"Namen konnten nicht vollständig gelesen werden: {exc}")
    text = f"{len(created)} Ordner aufgenommen, {existing} waren bereits in der Tabelle."
    if created:
        text += (
            f" Aufgelöst: {resolved}, nicht gefunden: {failed}."
            if adapter
            else " Namen folgen nach dem Verbinden mit Google Drive."
        )
    messages.success(request, text)
    return _altbestand_zurueck()


@permission_required("objects.write")
@require_POST
def takeover_source_resolve(request):
    from apps.drive import takeover
    from apps.drive.models import TakeoverSource

    adapter = oauth.get_adapter()
    if adapter is None:
        messages.error(request, "Keine Google-Verbindung.")
        return _altbestand_zurueck()
    qs = (
        TakeoverSource.objects.all()
        if request.POST.get("alle")
        else TakeoverSource.objects.filter(resolved_at__isnull=True)
    )
    ok = failed = 0
    try:
        for src in qs:
            if takeover.resolve_source(src, adapter):
                ok += 1
            else:
                failed += 1
    except DriveError as exc:
        messages.error(request, f"Drive-Fehler: {exc}")
    messages.success(request, f"{ok} Ordner aufgelöst, {failed} nicht gefunden.")
    return _altbestand_zurueck()


@permission_required("objects.write")
@require_POST
def takeover_source_refresh(request):
    """Alle Quellordner neu lesen; geloeschte oder im Papierkorb liegende Ordner verschwinden aus der Tabelle."""
    from apps.drive import takeover

    adapter = oauth.get_adapter()
    if adapter is None:
        messages.error(request, "Keine Google-Verbindung.")
        return _altbestand_zurueck()
    r = takeover.refresh_sources(adapter, user=request.user, request=request)
    text = (
        f"{r['checked']} Ordner geprüft, {r['updated']} aktualisiert, "
        f"{r['removed']} nicht mehr in Drive vorhanden und aus der Liste entfernt."
    )
    if r["errors"]:
        text += f" {r['errors']} nicht lesbar, Zeile bleibt mit Hinweis stehen."
    messages.success(request, text)
    return _altbestand_zurueck()


@permission_required("objects.write")
@require_POST
def takeover_source_run(request, pk: int):
    from apps.drive import takeover
    from apps.drive.models import TakeoverSource

    src = get_object_or_404(TakeoverSource, pk=pk)
    adapter = oauth.get_adapter()
    if adapter is None:
        messages.error(request, "Keine Google-Verbindung.")
        return _altbestand_zurueck()
    try:
        result = takeover.run_source(src, adapter, user=request.user, request=request)
    except takeover.TakeoverError as exc:
        messages.error(request, f"„{src.name or src.drive_folder_id}“: {exc}")
        return _altbestand_zurueck()
    except DriveError as exc:
        messages.error(request, f"Drive-Fehler bei „{src.name or src.drive_folder_id}“: {exc}")
        return _altbestand_zurueck()
    obj = src.object
    if result.registered:
        messages.success(
            request,
            f"{result.registered} Datei(en) aus „{src.name}“ in Objekt {obj.object_number} übernommen; "
            f"Lauf {result.run_id} legt sie in der neuen Struktur ab. Der Quellordner bleibt bestehen.",
        )
    else:
        messages.info(request, f"„{src.name}“: keine neue Datei zu übernehmen.")
    for note in result.notes:
        messages.info(request, note)
    return _altbestand_zurueck()


@permission_required("objects.write")
@require_POST
def takeover_source_remove(request, pk: int):
    from apps.drive.models import TakeoverSource

    src = get_object_or_404(TakeoverSource, pk=pk)
    record(
        "drive.takeover_source_remove",
        entity_type="takeover_source",
        entity_id=src.pk,
        object_id=src.object_id,
        request=request,
        before={"drive_folder_id": src.drive_folder_id, "name": src.name, "status": src.status},
    )
    src.delete()
    messages.success(request, "Zeile aus der Tabelle entfernt. In Drive wurde nichts verändert.")
    return _altbestand_zurueck()
