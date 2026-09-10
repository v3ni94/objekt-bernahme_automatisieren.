"""Synthetische Trainingsbeispiele fuer den Kaltstart (E 3.4 Nr. 2): je Unterart Vorlagen typischer Formulierungen mit
Platzhaltern fuer erfundene Namen, Einheiten, Betraege und Jahre, Varianten mit leichter OCR-Stoerung. Keine Stammdaten,
keine realen Personen (Namen Mustermann, Beispiel, Altmuster, Neumuster)."""

from __future__ import annotations

import random

from apps.config import store

NAMES = ["Mustermann", "Beispiel", "Altmuster", "Neumuster", "Musterfrau", "Probemann"]
FIRST = ["Erika", "Max", "Anna", "Peter", "Julia", "Thomas"]
UNITS = ["WE01", "WE 3", "WE 14", "GE 2", "ST 7", "TG 12"]
OCR_SWAPS = {"e": "c", "n": "m", "l": "1", "o": "0", "i": "l", "u": "ü"}

# label_a, label_b (nur 05), Vorlagen
TEMPLATES: list[tuple[str, str | None, list[str]]] = [
    (
        "01",
        None,
        [
            "Verwaltervertrag zwischen der Wohnungseigentümergemeinschaft {city} und der {company} über die Verwaltung des gemeinschaftlichen Eigentums ab {date}.",
            "Beschluss über die Bestellung des Verwalters: Die {company} wird zur Verwalterin bestellt.",
            "Vollmacht der Gemeinschaft an die {company} zur Vertretung gegenüber Dritten.",
        ],
    ),
    (
        "02",
        None,
        [
            "Teilungserklärung und Gemeinschaftsordnung für das Grundstück {city}, {street}, Aufteilung in Miteigentumsanteile.",
            "Protokoll der Eigentümerversammlung vom {date}. TOP 1 Jahresabrechnung {year}. TOP 2 Wirtschaftsplan {year2}.",
            "Beschlusssammlung der WEG {city}, fortlaufend geführt, Stand {date}.",
            "Versicherungsschein Wohngebäudeversicherung für das Gebäude {street}, {city}.",
            "Energieausweis für Wohngebäude {street}, {city}, ausgestellt am {date}.",
            "Hausmeistervertrag zwischen der WEG {city} und dem Hausmeisterdienst {company2}.",
            "Eigentümerliste der WEG {city} mit Einheit, Name und Anschrift, Stand {date}.",
        ],
    ),
    (
        "03",
        None,
        [
            "Gesamtabrechnung {year} der Wohnungseigentümergemeinschaft {city}, Abrechnungszeitraum 01.01.{year} bis 31.12.{year}. Gesamtkosten {amount} EUR.",
            "Gesamtwirtschaftsplan {year} mit Verteilerschlüssel für alle Einheiten der WEG {city}.",
            "Kontoauszug Gemeinschaftskonto WEG {city}, Buchungen vom {date}, Saldo {amount} EUR.",
            "Rechnung Nr. {number} des Hausmeisterdienstes {company2} für {city}, {street}, Betrag {amount} EUR.",
        ],
    ),
    (
        "04",
        None,
        [
            "Mietvertrag über die Wohnung {unit} in {city}, {street}. Mieter {first} {name}, Vermieter {first2} {name2}. Kaltmiete {amount} EUR.",
            "Betriebskostenabrechnung {year} für die Wohnung {unit}, Mieter {first} {name}, Nachzahlung {amount} EUR.",
            "Kautionsabrechnung für die Mieterin {first} {name}, Wohnung {unit}.",
        ],
    ),
    (
        "05",
        "05/05/einzelabrechnung",
        [
            "Einzelabrechnung {year} für Einheit {unit}, Eigentümer {first} {name}. Abrechnungsergebnis: Nachzahlung {amount} EUR.",
            "Einzelabrechnung {year} Einheit {unit}, Eigentümerin {first} {name}, Miteigentumsanteil {mea}/10.000, Guthaben {amount} EUR.",
        ],
    ),
    (
        "05",
        "05/06/einzelwirtschaftsplan",
        [
            "Einzelwirtschaftsplan {year} für Einheit {unit}, Eigentümer {first} {name}. Monatliches Hausgeld ab 01.01.{year}: {amount} EUR."
        ],
    ),
    (
        "05",
        "05/03/sepa_mandat",
        [
            "SEPA-Lastschriftmandat. Zahlungspflichtiger {first} {name}, Einheit {unit}. Mandatsreferenz WEG-{number}. IBAN [IBAN]."
        ],
    ),
    (
        "05",
        "05/09/zahlungserinnerung",
        [
            "Zahlungserinnerung Hausgeld {month}/{year} für Einheit {unit}, Eigentümer {first} {name}, offener Betrag {amount} EUR."
        ],
    ),
    (
        "05",
        "05/09/mahnung",
        [
            "Letzte Mahnung: Für die Einheit {unit} ist das Hausgeld {month}/{year} in Höhe von {amount} EUR trotz Zahlungserinnerung nicht eingegangen. Mahngebühr {fee} EUR."
        ],
    ),
    (
        "05",
        "05/02/grundbuchauszug",
        [
            "Grundbuchauszug Wohnungsgrundbuch Blatt {number}, Sondereigentum Nr. {n}, Eigentümer {first} {name}, Stand {date}."
        ],
    ),
    (
        "05",
        "05/02/kaufvertrag",
        [
            "Notarieller Kaufvertrag vom {date}. Verkäufer {first} {name}, Käufer {first2} {name2}, Einheit {unit}. Übergang von Nutzen und Lasten zum {date2}."
        ],
    ),
    (
        "05",
        "05/01/bankverbindungsmitteilung",
        [
            "Mitteilung neue Bankverbindung ab {date}: Kontoinhaber {first} {name}, Einheit {unit}, IBAN [IBAN]."
        ],
    ),
    (
        "05",
        "05/08/einwendung_abrechnung",
        [
            "Einwendungen gegen die Einzelabrechnung {year}: Sehr geehrte Damen und Herren, für die Einheit {unit} widerspreche ich der Abrechnung. {first} {name}."
        ],
    ),
    (
        "05",
        "05/10/versammlungsvollmacht",
        [
            "Vollmacht: Hiermit bevollmächtige ich, {first} {name}, Einheit {unit}, Herrn {first2} {name2} zur Vertretung in der Eigentümerversammlung am {date}."
        ],
    ),
    (
        "05",
        "05/04/zahlungsvereinbarung",
        [
            "Zahlungsvereinbarung: {first} {name}, Einheit {unit}, zahlt die Sonderumlage in Höhe von {amount} EUR in sechs Raten ab {date}."
        ],
    ),
    (
        "nicht_objektbezogen",
        None,
        [
            "Werbeprospekt: Angebote der Woche im Baumarkt, Gartenmöbel und Grills reduziert.",
            "Rechnung Dachdecker für {street2}, {city2}, Reparatur der Dachrinne, Betrag {amount} EUR.",
            "Newsletter: Neuigkeiten aus der Region, Veranstaltungen im {month}.",
        ],
    ),
]


