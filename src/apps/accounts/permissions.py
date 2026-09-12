"""Rechteprüfung in Views und Service-Schicht (docs/architektur.md 9.1). Verweigerungen werden protokolliert."""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

# Rechtematrix aus docs/architektur.md 9.1; Seed in db/seeds/roles.json
PERMISSIONS = {
    "settings.write": "Konfiguration ändern",
    "retention.approve": "Aufbewahrungsfristen setzen und freigeben",
    "users.manage": "Nutzer anlegen, sperren, Rolle ändern, TOTP zurücksetzen",
    "deletion.approve": "Löschläufe freigeben",
    "objects.write": "Objekte anlegen und bearbeiten, Ordnerabgleich",
    "documents.ingest": "Dokumente hochladen, Verarbeitung starten",
    "review.decide": "Review Center bearbeiten, Massenbearbeitung",
    "review.dismiss_object_case": "Fälle zur Objektzuordnung verwerfen",
    "demands.write": "Nachforderung erzeugen (Entwurf)",
    "demands.approve": "Nachforderung freigeben",
    "owner_files.read": "Eigentümerakten sehen",
    "tenant_files.read": "Mieterakten sehen",
    "masterdata.write": "Stammdaten ändern",
    "lists.generate": "Listen erzeugen und herunterladen",
    "status.read": "Statusseite lesen",
    "status.operate": "Statusaktionen (Sweeper, Google-Verbindung erneuern, Listen erzeugen)",
    "audit.read_all": "Audit vollständig einsehen",
    "audit.read_own": "Eigene Audit-Einträge einsehen",
    "ai.read": "KI-Aufrufe und Kosten einsehen",
    "imports.write": "Import hochladen und bestätigen",
    "drive.connect": "Google-Drive-Verbindung herstellen und trennen",
    "drive.cleanup": "Altbestand aufräumen (Dubletten, Temporärdateien und leere Ordner in den Papierkorb)",
    "sync.manage": "Synchronisation mit Paperless und Drive verwalten (Verbindung, Bestandsläufe, Operationen)",
    "inbox.work": "Dokumenteneingang bearbeiten (Objektzuordnung, Konflikte, Dublettenverdacht)",
}


def user_has_permission(user, code: str) -> bool:
    if code not in PERMISSIONS:
        raise ValueError(f"Unbekannter Berechtigungsschlüssel {code}")
    return bool(user and user.is_authenticated and user.has_permission(code))


def permission_required(code: str):
    """Decorator fuer Funktionsviews: Login noetig, sonst 403 mit Audit-Eintrag auth.denied."""

    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not user_has_permission(request.user, code):
                from apps.audit.services import record

                record(
                    "auth.denied",
                    entity_type="permission",
                    request=request,
                    reason=code,
                    after={"permission": code, "path": request.path},
                )
                raise PermissionDenied(f"Recht {code} fehlt")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
