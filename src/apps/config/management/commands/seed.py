"""app-seed: Rollen und app_settings aus db/seeds/ anlegen (idempotent; --force ueberschreibt Werte)."""

from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.accounts.models import Role
from apps.accounts.permissions import PERMISSIONS
from apps.config import store


class Command(BaseCommand):
    help = "Seeds laden: Rollen, app_settings (weitere Kataloge folgen in M2)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true", help="vorhandene Werte mit den Seeds ueberschreiben"
        )

    def handle(self, *args, **options):
        seed_dir = settings.OBJEKTAKTE["SEED_DIR"]
        roles = json.loads((seed_dir / "roles.json").read_text(encoding="utf-8"))
        for entry in roles:
            unknown = set(entry["permissions"]) - set(PERMISSIONS)
            if unknown:
                raise SystemExit(f"Rolle {entry['code']}: unbekannte Rechte {sorted(unknown)}")
            role, created = Role.objects.get_or_create(
                code=entry["code"],
                defaults={"name": entry["name"], "permissions": entry["permissions"], "is_system": True},
            )
            if not created and (role.permissions != entry["permissions"] or role.name != entry["name"]):
                role.permissions = entry["permissions"]
                role.name = entry["name"]
                role.save(update_fields=["permissions", "name", "updated_at"])
                self.stdout.write(f"Rolle {role.code} aktualisiert")
            elif created:
                self.stdout.write(f"Rolle {role.code} angelegt")
        created, updated = store.seed_missing(force=options["force"])
        self.stdout.write(
            f"app_settings: {created} angelegt, {updated} aktualisiert, {len(store.catalog())} im Katalog"
        )
