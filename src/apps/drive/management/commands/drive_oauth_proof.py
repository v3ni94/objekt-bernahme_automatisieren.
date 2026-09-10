"""Nachweis T9 (8 Tage Token-Refresh ohne Neuanmeldung) als Markdown-Tabelle ohne Chiffrate (docs/betrieb.md 7.9)."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.drive import oauth


class Command(BaseCommand):
    help = "Token-Metadaten und Refresh-Ereignisse als Tabelle ausgeben (fuer docs/betrieb/oauth-nachweis.md)"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=8)

    def handle(self, *args, **options):
        status = oauth.token_status()
        self.stdout.write("| Feld | Wert |\n|---|---|")
        for k in (
            "account",
            "status",
            "authorized_at",
            "days_since_authorization",
            "last_refresh_at",
            "last_refresh_status",
            "consecutive_failures",
        ):
            v = status.get(k)
            if hasattr(v, "strftime"):
                v = v.strftime("%d.%m.%Y %H:%M")
            self.stdout.write(f"| {k} | {v} |")
        self.stdout.write("\n| Zeitpunkt | Ereignis | Ergebnis | Grund |\n|---|---|---|---|")
        rows = oauth.oauth_proof_rows(options["days"])
        for r in rows:
            self.stdout.write(f"| {r['occurred_at']} | {r['action']} | {r['result']} | {r['reason'] or ''} |")
        authorizations = [r for r in rows if r["action"] == "drive.authorize" and r["result"] == "ok"]
        days = status.get("days_since_authorization")
        ok = (
            days is not None
            and days >= options["days"]
            and len(authorizations) <= 1
            and all(
                r["result"] in ("ok", "read_test_ok") for r in rows if r["action"] == "drive.token_refresh"
            )
        )
        self.stdout.write(
            f"\nKriterium erfüllt: {'ja' if ok else 'nein'} (Tage seit Autorisierung: {days}, Autorisierungen im Zeitraum: {len(authorizations)})"
        )
