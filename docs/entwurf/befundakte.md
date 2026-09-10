# Befund vor der Entwurfsphase (Stand 10.09.2026)

> Befundakte der Entwurfsphase vom 10.09.2026. Faktenbasis für alle Arbeitspapiere; Abschnitt 6 ist als Fragen F4, F5, F6, F10, F12, F20 im Umsetzungsplan aufgenommen.

Dieses Dokument fasst alle vor der Entwurfsphase ermittelten Fakten zusammen. Es enthaelt nur Gemessenes und Gelesenes, keine Annahmen. Wo etwas nicht ermittelt werden konnte, steht das ausdruecklich.

## 1. Repository

- Pfad: /home/user/objekt-bernahme_automatisieren.  (der Punkt am Ende gehoert zum Verzeichnisnamen)
- Remote: https://github.com/v3ni94/objekt-bernahme_automatisieren.
- Zustand: vollstaendig leer. Kein Commit lokal, kein Commit auf dem Remote (git ls-remote liefert nichts). Keine Dateien ausser .git.
- Arbeitsbranch: claude/eigentuemer-sonstiges-umstellung-faduqc
- Folge: Es gibt keinen Bestandscode und keine Vorarbeiten. Die in CR-05 Abschnitt 0.1 geforderte Liste der Stellen, die von einer Fuenf-Ordner-Struktur oder 05_Sonstiges ausgehen, ist leer.
- Der CR-Text liegt jetzt unter docs/anforderungen/CR-05_Eigentuemerakte_Sonstiges.md im Repository.

## 2. Zielserver (VPS 187.124.23.80)

- Aus der Entwicklungsumgebung NICHT erreichbar (TCP 22 blockiert bzw. keine Route). Kein SSH-Zugang vorhanden.
- Folge: nproc, free -h, df -h, docker network ls, Traefik-Konfiguration (externes Netzwerk, Cert-Resolver, Entrypoints) konnten NICHT ausgelesen werden. Diese Werte sind im Plan als offene Messpunkte mit exakten Befehlen fuer den Auftraggeber zu fuehren. Keine Annahmen ueber vCPU, RAM, Disk, Netzwerkname oder Resolver treffen. Compose-Datei mit Platzhaltern (Variablen) fuer diese Werte planen.
- IONOS Tarif "VPS KVM 8": konkrete Ausstattung nicht verifiziert, wird gemessen.

## 3. Entwicklungsumgebung (nur zur Einordnung, nicht der Zielserver)

- Python 3.11.15, Node 22, Docker-CLI vorhanden, 4 vCPU, 15 GiB RAM. Kein Zugriff auf den VPS, kein Zugriff auf das Google-Konto ablage@muellerhv.de.

## 4. Vorhandene Stammdaten (Skill objektstammdaten)

Quelle: Immoware24-Export der Hausverwaltung Mueller GmbH, Datenstand 01.07.2026, als CSV im Skill-Verzeichnis
/root/.claude/skills/synced/5c2afa63-9adf-4cbb-9db6-72250ba52900_41249ec5-5ad4-455b-a236-a5aca5ab343f/objektstammdaten/references/
(Dateien: einheiten.csv, kontakte.csv, objektregister.md). ACHTUNG: enthaelt personenbezogene Daten. In Entwurfsdokumente KEINE realen Namen, Adressen, Telefonnummern oder E-Mails uebernehmen. Nur Strukturmerkmale verwenden, Beispiele synthetisch waehlen.