def _fields(rng: random.Random) -> dict:
    year = rng.randint(2020, 2026)
    return {
        "city": rng.choice(["Musterstadt", "Beispielhausen", "Probedorf"]),
        "city2": rng.choice(["Anderstadt", "Fernhausen"]),
        "street": rng.choice(["Musterstraße 49", "Beispielweg 7", "Am Musterpark 12"]),
        "street2": rng.choice(["Fremdweg 3", "Andere Allee 10"]),
        "company": rng.choice(
            store.get("classification.own_company_names", []) or ["Hausverwaltung Muster GmbH"]
        ),
        "company2": rng.choice(["Hausmeisterdienst Sauber GmbH", "Reinigung Blank e.K."]),
        "date": f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{year}",
        "date2": f"01.{rng.randint(1, 12):02d}.{year}",
        "year": year,
        "year2": year + 1,
        "month": f"{rng.randint(1, 12):02d}",
        "unit": rng.choice(UNITS),
        "n": rng.randint(1, 40),
        "first": rng.choice(FIRST),
        "first2": rng.choice(FIRST),
        "name": rng.choice(NAMES),
        "name2": rng.choice(NAMES),
        "amount": f"{rng.randint(50, 5000)},{rng.randint(0, 99):02d}",
        "fee": f"{rng.randint(2, 15)},00",
        "mea": rng.randint(100, 900),
        "number": rng.randint(1000, 9999),
    }


def ocr_noise(text: str, rng: random.Random, rate: float) -> str:
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch in OCR_SWAPS and rng.random() < rate:
            chars[i] = OCR_SWAPS[ch]
    return "".join(chars)


def synthetic_samples(*, variants_per_template: int = 6, seed: int = 20260910):
    """Erzeugt Sample-Objekte (apps.classification.training.Sample) mit Gewicht label_weights.synthetic."""
    from apps.classification.training import Sample

    rng = random.Random(seed)
    weight = float((store.get("classification.label_weights", {}) or {}).get("synthetic", 0.3))
    out = []
    for label_a, label_b, templates in TEMPLATES:
        for template in templates:
            for _ in range(variants_per_template):
                text = template.format(**_fields(rng))
                text = ocr_noise(text, rng, rng.uniform(0.01, 0.03))
                out.append(Sample(f"__synthetic__ {text}", label_a, label_b, weight, "seed"))
    return out
