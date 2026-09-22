# KI-Anbieter anbinden (Stufe 3, OpenAI)

Stand: 20.09.2026. Ergänzt `docs/architektur.md` Abschnitt 6.7 (Provider-Abstraktion) und `docs/anleitungen/admin-konfiguration.md` (Schlüssel `ai.*`). Zuständig: Geschäftsführung (Vertrag, Freigabe, Kostenlimit) und Betreuung (Konfiguration, Kontrolle). Dieser Abschnitt ist eine technische Anleitung und keine Rechtsberatung; die vertraglichen Punkte prüft der Datenschutzberater.

## Was die Stufe 3 tut und was den Server verlässt

Dokumente, bei denen Regeln (Stufe 1) und lokaler Klassifikator (Stufe 2) unter `classification.threshold_stage3_call` (Seed 0,9) bleiben, gehen an den freigegebenen Anbieter. Übertragen werden nur der maskierte, gekürzte Textauszug (erste drei Seiten und letzte Seite, höchstens `ai.max_input_tokens` Token), der maskierte Dateiname, die Verwaltungsart, die Taxonomie und Hinweise ohne Personenbezug. IBAN, Kontonummern, BIC und Ausweisnummern sind vorher ersetzt, Dokumente mit erkannter Ausweiskopie gehen nie an die Stufe 3. Namen und Anschriften im Fließtext bleiben lesbar; deshalb sind Auftragsverarbeitungsvertrag, Datenresidenz und Trainingsausschluss Voraussetzung (Umsetzungsplan V-10, V-12, V-13). Jeder Aufruf steht ohne Prompttext in `ai_calls` (Token, Kosten nach Preisliste, Status), `classification.ai_sample_pct` (Seed 10) Prozent der KI-Entscheidungen gehen als Stichprobe ins Prüfcenter.

Ist der Anbieter freigegeben, wirkt das sofort auf alle laufenden Verarbeitungsläufe: jeder classify-Job unter der Schwelle ruft die KI auf. Das Kostenlimit je Objekt (`ai.providers.openai.cost_limit_eur_per_object`) begrenzt den Betrag je Objekt; bei Erreichen gehen weitere Dokumente ohne KI nach 06/01_Unklar mit Grund Kostenlimit und können später mit `ai-reclassify` nachgeholt werden.

## Voraussetzungen (Geschäftsführung)

1. Auftragsverarbeitungsvertrag mit OpenAI für die API-Nutzung abschließen (Data Processing Addendum in der OpenAI-Plattform), Kopie in die Verfahrensdokumentation (V-10).
2. Datenresidenz festlegen (V-12): Ein API-Projekt mit Region Europa anlegen; die Anfragen laufen dann über den EU-Endpunkt `https://eu.api.openai.com/v1`. Ohne EU-Projekt bleibt der Endpunkt leer und die Verarbeitung erfolgt am Standardstandort des Anbieters; das ist für die datenschutzrechtliche Bewertung relevant. Die Aussagen des Anbieters zu Trainingsausschluss und Aufbewahrung der API-Eingaben schriftlich sichern.
3. Kostenrahmen festlegen: Listenpreis des Modells (Preisseite des Anbieters), Kostenlimit je Objekt, optional Monatsdeckel als Alarm (`ai.monthly_budget_eur`).

## Schrittfolge auf dem Server (als root)

Vorbereitung der Shell wie bei allen Aktionen:

```bash
cd /opt/objektakte
B=claude/eigentuemer-sonstiges-umstellung-faduqc
d() { sudo -u deploy env SSH_ORIGINAL_COMMAND="$*" /opt/objektakte/scripts/deploy_remote.sh; }
```

### 1 API-Schlüssel als Secret hinterlegen (V-13)

Den Schlüssel in der OpenAI-Plattform im EU-Projekt erzeugen und direkt auf dem Server eintragen, nie per E-Mail oder Chat übertragen. Die Datei wird in Ort und Stelle beschrieben (`printf`, kein Editor), damit die in die Container eingebundene Datei denselben Inhalt zeigt; die Anwendung liest das Secret bei jedem Aufruf neu, ein Neustart ist nicht nötig.

```bash
printf '%s' 'HIER-DEN-SCHLUESSEL-EINFUEGEN' > /srv/objektakte/secrets/openai_api_key
chown root:root /srv/objektakte/secrets/openai_api_key
chmod 0444 /srv/objektakte/secrets/openai_api_key
history -c
```

Wurde die Datei durch ein Werkzeug ersetzt statt überschrieben (neue Inode), sehen die Container weiter den alten Inhalt; dann `docker compose up -d --force-recreate worker-io` ausführen.

### 2 Preisliste eintragen

Die Anwendung rechnet in EUR je 1.000 Token. Die Preisseite des Anbieters nennt meist USD je 1.000.000 Token; Umrechnung: Preis je 1.000.000 geteilt durch 1.000, multipliziert mit dem Wechselkurs. Beispiel für die Struktur (Zahlen sind Platzhalter und vom Auftraggeber einzusetzen), JSON ohne Leerzeichen, weil die Aktion nur ein Argumentwort annimmt:

