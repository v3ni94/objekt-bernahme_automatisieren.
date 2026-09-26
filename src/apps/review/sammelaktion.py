"""Sammelaktion je Objekt und Unterart fuer den Pruefrueckstand (26.09.2026, docs/plan/pruefrueckstand.md).

Die Oberflaeche des Pruefcenters bietet die Massenbearbeitung nur fuer markierte Faelle oder eine Gruppe (batch_key),
Verwerfen nur einzeln. Fuer den Rueckstand (rund 32.300 offene Faelle, Stand 23.09. bis 26.09.2026) braucht es eine
Auswahl nach Objekt und Unterart mit Vorschau und echter Ausfuehrung. Dieses Modul waehlt die Faelle aus und laesst
sie ueber die vorhandenen Dienste laufen; es kopiert keine Entscheidungslogik:

- kandidat-bestaetigen: bulk_rows und bulk_execute (Ziel aus proposal_target, also context.intended, dem besten
  Kandidaten aus Regel oder KI), nur wenn die Konfidenz des Kandidaten die Mindestschwelle erreicht.
- manuelle-pruefung: bulk_rows und bulk_execute mit Sammelfeldern 06/02 und Begruendung, wie in der Oberflaeche.
- verwerfen: dismiss je Fall mit gemeinsamem bulk_key.

Vorschau und Ausfuehrung liefern denselben Ergebnisdict (Zaehler je Objekt, Unterart und Zielkategorie, keine
Personendaten). Drive wird nie aufgerufen; die Ablage folgt als Job file_to_drive wie bei jeder Entscheidung."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field

from django.db.models import Q

from apps.audit.services import record
from apps.review import services
from apps.review.models import CaseStatus, CaseType, ReviewCase
from objektakte.masking import mask_text

AKTIONEN = ("kandidat-bestaetigen", "verwerfen", "manuelle-pruefung")
STANDARD_UNTERART = {
    "kandidat-bestaetigen": ["below_threshold"],
    "verwerfen": ["ai_sample"],
    "manuelle-pruefung": ["manual_check"],
}
GRUND_VERWERFEN = "Sammelaktion verwerfen (faelle_sammelaktion)"
GRUND_MANUELL = "Sammelaktion manuelle Prüfung: Ablage in 06/02 (faelle_sammelaktion)"
ZIELKATEGORIEN_KANDIDAT = ("01", "02", "03", "04", "05")


class SammelaktionError(ValueError):
    pass


@dataclass
class Auswahl:
    aktion: str
    unterarten: list[str]
    objekt: str | None = None
    min_konfidenz: float = 0.0
    limit: int = 0


@dataclass
class Ergebnis:
    aktion: str
    echt: bool
    bulk_key: str | None
    ausgewaehlt: int = 0
    ausfuehrbar: int = 0
    ok: int = 0
    fehlgeschlagen: int = 0
    je_objekt: Counter = field(default_factory=Counter)
    je_unterart: Counter = field(default_factory=Counter)
    je_ziel: Counter = field(default_factory=Counter)
    uebersprungen: Counter = field(default_factory=Counter)
    blockiert: Counter = field(default_factory=Counter)
    fehler: Counter = field(default_factory=Counter)
    begrenzung: int = 0

    def as_dict(self) -> dict:
        return {
            "aktion": self.aktion,
            "echt": self.echt,
            "bulk_key": self.bulk_key,
            "ausgewaehlt": self.ausgewaehlt,
            "ausfuehrbar": self.ausfuehrbar,
            "ok": self.ok,
            "fehlgeschlagen": self.fehlgeschlagen,
            "je_objekt": dict(self.je_objekt),
            "je_unterart": dict(self.je_unterart),
            "je_ziel": dict(self.je_ziel),
            "uebersprungen": dict(self.uebersprungen),
            "blockiert": dict(self.blockiert),
            "fehler": dict(self.fehler),
            "begrenzung": self.begrenzung,
        }


def kandidat_konfidenz(case: ReviewCase) -> float | None:
    """Konfidenz des Kandidaten, der context.intended gebildet hat (decide.py: cands[0]); Rueckfall auf die
    Gesamtkonfidenz des Falls. None, wenn keine Zahl vorliegt."""
    ctx = case.context or {}
    intended = ctx.get("intended") or {}
    for cand in case.candidates or []:
        if isinstance(cand, dict) and cand.get("category") == intended.get("category"):
            if cand.get("confidence") is not None:
                return _zahl(cand.get("confidence"))
            break
    return _zahl(ctx.get("confidence"))


def _zahl(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def case_queryset(auswahl: Auswahl):
    """Offene Faelle mit Dokument nach Unterart und Objekt, aelteste zuerst (stabile Reihenfolge fuer --limit)."""
    qs = ReviewCase.objects.filter(
        status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
        case_subtype__in=auswahl.unterarten,
        document__isnull=False,
        document__deleted_at__isnull=True,
    )
    if auswahl.objekt:
        nummer = str(auswahl.objekt).strip()
        if not nummer.isdigit():
            raise SammelaktionError("Objektnummer muss aus Ziffern bestehen")
        qs = qs.filter(
            Q(object__object_number_numeric=int(nummer))
            | Q(object__isnull=True, document__object__object_number_numeric=int(nummer))
        )
    return qs.select_related("document", "object", "document__object").order_by("id")


def _objektnummer(case: ReviewCase) -> str:
    obj = case.object or case.document.object
    return obj.object_number if obj else "(ohne)"


def _vorpruefung(auswahl: Auswahl, case: ReviewCase) -> str | None:
    """Grund, warum ein Fall nicht in die Aktion geht (Zaehler in uebersprungen), sonst None."""
    if auswahl.aktion == "kandidat-bestaetigen":
        intended = (case.context or {}).get("intended") or {}
        if intended.get("category") not in ZIELKATEGORIEN_KANDIDAT:
            return "ohne Kandidat 01 bis 05"
        konf = kandidat_konfidenz(case)
        if konf is None or konf < auswahl.min_konfidenz:
            return "unter Mindestkonfidenz"
    if auswahl.aktion == "verwerfen" and case.case_type in services.OBJECT_CASE_TYPES:
        return "Objektfall (nur einzeln)"
    return None


def auswaehlen(auswahl: Auswahl, ergebnis: Ergebnis) -> list[ReviewCase]:
    """Faelle nach Vorpruefung, ohne Begrenzung; zaehlt uebersprungene Faelle im Ergebnis. Die Begrenzung (--limit)
    greift erst in _entscheiden und _verwerfen und zaehlt nur ausfuehrbare Faelle (26.09.2026): blockierte Zeilen
    (rote Ampel, etwa „Unterart wählen") verbrauchen das Limit nicht, sonst kaeme ein Pilot mit limit=N nie voran,
    wenn die aeltesten Faelle blockiert sind."""
    faelle: list[ReviewCase] = []
    for case in case_queryset(auswahl).iterator(chunk_size=500):
        grund = _vorpruefung(auswahl, case)
        if grund:
            ergebnis.uebersprungen[grund] += 1
            continue
        faelle.append(case)
    return faelle


def neuer_bulk_key(aktion: str) -> str:
    """Gemeinsamer Schluessel eines echten Laufs; das Kommando erzeugt ihn vor dem ersten Block und gibt ihn aus,
    damit er auch nach einem Abbruch bekannt ist (26.09.2026)."""
    return f"cmd:{aktion}:{uuid.uuid4().hex[:12]}"


def _overrides(aktion: str) -> dict | None:
    if aktion == "manuelle-pruefung":
        return {"category": "06", "subfolder": "02", "reason": GRUND_MANUELL}
    return None


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _fehlertext(text) -> str:
    """Fehlertext als Zaehlerschluessel: maskiert wie ein Protokolleintrag und gekuerzt, weil er auf stdout und in
    after_state des Audits landet (26.09.2026)."""
    return mask_text(str(text or ""), mode="log").text[:120]


def ausfuehren(
    auswahl: Auswahl, *, user=None, echt: bool = False, grund: str | None = None, bulk_key: str | None = None
) -> Ergebnis:
    """Vorschau (echt=False, keine Schreibwirkung) oder Ausfuehrung ueber die Dienste des Pruefcenters."""
    if auswahl.aktion not in AKTIONEN:
        raise SammelaktionError(f"Aktion unbekannt: {auswahl.aktion}")
    if echt and user is None:
        raise SammelaktionError("Ausfuehrung braucht einen Benutzer (Entscheidungen tragen decided_by)")
    bulk_key = (bulk_key or neuer_bulk_key(auswahl.aktion)) if echt else None
    ergebnis = Ergebnis(auswahl.aktion, echt, bulk_key)
    faelle = auswaehlen(auswahl, ergebnis)
    if auswahl.aktion == "verwerfen":
        _verwerfen(
            faelle,
            ergebnis,
            auswahl.limit,
            user=user,
            echt=echt,
            grund=grund or GRUND_VERWERFEN,
            bulk_key=bulk_key,
        )
    else:
        _entscheiden(
            faelle, ergebnis, auswahl.limit, user=user, echt=echt, bulk_key=bulk_key, aktion=auswahl.aktion
        )
    if echt:
        record(
            "review.bulk_command",
            entity_type="review_bulk",
            entity_id=None,
            actor=user,
            reason=f"faelle_sammelaktion --aktion {auswahl.aktion}",
            after={
                "bulk_key": bulk_key,
                "unterarten": auswahl.unterarten,
                "objekt": auswahl.objekt,
                "min_konfidenz": auswahl.min_konfidenz,
                "limit": auswahl.limit,
                **{k: v for k, v in ergebnis.as_dict().items() if k not in ("aktion", "echt", "bulk_key")},
            },
        )
    return ergebnis


def _aufnehmen(case: ReviewCase, ergebnis: Ergebnis) -> None:
    ergebnis.ausgewaehlt += 1
    ergebnis.je_unterart[case.case_subtype or "(ohne)"] += 1


def _entscheiden(faelle, ergebnis: Ergebnis, limit: int, *, user, echt, bulk_key, aktion) -> None:
    """kandidat-bestaetigen und manuelle-pruefung: Vorschau ueber bulk_rows, Ausfuehrung ueber bulk_execute
    (je Zeile apply_decision mit is_bulk und gemeinsamem bulk_key), in Bloecken von review.bulk_max_cases.
    Das Limit zaehlt ausfuehrbare Zeilen; blockierte Zeilen bleiben offen und zaehlen in blockiert. Sobald das
    Limit erreicht ist, zaehlen die uebrigen Faelle in begrenzung, ohne dass bulk_rows sie noch bewertet."""
    overrides = _overrides(aktion)
    by_id = {c.pk: c for c in faelle}
    ids = [c.pk for c in faelle]
    offen = 0
    for block in _chunks(ids, services.bulk_max()):
        if limit and ergebnis.ausfuehrbar >= limit:
            offen += len(block)
            continue
        rows = services.bulk_rows(block, overrides, None, user)
        ausschluss: list[int] = []
        for row in rows:
            case = by_id[row.case_id]
            if limit and ergebnis.ausfuehrbar >= limit:
                ausschluss.append(row.case_id)
                offen += 1
                continue
            _aufnehmen(case, ergebnis)
            if row.errors:
                ausschluss.append(row.case_id)
                for err in row.errors:
                    ergebnis.blockiert[err] += 1
                continue
            ergebnis.ausfuehrbar += 1
            ergebnis.je_objekt[_objektnummer(case)] += 1
            ergebnis.je_ziel[row.target.category or "(ohne)"] += 1
        if not echt or len(ausschluss) == len(rows):
            continue
        result = services.bulk_execute(
            block, user, overrides=overrides, exclude=ausschluss, bulk_key=bulk_key
        )
        ergebnis.ok += len(result["ok"])
        ergebnis.fehlgeschlagen += len(result["failed"])
        for f in result["failed"]:
            ergebnis.fehler[_fehlertext(f.get("error"))] += 1
    ergebnis.begrenzung = offen


def _verwerfen(faelle, ergebnis: Ergebnis, limit: int, *, user, echt, grund, bulk_key) -> None:
    for case in faelle:
        if limit and ergebnis.ausfuehrbar >= limit:
            ergebnis.begrenzung += 1
            continue
        _aufnehmen(case, ergebnis)
        ergebnis.ausfuehrbar += 1
        ergebnis.je_objekt[_objektnummer(case)] += 1
        ergebnis.je_ziel["verworfen"] += 1
        if not echt:
            continue
        try:
            services.dismiss(case, user, reason=grund, is_bulk=True, bulk_key=bulk_key)
            ergebnis.ok += 1
        except Exception as exc:  # eine Zeile scheitert, die uebrigen laufen weiter (wie bulk_execute)
            ergebnis.fehlgeschlagen += 1
            ergebnis.fehler[_fehlertext(exc)] += 1


def unterarten_aus(text: str | None, aktion: str) -> list[str]:
    werte = [t.strip() for t in (text or "").split(",") if t.strip()]
    if not werte:
        return list(STANDARD_UNTERART[aktion])
    if any(w in CaseType.values for w in werte):
        raise SammelaktionError("--unterart erwartet Unterarten (case_subtype), keine Fallarten")
    return werte