Relevante Strukturbefunde:
- 67 aktive Objekte, 869 aktive Verwaltungseinheiten, 1.737 Einheiten insgesamt (inkl. Status abrechnung, archiv, technisch).
- Verwaltungsarten im Export: "WEG-Verwaltung", "Mietverwaltung", "WEG mit SE-Verwaltung".
- Objektnummern sind NICHT durchgaengig dreistellig. Verteilung nach Stellenzahl (alle Status): 2-stellig 59 Zeilen, 3-stellig 1.661, 4-stellig 1, 5-stellig 15, 6-stellig 1 (999999 = technisches Buchungsobjekt FONATA, keine Liegenschaft). Aktive Objekte mit abweichender Stellenzahl: 82 (Shalomweg 3) und 10014 (Friedhofstrasse 17). Abrechnung: 2911. Archiv: 60, 61, 63, 66, 81, 83, 84, 10012, 10013.
  Folge: Die CR-Vorgabe "dreistellige Objektnummer" trifft nicht auf den gesamten Bestand zu. Erkennungsregel muss als "fuehrende Ziffernfolge, durch Leerzeichen/Unterstrich/Komma vom Rest getrennt" geplant werden, Stellenzahl konfigurierbar (z. B. 2 bis 6), Eindeutigkeit ueber die Zahl. Offene Frage an Auftraggeber: Nullauffuellung (082) oder Ist-Nummer (82) bei Neuanlage.
- Spalten einheiten.csv: Objekt-Nr; Status; Objekt; Verwaltungsart; Gebaeude; VE-Nr; VE-Beschreibung; Lage; Eigentuemer; Hausgeld_EUR_mtl; Mieter; Miete_EUR_mtl
- VE-Nr ist eine rein numerische Immoware-Kennung (z. B. 10001, 10002 oder 51, 14), NICHT die fachliche WE-Nummer.
- VE-Beschreibung traegt die fachliche Einheitenbezeichnung. Beobachtete Muster (Ziffern als N): "WEN" (761), "WE N" (617), "SN" (55, Stellplatz), "Garage N" (29), nur "N" (23), "GE N" (21, Gewerbe), "MVW N" (19), "Stellplatz Nr. N" (17), "GAN" (17), "TGN" (16, Tiefgarage), "SPN" (12), "GA N" (11), "GEN" (10), "STN" (9), "Haus N" (9), "Container N" (7), "Wohnung N" (6), "WE N - N.OG rechts/mitte/links" (je 6), "Lagerhalle N" (6), "STPN" (5), "VEN_SILN_N.L" u. ae. (4).
  Folge: unit_label muss frei sein, unit_number wird aus der Ziffernfolge abgeleitet, unit_type ueber ein konfigurierbares Praefix-Mapping (WE/Wohnung -> apartment, GE -> commercial, S/ST/STP/SP/Stellplatz -> parking, GA/Garage -> garage, TG -> underground_parking, sonst other). Normalisierung "WE 14" und "WE14" -> gleiche Einheit.
- Eigentuemerfeld ist ein Freitext mit mehreren Personen in wechselnden Schreibweisen: "Nachname, Vorname & Vorname", "Nachname, Vorname u. Vorname", "Vorname und Vorname Nachname", "Vorname u. Vorname Nachname", GbR-Bezeichnungen, "c/o"-Zusaetze, Schraegstrich-Trennung mehrerer Personen. Folge: Namens-Splitter mit Konfidenz und Review-Pflicht, nie stilles Uebernehmen.
- Spalten kontakte.csv: ID; Name; Briefanrede; Adresse; PLZ; Stadt; Land; Telefon; E-Mail. 46 Namen doppelt.
- Nicht im Export: Miteigentumsanteile (MEA), Wirtschaftsjahr, IBAN, Beiraete, Versicherer, Wohnflaechen, Kautionen, Salden.
- Das CR-Beispielobjekt "623 Duesseldorf, Joachimstrasse 49" ist NICHT im Bestand. Die Anwendung dient offenbar der Uebernahme neuer Objekte von Vorverwaltungen.
- Widerspruch zum CR: CR sagt "keine Bestandsdaten". Der Immoware24-Export existiert aber und koennte als erster Import (Formatprofil Immoware24) ueber das Review Center dienen. Offene Frage an Auftraggeber, keine Annahme.

## 5. Corporate Identity Hausverwaltung Mueller GmbH (Skill hvm-ci)