```bash
d config-set $B 'ai.price_list={"version":"2026-09-20","models":{"MODELLNAME":{"input_per_1k":0.0000,"output_per_1k":0.0000}}}'
```

Ohne Preis für das konfigurierte Modell (fehlender Eintrag oder Platzhalter mit 0) greift das Kostenlimit je Objekt nicht, weil jeder Aufruf mit 0 EUR gebucht würde. Der Router arbeitet deshalb fail-closed: Ist ein Kostenlimit gesetzt und das Modell fehlt in `ai.price_list`, wird nicht aufgerufen und der Fall als `budget_blocked` mit Hinweis protokolliert (sichtbar in `ai-check` unter „Aufrufe der letzten 24 Stunden“). Ohne Kostenlimit laufen Aufrufe auch ohne Preis, dann mit 0 EUR gebucht; das ist nur für Tests gedacht. Der Nutzerteil des Prompts beginnt seit Version 2026-09-20.2 mit den für alle Dokumente identischen Teilen (Taxonomie, Schema), damit der automatische Prompt-Cache des Anbieters den Präfix wiederverwendet; die Ersparnis richtet sich nach der Preisspalte für zwischengespeicherte Eingabetoken, die Anwendung bucht weiterhin den vollen Eingabepreis (konservativ).

### 3 Anbieter konfigurieren und freigeben

Modellwahl: Das Modell muss strukturierte JSON-Ausgaben (Structured Outputs) unterstützen. Für Reasoning-Modelle (gpt-5, o-Reihe) setzt die Anwendung seit dem 20.09.2026 keinen `temperature`-Parameter mehr, weil diese Modelle ihn ablehnen. Für die Klassifikation reicht ein kleines, günstiges Modell; Modellnamen und Preise aus der Anbieterdokumentation übernehmen.

```bash
d config-set $B 'ai.providers={"openai":{"enabled":true,"model":"MODELLNAME","endpoint":"https://eu.api.openai.com/v1","region":"EU","timeout_s":30,"max_attempts":2,"cost_limit_eur_per_object":5},"anthropic":{"enabled":false,"model":null,"endpoint":null,"region":null,"timeout_s":30,"max_attempts":2,"cost_limit_eur_per_object":null}}'
```

Ohne EU-Projekt `"endpoint":null` setzen. `ai.provider_order` steht im Seed auf `["openai","anthropic"]`; Anthropic bleibt abgeschaltet, bis auch dort Vertrag und Schlüssel vorliegen.

### 4 Prüfen

```bash
d ai-check $B
d ai-check $B probe
```

`ai-check` zeigt Konfiguration ohne Geheimnisse, ob der Schlüssel vorhanden ist, die Preisliste, die Aufrufe der letzten 24 Stunden und ob das Modell beim Anbieter abrufbar ist (ohne Tokenverbrauch). Mit `probe` folgt eine echte Klassifikation eines synthetischen Textes ohne Personenbezug; erwartet wird Kategorie 05 mit hoher Konfidenz. Der Probeaufruf kostet wenige Token und erscheint nicht in `ai_calls`.

### 5 Testobjekt nachklassifizieren, dann Bestand

Zuerst ein Objekt mit vielen Unklar-Fällen, zum Beispiel Objekt 457 mit 552 Fällen (Stand 20.09.2026):

```bash
d ai-reclassify $B 457
d ai-reclassify $B 457+echt
```

Die Vorschau nennt die Anzahl der Dokumente, die erneut klassifiziert würden. Nach dem echten Lauf zeigen `d doc-status $B 457` und `d ai-check $B` (Aufrufe, Kosten, Status je Anbieter) das Ergebnis; im Prüfcenter erscheinen die Stichprobe und die weiterhin unklaren Fälle. Erst wenn Trefferquote und Kosten passen, den Bestand nachziehen:

```bash
d ai-reclassify $B echt
```

Der Nachklassifikationslauf nimmt nur offene Fälle `below_threshold` mit Grund Stufe 3 nicht freigegeben, KI nicht verfügbar oder Kostenlimit, die noch kein Mensch bearbeitet hat; Bestandsdateien in Drive werden weiterhin nie verschoben, sondern nur als Vorschlag gestellt.

## KI-Schiedsrichter der Objektzuordnung (Zweck `assign_object`, seit 22.09.2026)

Die KI greift immer, wenn die Anwendung ein Eingangsdokument nicht eindeutig einem Objekt zuordnen kann: Die lokale Bewertung (Anschriften, Objektnummern, Ordner, Regeln) ergibt keinen Automatismus (Prüffall oder kein Vorschlag), aber es gibt Kandidaten. Dann erhält der freigegebene Anbieter den maskierten Textauszug, den Dateinamen ohne Personennamen und die Kandidaten (Objektnummer, Verwaltungsart, alle Anschriften des Gebäudes einschließlich weiterer Anschriften von Eckobjekten, Bewertung, Belegarten). Keine Eigentümer- oder Mieternamen, keine Einheitenlisten.

