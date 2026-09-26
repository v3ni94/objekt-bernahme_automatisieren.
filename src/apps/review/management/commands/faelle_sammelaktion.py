"""faelle_sammelaktion: Pruefrueckstand je Objekt und Unterart abarbeiten (26.09.2026, docs/plan/pruefrueckstand.md).

Aktionen (alle ueber die Dienste des Pruefcenters, apps.review.sammelaktion):
- kandidat-bestaetigen: Faelle below_threshold (unclear und move_proposal) mit context.intended ueber den Weg
  „besten Kandidaten bestaetigen" (bulk_execute, apply_decision) erledigen, nur ab --min-konfidenz; die Ablage
  folgt als Job file_to_drive wie bei jeder Entscheidung.
- verwerfen: Faelle ai_sample (oder per --unterart genannte) mit dismiss schliessen (resolution decision reject,
  Audit review.dismiss je Fall).
- manuelle-pruefung: Dokumente der Faelle manual_check nach 06/02 ablegen und den Fall erledigen (bulk_execute mit
  Sammelfeldern 06/02 und Begruendung, wie in der Oberflaeche).

Ohne --echt Vorschau: Zaehler je Objekt, Unterart und Zielkategorie, keine Personendaten, nichts veraendert.
Mit --echt Ausfuehrung unter dem angegebenen Benutzer (--benutzer, E-Mail; aktiv und mit Berechtigung review.decide
wie in der Oberflaeche); je Lauf ein Audit review.bulk_command mit bulk_key, ueber den die Entscheidungen abfragbar
bleiben. Der bulk_key wird vor dem ersten Block ausgegeben, damit er auch nach einem Abbruch (SSH-Trennung,
Zeitueberschreitung) bekannt ist; die je Block geschriebenen Audits review.bulk_execute und die Audits
review.dismiss tragen ihn ebenfalls. --limit zaehlt ausfuehrbare Faelle, blockierte Zeilen verbrauchen es nicht."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.review import sammelaktion


class Command(BaseCommand):
    help = "Pruefrueckstand je Objekt und Unterart abarbeiten (Vorschau ohne --echt)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--aktion",
            required=True,
            choices=sammelaktion.AKTIONEN,
            help="kandidat-bestaetigen, verwerfen oder manuelle-pruefung",
        )
        parser.add_argument(
            "--unterart",
            default=None,
            help="Unterarten (Komma-Liste); Standard je Aktion: below_threshold, ai_sample, manual_check",
        )
        parser.add_argument("--objekt", default=None, help="nur dieses Objekt (Objektnummer)")
        parser.add_argument(
            "--min-konfidenz",
            type=float,
            default=0.7,
            help="Mindestkonfidenz des Kandidaten fuer kandidat-bestaetigen (Standard 0.7)",
        )
        parser.add_argument(
            "--limit", type=int, default=0, help="hoechstens so viele ausfuehrbare Faelle (0 = alle)"
        )
        parser.add_argument(
            "--benutzer", default=None, help="E-Mail des Benutzers, unter dem die Entscheidungen laufen"
        )
        parser.add_argument("--grund", default=None, help="Begruendung fuer verwerfen (Standardtext sonst)")
        parser.add_argument("--echt", action="store_true", help="ausfuehren statt Vorschau")

    def handle(self, *args, **options):
        aktion = options["aktion"]
        try:
            unterarten = sammelaktion.unterarten_aus(options["unterart"], aktion)
        except sammelaktion.SammelaktionError as exc:
            raise CommandError(str(exc)) from exc
        min_konf = float(options["min_konfidenz"])
        if not 0.0 <= min_konf <= 1.0:
            raise CommandError("--min-konfidenz muss zwischen 0 und 1 liegen")
        user = None
        if options["echt"]:
            if not options["benutzer"]:
                raise CommandError("--echt erfordert --benutzer (E-Mail des entscheidenden Benutzers)")
            from apps.accounts.models import User

            user = User.objects.filter(
                email=str(options["benutzer"]).strip().lower(), deleted_at__isnull=True
            ).first()
            if user is None:
                raise CommandError("Benutzer nicht gefunden")
            # Gleiche Huerde wie die Oberflaeche (permission_required("review.decide")): gesperrte oder nur
            # eingeladene Benutzer und Rollen ohne review.decide entscheiden nicht (26.09.2026).
            from apps.accounts.permissions import user_has_permission

            if not user.is_active:
                raise CommandError("Benutzer ist nicht aktiv")
            if not user_has_permission(user, "review.decide"):
                raise CommandError("Benutzer hat keine Berechtigung review.decide")
        auswahl = sammelaktion.Auswahl(
            aktion=aktion,
            unterarten=unterarten,
            objekt=(options["objekt"] or "").strip() or None,
            min_konfidenz=min_konf,
            limit=max(int(options["limit"] or 0), 0),
        )
        bulk_key = None
        if options["echt"]:
            bulk_key = sammelaktion.neuer_bulk_key(aktion)
            self.stdout.write(
                f"bulk_key {bulk_key} (Entscheidungen dieses Laufs tragen diesen Schluessel, auch nach Abbruch)"
            )
        try:
            ergebnis = sammelaktion.ausfuehren(
                auswahl, user=user, echt=options["echt"], grund=options["grund"], bulk_key=bulk_key
            )
        except sammelaktion.SammelaktionError as exc:
            raise CommandError(str(exc)) from exc
        self._ausgabe(ergebnis, auswahl, options["echt"])

    def _ausgabe(self, e: sammelaktion.Ergebnis, auswahl: sammelaktion.Auswahl, echt: bool) -> None:
        w = self.stdout.write
        w(
            f"Aktion {e.aktion}: Unterarten {', '.join(auswahl.unterarten)}"
            + (f", Objekt {auswahl.objekt}" if auswahl.objekt else ", alle Objekte")
            + (
                f", Mindestkonfidenz {auswahl.min_konfidenz:.2f}"
                if e.aktion == "kandidat-bestaetigen"
                else ""
            )
        )
        w(f"Ausgewaehlt: {e.ausgewaehlt} | ausfuehrbar: {e.ausfuehrbar}")
        for nr, n in sorted(e.je_objekt.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
            w(f"Objekt {nr}: {n}")
        for art, n in e.je_unterart.most_common():
            w(f"Unterart {art}: {n}")
        for ziel, n in sorted(e.je_ziel.items()):
            w(f"Ziel {ziel}: {n}")
        for grund, n in e.uebersprungen.most_common():
            w(f"Uebersprungen ({grund}): {n}")
        for grund, n in e.blockiert.most_common():
            w(f"Blockiert ({grund}): {n}")
        if e.begrenzung:
            w(f"Begrenzung {auswahl.limit} erreicht, {e.begrenzung} nicht bearbeitet")
        if not echt:
            w("Vorschau, nichts veraendert (mit --echt ausfuehren)")
            return
        for text, n in e.fehler.most_common(10):
            w(f"Fehler ({text}): {n}")
        w(f"Ausgefuehrt: {e.ok} erledigt, {e.fehlgeschlagen} fehlgeschlagen, bulk_key {e.bulk_key}")
        if e.aktion != "verwerfen" and e.ok:
            w("Ablage laeuft als Job file_to_drive (Queue io), Fortschritt in der Dokumentansicht (Jobs)")
