"""app-seed: Rollen, Katalog (Kategorien, Unterordner, Dokumentunterarten), Aufbewahrungsfristen und app_settings
aus db/seeds/ anlegen (Beschluss B-22). Idempotent: ein zweiter Lauf aendert nichts. --force ueberschreibt
Konfigurationswerte, die von den Seeds abweichen (protokolliert)."""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Role
from apps.accounts.permissions import PERMISSIONS
from apps.config import store
from apps.documents.models import DocumentCategory, DocumentSubfolder, DocumentType, RetentionPolicy


def _load(seed_dir: Path, name: str) -> list[dict]:
    path = seed_dir / name
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _upsert(model, lookup: dict, values: dict) -> str:
    obj, created = model.objects.get_or_create(**lookup, defaults=values)
    if created:
        return "created"
    changed = [k for k, v in values.items() if getattr(obj, k) != v]
    if changed:
        for k in changed:
            setattr(obj, k, values[k])
        obj.save(update_fields=[*changed, "updated_at"])
        return "updated"
    return "unchanged"


class Command(BaseCommand):
    help = "Seeds laden: Rollen, Dokumentkatalog, Aufbewahrungsfristen, app_settings"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true", help="vorhandene Konfigurationswerte mit den Seeds ueberschreiben"
        )

    def handle(self, *args, **options):
        seed_dir = settings.OBJEKTAKTE["SEED_DIR"]
        with transaction.atomic():
            self.seed_roles(seed_dir)
            self.seed_catalog(seed_dir)
        created, updated = store.seed_missing(force=options["force"])
        self.stdout.write(
            f"app_settings: {created} angelegt, {updated} aktualisiert, {len(store.catalog())} im Katalog"
        )

    def seed_roles(self, seed_dir: Path) -> None:
        for entry in _load(seed_dir, "roles.json"):
            unknown = set(entry["permissions"]) - set(PERMISSIONS)
            if unknown:
                raise SystemExit(f"Rolle {entry['code']}: unbekannte Rechte {sorted(unknown)}")
            result = _upsert(
                Role,
                {"code": entry["code"]},
                {"name": entry["name"], "permissions": entry["permissions"], "is_system": True},
            )
            if result != "unchanged":
                self.stdout.write(
                    f"Rolle {entry['code']} {'angelegt' if result == 'created' else 'aktualisiert'}"
                )

    def seed_catalog(self, seed_dir: Path) -> None:
        counts: dict[str, dict[str, int]] = {}

        def tally(table: str, result: str) -> None:
            counts.setdefault(table, {"created": 0, "updated": 0, "unchanged": 0})[result] += 1

        for c in _load(seed_dir, "document_categories.json"):
            tally(
                "document_categories",
                _upsert(
                    DocumentCategory,
                    {"code": c["code"]},
                    {
                        "folder_name": c["folder_name"],
                        "display_name": c["display_name"],
                        "scope": c["scope"],
                        "sort_order": c["sort_order"],
                    },
                ),
            )
        for s in _load(seed_dir, "document_subfolders.json"):
            tally(
                "document_subfolders",
                _upsert(
                    DocumentSubfolder,
                    {"category_id": s["category_code"], "code": s["code"]},
                    {
                        "folder_name": s["folder_name"],
                        "display_name": s["display_name"],
                        "sort_order": s["sort_order"],
                    },
                ),
            )
        subfolders = {(sf.category_id, sf.code): sf for sf in DocumentSubfolder.objects.all()}
        for t in _load(seed_dir, "document_types.json"):
            sub = (
                subfolders.get((t["category_code"], t["subfolder_code"])) if t.get("subfolder_code") else None
            )
            if t.get("subfolder_code") and sub is None:
                raise SystemExit(
                    f"Dokumentunterart {t['code']}: Unterordner {t['subfolder_code']} in {t['category_code']} fehlt"
                )
            tally(
                "document_types",
                _upsert(
                    DocumentType,
                    {"code": t["code"]},
                    {
                        "category_id": t["category_code"],
                        "subfolder": sub,
                        "name": t["name"],
                        "requires_period": bool(t.get("requires_period")),
                        "requires_owner": bool(t.get("requires_owner")),
                        "requires_tenant": bool(t.get("requires_tenant")),
                        "keywords": t.get("keywords") or [],
                    },
                ),
            )
        for r in _load(seed_dir, "retention_policies.json"):
            # Werte legt die Geschaeftsfuehrung mit dem Steuerberater fest (F31); der Seed legt nur die Zeile ohne Frist an
            # und ueberschreibt nie eine gesetzte Frist.
            exists = RetentionPolicy.objects.filter(
                category_id=r["category_code"], subfolder__isnull=True, document_type__isnull=True
            ).exists()
            if not exists:
                RetentionPolicy.objects.create(
                    category_id=r["category_code"], retention_basis=r.get("retention_basis")
                )
                tally("retention_policies", "created")
            else:
                tally("retention_policies", "unchanged")
        for table, c in counts.items():
            self.stdout.write(
                f"{table}: {c['created']} angelegt, {c['updated']} aktualisiert, {c['unchanged']} unverändert"
            )
