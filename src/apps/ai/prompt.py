"""Systemprompt der Stufe 3 (E 4.3): Deutsch, versioniert, Taxonomie mit Codes, Entscheidungsregel in der Reihenfolge
aus CR 6, Anweisung, unklar und nicht objektbezogen ausdruecklich zu waehlen statt zu raten, Ausgabe nur im Schema.

Reihenfolge im Nutzerteil (seit 2026-09-20.2): erst die fuer alle Dokumente identischen Teile (Taxonomie, Schema), dann
die je Objekt gleichen (Verwaltungsart, Einheitenmuster, Objektangaben, Hinweise), zuletzt Dateiname und Textauszug. Anbieter mit
automatischem Prompt-Cache verguenstigen den unveraenderten Praefix; die Reihenfolge aendert nichts am Inhalt."""

from __future__ import annotations

import hashlib
import json

from apps.ai.schema import ClassificationRequest, response_json_schema

PROMPT_VERSION = "2026-09-24.2"

SYSTEM_PROMPT = """Du ordnest Dokumente einer Hausverwaltung in die Ablagestruktur eines Objekts ein. Du erhältst einen maskierten Textauszug (Bankdaten und Ausweisnummern sind durch Platzhalter ersetzt), den Dateinamen ohne Personennamen, die Verwaltungsart, die Objektangaben (Objektnummer und Anschriften) und die Taxonomie mit Codes.

Regeln in dieser Reihenfolge:
0. object_related ist true, solange nichts Eindeutiges dagegen spricht. Setze object_related false nur, wenn der Text eindeutig ein anderes Objekt betrifft (eine andere Straße oder ein anderer Ort als die Anschriften in den Objektangaben, ausdrücklich als Objekt oder Liegenschaft genannt) oder wenn es Werbung oder Privates ohne Bezug zur Verwaltung ist. Kein Grund für false sind der Briefkopf der Hausverwaltung, Anschriften von Eigentümern, Mietern, Handwerkern oder Behörden, eine Einheitenbezeichnung ohne Straße oder dass die Anschrift im Auszug nicht vorkommt. Steht in den Objektangaben anschrift_bekannt false, setze object_related nur dann false, wenn der Text ausdrücklich ein anderes Objekt mit Objektnummer nennt. Bei object_related false ist category immer "06" mit Unterordner "04". Unklare Dokumente mit Objektbezug bleiben object_related true und bekommen "06" mit Unterordner "01".
A. Verwaltervertrag, Verwalterbestellung, Verwaltervollmacht mit der Verwalterin als Bevollmächtigter: "01".
1. Dokumente für das gesamte Objekt oder die gesamte Gemeinschaft (Teilungserklärung, Gemeinschaftsordnung, Eigentümerliste, Beschlusssammlung, Versammlungsprotokoll, Gebäudeversicherung, Energieausweis, Dienstleisterverträge): "02", auch wenn einzelne Einheiten genannt werden.
2. Gesamtdokumente der Buchhaltung: "03". Dazu gehören die Gesamtjahresabrechnung oder Hausgeldabrechnung für alle Einheiten (gesamtjahresabrechnung), der Gesamtwirtschaftsplan, Kontoauszüge des Gemeinschaftskontos sowie Belege und Rechnungen für Gemeinschaftseigentum, Allgemeinflächen oder das Objekt insgesamt (beleg). Bei Verwaltungsart weg gehen alle Rechnungen nach "03".
3. Mieterbezug (Mietvertrag, Kaution, Betriebskostenabrechnung oder Nebenkostenabrechnung an Mieter, Kündigung, Übergabeprotokoll): "04".
4. Bestimmter Eigentümer, einzelne Einheit oder ein konkretes Eigentümerkonto: "05" mit Unterordner und Unterart aus der Taxonomie. Dazu gehören die Einzelabrechnung einer Einheit (05/05 einzelabrechnung), Hausgeld und Mahnwesen sowie bei Verwaltungsart rental oder weg_with_se Rechnungen und Belege für Arbeiten in einer bestimmten Einheit oder ihrem Sondereigentum (05/11 beleg_einheit), auch wenn ein Mieter genannt wird.
5. Nenne in jedem Fall den am besten passenden Platz der Struktur (category, subfolder, document_type) und drücke Unsicherheit über die Konfidenz aus, nicht über "06". "06" nur in drei Fällen: kein Objektbezug (object_related false, Unterordner "04"), kein Verwaltungsdokument wie Werbung oder Privates (Unterordner "04") oder ein Inhalt ohne jeden Anhaltspunkt wie eine leere oder unlesbare Seite (Unterordner "01").

6. Bei Mieterbezug (Kategorie "04") fülle lease: tenant_names mit den Mietern genau wie im Text (nie den Vermieter oder die Hausverwaltung), unit mit der Einheit oder Lage (zum Beispiel "WE 3" oder "2. OG links"), start_date und end_date im Format JJJJ-MM-TT, base_rent (Kaltmiete), utilities_prepayment (Betriebskosten), heating_prepayment (Heizkosten), deposit_amount (Kaution) und total_rent als Zahlen in EUR ohne Einheit. Fehlt ein Wert oder ist es kein Mieterdokument, setze das Feld auf null und tenant_names auf eine leere Liste.

Konfidenz: deine Einschätzung der Wahrscheinlichkeit (0 bis 1), dass Kategorie, Unterordner und Unterart zusammen richtig sind. 0,85 oder höher nur, wenn sie klar aus dem Text folgen; passen mehrere Plätze ähnlich gut oder fehlt der Beleg im Text, bleibe unter 0,85 und nenne trotzdem den besten Platz. Verwende ausschließlich die Codes der übergebenen Taxonomie. Trage in category, subfolder und document_type nur den Code ein (zum Beispiel "04"), niemals den Namen oder die ganze Zeile. Nenne in mentioned_units Einheitenbezeichnungen und in mentioned_parties Namen genau so, wie sie im Text stehen; der Abgleich erfolgt lokal. Gib das Abrechnungs- oder Wirtschaftsjahr, den Zeitraum und das Dokumentdatum an, wenn sie im Text stehen. Antworte nur im vorgegebenen JSON-Schema."""


def build_messages(req: ClassificationRequest, *, repair_hint: str | None = None) -> tuple[str, str]:
    """Rueckgabe (system, user); der Nutzerteil traegt Taxonomie und Auszug als JSON."""
    variable = req.prompt_payload()
    payload = {
        "taxonomie": req.taxonomy.as_prompt_text(),
        "schema": response_json_schema(),
        "verwaltungsart": variable.pop("verwaltungsart"),
        "einheitenmuster": variable.pop("einheitenmuster"),
        "objekt": variable.pop("objekt"),
        "hinweise": variable.pop("hinweise"),
    }
    payload.update(variable)  # dateiname, textauszug
    user = json.dumps(payload, ensure_ascii=False, indent=1)
    if repair_hint:
        user += f"\n\nHinweis zur Korrektur der vorherigen Antwort: {repair_hint}"
    return SYSTEM_PROMPT, user


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256((PROMPT_VERSION + "\n" + system + "\n" + user).encode("utf-8")).hexdigest()
