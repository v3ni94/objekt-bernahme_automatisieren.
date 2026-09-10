"""Erzeugt den generierten Teil des Bezeichnerregisters (docs/architektur/bezeichnerregister.md, Beschluss B-01)
aus dem Modellregister, den Wertevorraeten (TextChoices), dem Konfigurationskatalog, den Audit-Aktionen,
den Queues und der .env-Vorlage. Der Test tests/unit/test_bezeichnerregister.py vergleicht die Ausgabe mit
der eingecheckten Datei; neue Bezeichner ohne Registeraktualisierung lassen die CI fehlschlagen."""

from __future__ import annotations

import json
import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, models

from apps.audit.actions import AUDIT_ACTIONS
from apps.config import store

MARKER = "<!-- generiert: ab hier nicht von Hand aendern; Erzeugung: manage.py bezeichnerregister -->"

DIRECTORIES = [
    "db",
    "redis",
    "transit",
    "work",
    "ocr-cache",
    "previews",
    "models",
    "lists",
    "imports",
    "requests",
    "exports",
    "backup",
    "secrets",
    "deploy",
]
SECRETS = [
    "db_root_password",
    "db_app_password",
    "db_worker_password",
    "db_migrate_password",
    "db_backup_password",
    "db_ro_password",
    "redis_password",
    "app_secret_key",
    "iban_key",
    "iban_hmac_key",
    "token_key",
    "totp_key",
    "google_client_secret",
    "openai_api_key",
    "anthropic_api_key",
    "smtp_password",
    "readyz_token",
    "backup_age_recipient",
    "rclone_conf",
]
CONTAINERS = [
    ("web", "keine", "web", "data, egress, Traefik-Netz"),
    ("worker", "ocr", "worker", "data, egress"),
    ("worker-nlp", "classify", "worker", "data"),
    ("worker-io", "ai, io, lists", "worker", "data, egress"),
    ("beat", "keine (Zeitplan)", "web", "data, egress"),
    ("db", "", "MariaDB-Image", "data"),
    ("redis", "", "Redis-Image", "data"),
    ("backup", "", "eigenes Image", "data, egress nur bei Offsite-Kopie"),
    ("classifier (optional, Profil)", "classify", "worker", "data"),
]


def _db_type(field, conn) -> str:
    if isinstance(field, models.GeneratedField):
        return f"{field.output_field.db_type(conn)} GENERATED"
    t = field.db_type(conn) or ""
    return t.replace("bool", "tinyint(1)") if t == "bool" else t


def render(conn=None) -> str:
    conn = conn or connection
    out: list[str] = [MARKER, ""]
    out.append("## Tabellen und Spalten")
    out.append("")
    out.append(
        "Quelle: Django-Modellregister (`db_table`, `db_column`). Typ nach MariaDB-Backend. Wertevorräte als CHECK-Constraint in der Datenbank."
    )
    out.append("")
    model_list = sorted(apps.get_models(), key=lambda m: (m._meta.app_label, m._meta.db_table))
    for model in model_list:
        meta = model._meta
        if meta.app_label in ("auth", "contenttypes", "sessions"):
            continue
        out.append(f"### {meta.db_table}")
        out.append("")
        out.append(
            f"Modell `{meta.app_label}.{model.__name__}`. {meta.verbose_name_plural if meta.app_label not in ('account', 'mfa') else 'Tabelle der Bibliothek django-allauth'}."
        )
        out.append("")
        out.append("| Spalte | Typ | Null | Verweis oder Wertevorrat |")
        out.append("|---|---|---|---|")
        for f in meta.local_fields:
            ref = ""
            if f.is_relation and f.related_model is not None:
                ref = f"→ {f.related_model._meta.db_table}.{f.target_field.column}"
            elif getattr(f, "choices", None):
                ref = ", ".join(str(c[0]) for c in f.choices)
            out.append(f"| {f.column} | {_db_type(f, conn)} | {'ja' if f.null else 'nein'} | {ref} |")
        names = [c.name for c in meta.constraints] + [i.name for i in meta.indexes]
        if names:
            out.append("")
            out.append("Constraints und Indizes: " + ", ".join(f"`{n}`" for n in names))
        out.append("")
    out.append("## Container und Queues")
    out.append("")
    out.append("| Container | Queues | Build-Target | Netze |")
    out.append("|---|---|---|---|")
    for row in CONTAINERS:
        out.append("| " + " | ".join(row) + " |")
    out.append("")
    out.append(
        "Celery-Queues aus den Settings: " + ", ".join(f"`{q}`" for q in sorted(settings.CELERY_TASK_QUEUES))
    )
    out.append("")
    out.append("## Verzeichnisse unter /srv/objektakte/")
    out.append("")
    out.append(", ".join(f"`{d}`" for d in DIRECTORIES) + " (Beschluss B-14)")
    out.append("")
    out.append("## Secrets (Dateinamen unter /srv/objektakte/secrets/)")
    out.append("")
    out.append(", ".join(f"`{s}`" for s in SECRETS))
    out.append("")
    out.append("## Konfigurationsschlüssel app_settings")
    out.append("")
    out.append("| Schlüssel | Kategorie | Typ | Seed |")
    out.append("|---|---|---|---|")
    seeds = store.seeds()
    for key, entry in sorted(store.catalog().items()):
        # Der Altname des Auffangordners darf nur in db/seeds/ stehen (B-22, Definition of Done)
        if key == "drive.legacy_folder_aliases":
            seed = "siehe db/seeds/app_settings.json (einzige Fundstelle der Altbezeichnung)"
        else:
            seed = json.dumps(seeds.get(key), ensure_ascii=False)
        if len(seed) > 90:
            seed = seed[:87] + "..."
        out.append(
            f"| {key} | {entry['category']} | {entry.get('value_type') or entry.get('type')} | `{seed}` |"
        )
    out.append("")
    out.append("## Audit-Aktionen")
    out.append("")
    out.append("| Aktion | Bedeutung |")
    out.append("|---|---|")
    for action, text in AUDIT_ACTIONS.items():
        out.append(f"| {action} | {text} |")
    out.append("")
    out.append("## Startparameter (.env)")
    out.append("")
    env = Path(settings.REPO_DIR) / ".env.example"
    names = re.findall(r"^([A-Z][A-Z0-9_]+)=", env.read_text(encoding="utf-8"), re.M) if env.exists() else []
    out.append(", ".join(f"`{n}`" for n in names))
    out.append("")
    out.append("## Rechte (roles.permissions)")
    out.append("")
    from apps.accounts.permissions import PERMISSIONS

    out.append("| Recht | Bedeutung |")
    out.append("|---|---|")
    for code, text in PERMISSIONS.items():
        out.append(f"| {code} | {text} |")
    out.append("")
    return "\n".join(out)


class Command(BaseCommand):
    help = "Generierten Teil des Bezeichnerregisters ausgeben oder in die Datei schreiben (--write)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--write", action="store_true", help="docs/architektur/bezeichnerregister.md aktualisieren"
        )

    def handle(self, *args, **options):
        text = render()
        if options["write"]:
            path = Path(settings.REPO_DIR) / "docs" / "architektur" / "bezeichnerregister.md"
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            head = current.split(MARKER)[0] if MARKER in current else current
            path.write_text(head.rstrip() + "\n\n" + text, encoding="utf-8")
            self.stdout.write(f"{path} aktualisiert")
        else:
            self.stdout.write(text)