Verbindlich fuer alle von der Anwendung erzeugten Dokumente der HVM (PDF-Listen, Nachforderungsschreiben):
- Firma: Hausverwaltung Mueller GmbH, Rheinpromenade 13, 40789 Monheim am Rhein, Amtsgericht Duesseldorf HRB 104762, Geschaeftsfuehrer Timo Mueller, www.muellerhv.de. Keine Telefon-, E-Mail-, Bank- oder Steuerangaben erfinden.
- Farben: Orange #E6A83C (Akzent, Tabellenkoepfe), Anthrazit #87888A, Mittelgrau #9C9D9F, Hellgrau #D7D8DA (alternierende Zeilen), Umrissgrau #ECECEC, Textschwarz #1A1A1A.
- Schrift: Helvetica/Arial, 10 bis 11 pt.
- Kennlinie am oberen Blattrand (4 Segmente Anthrazit 0-40 %, Mittelgrau 40-60 %, Orange 60-67,5 %, Hellgrau 67,5-100 %), Fusszeile mit Pflichtangaben, Logo rechts oben (Datei assets/Logo_HVM.jpg, 1320x1143).
- Es existiert ein ReportLab-Skript scripts/hvm_briefkopf.py mit Bausteinen (briefkopf_seite1, anschriftfeld, infoblock, betreff, unterschriftsblock, fusszeile, folgeseite). Fuer PDF-Listen (A4 quer) und Nachforderungsschreiben ist die Wiederverwendung dieser Bausteine bzw. deren Portierung zu planen.
- Sprachregel: keine Gedankenstriche in deutschen Texten.

## 6. Im CR erkannte Widersprueche und Klaerungsbedarf (fuer offene Fragen)

1. "Dreistellige Objektnummer" vs. realer Bestand mit 2- bis 5-stelligen Nummern (siehe 4).
2. "Bestehenden Nachforderungsgenerator erweitern, nicht neu bauen" und "Requirement Engine erweitern" (Abschnitt 12), aber es gibt keinen Code. Beides muss neu gebaut werden oder es existiert eine Vorlage ausserhalb des Repos (Word, Excel, anderes System). Nachfragen.
3. CR-05 setzt eine Basis voraus, die nirgends spezifiziert ist: Aufbau von 01_Legitimationsunterlagen, 02_Stammakte, 03_Buchhaltung, 04_Mieterakte (Unterstrukturen, Dokumenttypen), Grundfunktionen Review Center, Requirement Engine, Nachforderung. Frage: Existieren CR-01 bis CR-04 oder ein Lastenheft? Bis zur Antwort im Plan als Annahme fuehren und minimal definieren.
4. Definition of Done "Kein Vorkommen von 05_Sonstiges im Code (Grep leer)" kollidiert mit Abschnitt 9.3, der das Erkennen eines Altordners 05_Sonstiges in Drive verlangt. Loesung planen: Alt-Bezeichnung als Konfigurationswert (legacy alias in DB-Seed oder Config), nicht als Literal im Anwendungscode; Grep-Pruefung auf src/ bezogen, Seed-Datei ausgenommen und dokumentiert.
5. Schreibweise der Ordner: Hauptordner "05_Eigentuemerakte" mit Umlaut ("05_Eigentümerakte"), Unterordner aber ASCII ("Wirtschaftsplaene", "Beschluesse"). CR-Vorgabe ist wortgetreu umzusetzen, Bestaetigung einholen.
6. Wurzelpfad "Meine Ablage/..." ist ein persoenliches My Drive des technischen Kontos, keine geteilte Ablage (Shared Drive). Drive-Adapter fuer beides auslegen (supportsAllDrives), Klaerung ob spaeter Umzug in Shared Drive geplant ist.
7. Abschnitt 4 nennt "WE01_Nachname" als Standard. Bei Einheiten, die keine Wohnung sind (Stellplatz, Garage, Gewerbe), ist das Praefix zu klaeren (z. B. "ST03_Nachname", "GE01_Nachname") oder immer "WE".
8. Traefik-Werte, Serverressourcen, DNS, OAuth-App, AVV mit OpenAI und Anthropic: alles Voraussetzungen des Auftraggebers, nicht pruefbar aus der Entwicklungsumgebung.
