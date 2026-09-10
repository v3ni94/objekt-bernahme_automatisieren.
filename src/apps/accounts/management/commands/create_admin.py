"""app-create-admin: ersten oder weiteren Admin anlegen (docs/betrieb.md 4.5)."""

from __future__ import annotations

import getpass
import os

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User
from apps.audit.services import record


class Command(BaseCommand):
    help = "Admin-Nutzer anlegen. Passwort interaktiv oder aus ADMIN_PASSWORD (nur fuer Tests)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--display-name", default="")
        parser.add_argument("--no-input", action="store_true")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        if User.objects.filter(email=email, deleted_at__isnull=True).exists():
            raise CommandError(f"Nutzer {email} existiert bereits")
        password = os.environ.get("ADMIN_PASSWORD")
        if not password and not options["no_input"]:
            password = getpass.getpass("Passwort: ")
            if password != getpass.getpass("Passwort wiederholen: "):
                raise CommandError("Passwoerter stimmen nicht ueberein")
        if not password:
            raise CommandError("Kein Passwort (interaktiv oder ADMIN_PASSWORD)")
        role = Role.objects.get(code=Role.ADMIN)
        user = User.objects.create_user(
            email, password, role=role, display_name=options["display_name"] or email
        )
        record(
            "user.create",
            entity_type="user",
            entity_id=user.pk,
            actor=None,
            after={"email": email, "role": role.code},
        )
        self.stdout.write(f"Admin {email} angelegt. TOTP wird beim ersten Login eingerichtet.")
