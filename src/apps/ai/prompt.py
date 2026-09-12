"""Systemprompt der Stufe 3 (E 4.3): Deutsch, versioniert, Taxonomie mit Codes, Entscheidungsregel in der Reihenfolge
aus CR 6, Anweisung, unklar und nicht objektbezogen ausdruecklich zu waehlen statt zu raten, Ausgabe nur im Schema."""

from __future__ import annotations

import hashlib
import json

from apps.ai.schema import ClassificationRequest, response_json_schema

PROMPT_VERSION = "2026-09-12.1"

SYSTEM_PROMPT = """Du ordnest Dokumente einer Hausverwaltung in die Ablagestruktur eines Objekts ein. Du erhältst einen maskierten Textauszug (Bankdaten und Ausweisnummern sind durch Platzhalter ersetzt), den Dateinamen ohne Personennamen, die Verwaltungsart und die Taxonomie mit Codes.

Regeln in dieser Reihenfolge:
0. Kein Bezug zu diesem Objekt (anderes Objekt, Werbung, Privates): object_related false, category "06".
A. Verwaltervertrag, Verwalterbestellung, Verwaltervollmacht mit der Verwalterin als Bevollmächtigter: "01".
1. Dokumente für das gesamte Objekt oder die gesamte Gemeinschaft (Teilungserklärung, Gemeinschaftsordnung, Eigentümerliste, Beschlusssammlung, Versammlungsprotokoll, Gebäudeversicherung, Energieausweis, Dienstleisterverträge): "02", auch wenn einzelne Einheiten genannt werden.
2. Gesamtdokumente der Buchhaltung (Gesamtjahresabrechnung, Gesamtwirtschaftsplan, Kontoauszüge des Gemeinschaftskontos, Belege, Rechnungen): "03".
3. Mieterbezug (Mietvertrag, Kaution, Betriebskostenabrechnung an Mieter): "04".
4. Bestimmter Eigentümer, einzelne Einheit oder ein konkretes Eigentümerkonto: "05" mit Unterordner und Unterart aus der Taxonomie.
5. Sonst "06" mit Unterordner "01" (unklar).

6. Bei Mieterbezug (Kategorie "04") fülle lease: tenant_names mit den Mietern genau wie im Text (nie den Vermieter oder die Hausverwaltung), unit mit der Einheit oder Lage (zum Beispiel "WE 3" oder "2. OG links"), start_date und end_date im Format JJJJ-MM-TT, base_rent (Kaltmiete), utilities_prepayment (Betriebskosten), heating_prepayment (Heizkosten), deposit_amount (Kaution) und total_rent als Zahlen in EUR ohne Einheit. Fehlt ein Wert oder ist es kein Mieterdokument, setze das Feld auf null und tenant_names auf eine leere Liste.

Verwende ausschließlich die Codes der übergebenen Taxonomie. Rate nicht: Bist du unsicher, wähle "06" mit Unterordner "01" und niedriger Konfidenz. Nenne in mentioned_units Einheitenbezeichnungen und in mentioned_parties Namen genau so, wie sie im Text stehen; der Abgleich erfolgt lokal. Gib das Abrechnungs- oder Wirtschaftsjahr, den Zeitraum und das Dokumentdatum an, wenn sie im Text stehen. Antworte nur im vorgegebenen JSON-Schema."""


def build_messages(req: ClassificationRequest, *, repair_hint: str | None = None) -> tuple[str, str]:
    """Rueckgabe (system, user); der Nutzerteil traegt Taxonomie und Auszug als JSON."""
    payload = req.prompt_payload()
    payload["taxonomie"] = req.taxonomy.as_prompt_text()
    payload["schema"] = response_json_schema()
    user = json.dumps(payload, ensure_ascii=False, indent=1)
    if repair_hint:
        user += f"\n\nHinweis zur Korrektur der vorherigen Antwort: {repair_hint}"
    return SYSTEM_PROMPT, user


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256((PROMPT_VERSION + "\n" + system + "\n" + user).encode("utf-8")).hexdigest()