Die Antwort (Schema `apps.ai.assignment.response_json_schema`) enthält: `is_object_document` (ist es überhaupt ein Dokument eines verwalteten Objekts oder eine interne Unterlage, in der die Anschrift nur beiläufig vorkommt, etwa eine Fahrtkostenabrechnung mit der Anschrift als Fahrtziel), `object_number` (nur aus den Kandidaten oder null), `multiple_objects` (Sammelbeleg ohne Hauptbezug), `other_addresses_role` (Rolle weiterer Anschriften: same_object, billing_address, sender_address, neighbor, travel_destination, multiple_objects), `confidence`, `reasoning`.

Wirkung im Eingang (`apps.sync.flows.assign.run_for_document`):

| KI-Antwort | Ergebnis |
|---|---|
| Objektdokument, Kandidat mit Konfidenz mindestens `sync.assignment_ai_min` (0,85) | Übernahme in das Objekt wie beim Regelwerk (Fall `ai_auto` erledigt, Audit `inbox.assign_ai`, Grund „KI-Zuordnung“) |
| Kandidat wurde für dieses Dokument bereits manuell verworfen (Lernbeispiel `reject`) | keine Übernahme, nur Vorschlag im Prüffall; der Mensch bleibt Herr der Entscheidung |
| kein Objektdokument | Dokument bleibt im Eingang, offener Fall `ai_not_object` ohne Vorschlag |
| Kandidat unter der Mindestkonfidenz, mehrere Objekte oder kein Kandidat | Prüffall wie bisher, der KI-Kandidat wird Vorschlag, die Einschätzung steht im Fall („KI-Einschätzung“ auf der Falldetailseite) |
| KI nicht verfügbar (kein Anbieter, Preisliste 0, Monatsdeckel, Fehler) | Prüffall wie bisher mit dem Grund im Fall (`context.ai.status`) |

Kosten und Schutz: Jeder Aufruf steht in `ai_calls` mit `purpose = assign_object`, Kosten nach `ai.price_list`, Circuit Breaker und Wiederholungen wie in Stufe 3. Die Aufrufe werden am Eingangsobjekt protokolliert; dort gilt statt des Kostenlimits je Objekt (`ai.providers.<p>.cost_limit_eur_per_object`, das für das Eingangsobjekt eine versteckte Gesamtsperre wäre) der Monatsdeckel des Anbieters `ai.monthly_budget_eur` (null = kein Deckel). Abschalten ohne Deploy: `sync.assignment_ai_enabled = false` (Deploy-Aktion `config-set`). Lokal senkt die Rolle „Fahrtziel“ (Wörter wie Fahrtziel, Reisekosten, Dienstfahrt vor der Anschrift) das Gewicht einer Anschrift, sodass solche Dokumente nicht mehr automatisch zugeordnet werden, sondern zur KI gehen.

Deploy-Aktion `ai-check` zeigt Anbieter, Preisliste und Probe; die Aufrufe der letzten 24 Stunden sind dort nach Anbieter, Zweck (`classify`, `assign_object`) und Status mit Kosten aufgeführt.

## Kontrolle im Betrieb

- `d ai-check $B`: Aufrufe der letzten 24 Stunden je Anbieter und Status mit Kosten nach Preisliste.
- Prüfcenter: Stichprobe der KI-Entscheidungen (`classification.ai_sample_pct`), Fälle mit Grund Kostenlimit oder KI nicht verfügbar.
- Statusbereich der Anwendung: Hinweis bei geöffnetem Circuit Breaker oder erreichtem Monatsdeckel.
- Kosten je Objekt: Summe `cost_eur` in `ai_calls`; bei Erreichen des Limits Status `budget_blocked`, Erhöhung über `ai.providers.openai.cost_limit_eur_per_object`, danach `ai-reclassify` für das Objekt.

## Rückweg

Der Anbieter lässt sich ohne Deployment abschalten; laufende Aufrufe enden, neue Dokumente unter der Schwelle gehen wieder nach 06/01_Unklar:

```bash
d config-set $B 'ai.providers={"openai":{"enabled":false,"model":"MODELLNAME","endpoint":"https://eu.api.openai.com/v1","region":"EU","timeout_s":30,"max_attempts":2,"cost_limit_eur_per_object":5},"anthropic":{"enabled":false,"model":null,"endpoint":null,"region":null,"timeout_s":30,"max_attempts":2,"cost_limit_eur_per_object":null}}'
```

Der Schlüssel kann jederzeit in der Anbieterkonsole widerrufen werden; die Secret-Datei dann mit `printf '' > /srv/objektakte/secrets/openai_api_key` leeren.
