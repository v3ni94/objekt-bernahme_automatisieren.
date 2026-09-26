"""crm_token: Zugangstoken der CRM-Schnittstelle anlegen, auflisten und sperren (Schnittstellenvertrag M29 Stufe 3).

  crm_token anlegen --name crm --scopes objects:read documents:read persons:read
  crm_token liste
  crm_token sperren --id 3   (oder --name crm fuer alle aktiven Tokens dieses Namens)

Der Klartext erscheint genau einmal bei der Anlage; gespeichert wird nur der SHA-256-Hash.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.crm_api.auth import generate_token, hash_token
from apps.crm_api.models import SCOPES, CrmApiToken


def _scopes(values: list[str]) -> list[str]:
    scopes: list[str] = []
    for value in values:
        for part in value.replace(",", " ").split():
            if part not in SCOPES:
                raise CommandError(f"Unbekannter Scope {part}; zulässig: {', '.join(SCOPES)}")
            if part not in scopes:
                scopes.append(part)
    if not scopes:
        raise CommandError("Mindestens einen Scope angeben")
    return scopes


class Command(BaseCommand):
    help = "Token der CRM-Schnittstelle anlegen, auflisten oder sperren"

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="aktion", required=True)
        anlegen = sub.add_parser("anlegen", help="neues Token anlegen, Klartext wird einmal ausgegeben")
        anlegen.add_argument("--name", required=True)
        anlegen.add_argument(
            "--scopes", nargs="+", required=True, help="objects:read, documents:read, persons:read"
        )
        sub.add_parser("liste", help="Tokens mit Status und letzter Nutzung (ohne Klartext)")
        sperren = sub.add_parser("sperren", help="Token sperren")
        gruppe = sperren.add_mutually_exclusive_group(required=True)
        gruppe.add_argument("--id", type=int)
        gruppe.add_argument("--name")

    def handle(self, *args, **options):
        return {"anlegen": self._anlegen, "liste": self._liste, "sperren": self._sperren}[options["aktion"]](
            options
        )

    def _anlegen(self, options):
        name = options["name"].strip()
        if not name:
            raise CommandError("--name darf nicht leer sein")
        scopes = _scopes(options["scopes"])
        raw = generate_token()
        with transaction.atomic():
            token = CrmApiToken.objects.create(name=name[:80], token_hash=hash_token(raw), scopes=scopes)
            record(
                "crm_token.create",
                entity_type="crm_api_token",
                entity_id=token.pk,
                after={"name": token.name, "scopes": scopes},
            )
        self.stdout.write(f"Token {token.pk} ({token.name}) angelegt, Scopes: {' '.join(scopes)}")
        self.stdout.write(
            "Klartext (wird nicht erneut angezeigt, sicher im CRM als OBJEKTAKTE_API_TOKEN ablegen):"
        )
        self.stdout.write(raw)

    def _liste(self, options):
        rows = list(CrmApiToken.objects.order_by("pk"))
        if not rows:
            self.stdout.write("Keine Tokens angelegt.")
            return
        for t in rows:
            used = timezone.localtime(t.last_used_at).strftime("%d.%m.%Y %H:%M") if t.last_used_at else "nie"
            created = timezone.localtime(t.created_at).strftime("%d.%m.%Y %H:%M")
            status = "aktiv" if t.is_active else "gesperrt"
            self.stdout.write(
                f"{t.pk}\t{t.name}\t{status}\t{' '.join(t.scopes or [])}\tangelegt {created}\tzuletzt genutzt {used}"
            )

    def _sperren(self, options):
        qs = CrmApiToken.objects.filter(is_active=True)
        qs = qs.filter(pk=options["id"]) if options.get("id") is not None else qs.filter(name=options["name"])
        tokens = list(qs)
        if not tokens:
            raise CommandError("Kein aktives Token gefunden")
        now = timezone.now()
        with transaction.atomic():
            for t in tokens:
                t.is_active = False
                t.revoked_at = now
                t.save(update_fields=["is_active", "revoked_at", "updated_at"])
                record(
                    "crm_token.revoke", entity_type="crm_api_token", entity_id=t.pk, after={"name": t.name}
                )
        self.stdout.write(f"{len(tokens)} Token gesperrt: {', '.join(str(t.pk) for t in tokens)}")
