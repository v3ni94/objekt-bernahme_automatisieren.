"""HVM-Briefbogen (Hausverwaltung Mueller GmbH) als ReportLab-Bausteine, portiert aus dem CI-Skill hvm-ci
(scripts/hvm_briefkopf.py, Befund 5). Seitengroesse ist Parameter: A4 hoch fuer Briefe, A4 quer fuer Listen.

Logo liegt unter assets/Logo_HVM.jpg. Das Unterschriftsbild kommt aus dem Betrieb (Docker Secret oder Volume,
OBJEKTAKTE["HVM_SIGNATURE_PATH"]) und wird nur in freigegebenen Fassungen eingebettet (H 4.5). Kontaktdaten, die die CI
nicht nennt (Telefon, E-Mail, Bank, Steuernummern), erscheinen nirgends.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit

ORANGE = HexColor("#E6A83C")
ANTHRAZIT = HexColor("#87888A")
MITTEL = HexColor("#9C9D9F")
HELL = HexColor("#D7D8DA")
UMRISS = HexColor("#ECECEC")
SCHWARZ = HexColor("#1A1A1A")

ASSETS = Path(__file__).resolve().parent / "assets"
LOGO = ASSETS / "Logo_HVM.jpg"
LOGO_RATIO = 1143 / 1320
SIGNATURE_RATIO = 64 / 247

FIRMA = "Hausverwaltung Müller GmbH"
ANSCHRIFT = "Rheinpromenade 13, 40789 Monheim am Rhein"
ABSENDERZEILE = f"{FIRMA}, {ANSCHRIFT}"
FUSS_1 = "Hausverwaltung Müller GmbH | Rheinpromenade 13 | 40789 Monheim am Rhein"
FUSS_2 = "Amtsgericht Düsseldorf, HRB 104762 | Geschäftsführer: Timo Müller | www.muellerhv.de"
UNTERZEICHNER = ("Timo Müller", "Geschäftsführender Gesellschafter", FIRMA)
GRUSSFORMEL = "Mit freundlichen Grüßen"
SCHRIFT = "Helvetica"
SCHRIFT_FETT = "Helvetica-Bold"

RAND_L = 25 * mm
RAND_R = 20 * mm
RAND_U = 28 * mm  # nutzbarer Textbereich endet hier, Fusszeile beginnt bei 26 mm


@dataclass(frozen=True)
class Seite:
    breite: float
    hoehe: float

    @classmethod
    def hoch(cls) -> Seite:
        return cls(*A4)

    @classmethod
    def quer(cls) -> Seite:
        return cls(*landscape(A4))

    @property
    def groesse(self) -> tuple[float, float]:
        return (self.breite, self.hoehe)

    @property
    def textbreite(self) -> float:
        return self.breite - RAND_L - RAND_R


def kennlinie(c, seite: Seite, y_oben: float, hoehe: float) -> None:
    """Vier Farbsegmente mit diagonalen Uebergaengen (H dunkel, V mittel, Orangekeil, M hell)."""
    schraege = hoehe * 0.9
    segmente = [(0.0, 0.40, ANTHRAZIT), (0.40, 0.60, MITTEL), (0.60, 0.675, ORANGE), (0.675, 1.0, HELL)]
    for a, b, farbe in segmente:
        x1, x2 = a * seite.breite, b * seite.breite
        c.setFillColor(farbe)
        p = c.beginPath()
        p.moveTo(x1, y_oben)
        p.lineTo(x2, y_oben)
        p.lineTo(x2 - schraege if b < 1.0 else x2, y_oben - hoehe)
        p.lineTo(x1 - schraege if a > 0.0 else x1, y_oben - hoehe)
        p.close()
        c.drawPath(p, fill=1, stroke=0)


def wasserzeichen_haus(c, seite: Seite) -> None:
    """Dezenter Hausumriss unten rechts, laeuft bewusst ueber den Seitenrand hinaus; vor dem Text zeichnen."""
    c.saveState()
    c.setStrokeColor(UMRISS)
    c.setLineWidth(9)
    c.setLineJoin(1)
    c.setLineCap(1)
    x0 = seite.breite - (A4[0] - 135 * mm)  # 135 mm auf A4 hoch, an der rechten Kante ausgerichtet
    y0 = -25 * mm
    breite, wand, dach = 115 * mm, 62 * mm, 42 * mm
    p = c.beginPath()
    p.moveTo(x0, y0)
    p.lineTo(x0, y0 + wand)
    p.lineTo(x0 + breite / 2, y0 + wand + dach)
    p.lineTo(x0 + breite, y0 + wand)
    p.lineTo(x0 + breite, y0)
    c.drawPath(p, fill=0, stroke=1)
    c.restoreState()


def falzmarken(c, seite: Seite) -> None:
    """DIN 5008 Form B: Falzmarken 105 mm und 210 mm, Lochmarke 148,5 mm von der oberen Blattkante; nur Seite 1."""
    c.setStrokeColor(MITTEL)
    c.setLineWidth(0.4)
    for abstand, laenge in [(105, 4.5), (210, 4.5), (148.5, 7)]:
        y = seite.hoehe - abstand * mm
        c.line(2 * mm, y, (2 + laenge) * mm, y)


def logo(c, seite: Seite, breite: float = 40 * mm) -> None:
    bild = ImageReader(str(LOGO))
    lh = breite * LOGO_RATIO
    c.drawImage(bild, seite.breite - RAND_R - breite, seite.hoehe - 10 * mm - lh, breite, lh, mask="auto")


def absenderzeile(c, seite: Seite) -> None:
    c.setFont(SCHRIFT, 7.5)
    c.setFillColor(ANTHRAZIT)
    c.drawString(RAND_L, seite.hoehe - 45 * mm, ABSENDERZEILE)
    c.setStrokeColor(HELL)
    c.setLineWidth(0.4)
    c.line(RAND_L, seite.hoehe - 46.5 * mm, 105 * mm, seite.hoehe - 46.5 * mm)


def briefkopf_seite1(c, seite: Seite | None = None) -> None:
    """Alle festen Gestaltungselemente der ersten Briefseite."""
    seite = seite or Seite.hoch()
    kennlinie(c, seite, seite.hoehe, 3 * mm)
    wasserzeichen_haus(c, seite)
    falzmarken(c, seite)
    logo(c, seite)
    absenderzeile(c, seite)


def anschriftfeld(c, seite: Seite, zeilen: list[str], y_start: float | None = None) -> float:
    y = y_start or (seite.hoehe - 53 * mm)
    c.setFont(SCHRIFT, 11)
    c.setFillColor(SCHWARZ)
    for zeile in zeilen[:6]:
        c.drawString(RAND_L, y, zeile)
        y -= 5 * mm
    return y


def infoblock(
    c, seite: Seite, paare: list[tuple[str, str]], x: float = 125 * mm, y_start: float | None = None
) -> float:
    """Rechter Infoblock nach DIN 5008; nur Felder mit vorhandenen Daten uebergeben, nichts erfinden."""
    y = y_start or (seite.hoehe - 53 * mm)
    for bezeichnung, wert in paare:
        c.setFillColor(ANTHRAZIT)
        c.setFont(SCHRIFT, 8)
        c.drawString(x, y, bezeichnung)
        c.setFillColor(SCHWARZ)
        c.setFont(SCHRIFT, 9.5)
        c.drawString(x, y - 3.8 * mm, wert)
        y -= 10 * mm
    return y


def betreff(c, seite: Seite, text: str, y: float) -> None:
    """Betreff fett mit orangefarbenem Marker unter dem Zeilenanfang, ohne das Wort Betreff."""
    c.setFillColor(ORANGE)
    c.rect(RAND_L, y - 1.2 * mm, 7 * mm, 1.1 * mm, fill=1, stroke=0)
    c.setFont(SCHRIFT_FETT, 11.5)
    c.setFillColor(SCHWARZ)
    c.drawString(RAND_L, y + 1.5 * mm, text)


def unterschriftsblock(c, seite: Seite, x: float, y: float, signatur: Path | None = None) -> float:
    """Grussformel setzt der Aufrufer. Unterschriftsbild nur, wenn ein Pfad uebergeben wird und die Datei existiert
    (freigegebene Fassung); sonst bleibt der Platz frei. Rueckgabe: y unterhalb des Blocks."""
    if signatur is not None and Path(signatur).exists():
        bild = ImageReader(str(signatur))
        sw = 40 * mm
        sh = sw * SIGNATURE_RATIO
        c.drawImage(bild, x, y - 8 * mm - sh, sw, sh, mask="auto")
        y2 = y - 12 * mm - sh
    else:
        y2 = y - 22 * mm
    name, funktion, firma = UNTERZEICHNER
    c.setFont(SCHRIFT, 10.5)
    c.setFillColor(SCHWARZ)
    c.drawString(x, y2, name)
    c.setFont(SCHRIFT, 9.5)
    c.setFillColor(ANTHRAZIT)
    c.drawString(x, y2 - 4.5 * mm, funktion)
    c.drawString(x, y2 - 9 * mm, firma)
    return y2 - 9 * mm


def fusszeile(c, seite: Seite, zusatz: str | None = None) -> None:
    """Schmale Kennlinie plus Pflichtangaben; zusatz nur mit vom Auftraggeber bestaetigten Daten."""
    kennlinie(c, seite, 26 * mm, 1.2 * mm)
    c.setFont(SCHRIFT, 7.5)
    c.setFillColor(ANTHRAZIT)
    c.drawCentredString(seite.breite / 2, 20 * mm, FUSS_1)
    c.drawCentredString(seite.breite / 2, 16 * mm, FUSS_2)
    if zusatz:
        c.drawCentredString(seite.breite / 2, 12 * mm, zusatz)


def folgeseite(c, seite: Seite, seitenzahl: int, gesamt: int | None = None) -> None:
    """Kopf der Folgeseiten: schmale Kennlinie und Seitenzahl, keine Falzmarken, kein Anschriftfeld."""
    kennlinie(c, seite, seite.hoehe, 1.2 * mm)
    c.setFont(SCHRIFT, 8)
    c.setFillColor(ANTHRAZIT)
    text = f"Seite {seitenzahl}" if gesamt is None else f"Seite {seitenzahl} von {gesamt}"
    c.drawRightString(seite.breite - RAND_R, 12 * mm, text)


def entwurf_wasserzeichen(c, seite: Seite, text: str = "ENTWURF") -> None:
    """Halbtransparenter Hinweis im Hintergrund fuer nicht freigegebene Fassungen (H 4.2 Nr. 11)."""
    c.saveState()
    c.setFillColor(HELL)
    c.setFillAlpha(0.45)
    c.setFont(SCHRIFT_FETT, 90 if seite.breite < seite.hoehe else 70)
    c.translate(seite.breite / 2, seite.hoehe / 2)
    c.rotate(35)
    c.drawCentredString(0, -20, text)
    c.restoreState()


def listenkopf(c, seite: Seite, titel: str, zeilen: list[str], seitenzahl: int, gesamt: int) -> float:
    """Kopf fuer Listen im Querformat (H 5.4): Kennlinie, Logo, Titel, Untertitelzeilen, Seite X von Y.
    Rueckgabe: y-Position, ab der der Tabellenbereich beginnt."""
    kennlinie(c, seite, seite.hoehe, 3 * mm)
    logo(c, seite, breite=30 * mm)
    c.setFont(SCHRIFT_FETT, 13)
    c.setFillColor(SCHWARZ)
    y = seite.hoehe - 16 * mm
    c.drawString(RAND_L, y, titel)
    c.setFont(SCHRIFT, 8.5)
    c.setFillColor(ANTHRAZIT)
    for zeile in zeilen:
        y -= 4.5 * mm
        c.drawString(RAND_L, y, zeile)
    c.setFont(SCHRIFT, 8)
    c.drawRightString(seite.breite - RAND_R, 12 * mm, f"Seite {seitenzahl} von {gesamt}")
    kennlinie(c, seite, 18 * mm, 1.0 * mm)
    c.setFont(SCHRIFT, 7)
    c.drawCentredString(seite.breite / 2, 13 * mm, FUSS_1 + " | " + FUSS_2)
    return y - 6 * mm


def umbrechen(text: str, breite: float, schrift: str = SCHRIFT, groesse: float = 10.5) -> list[str]:
    return simpleSplit(text, schrift, groesse, breite) or [""]
