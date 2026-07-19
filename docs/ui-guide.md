# data-prep — Leitfaden für die Oberfläche

Dieser Leitfaden erklärt die Weboberfläche in einfacher Sprache — ohne
IT-Vorwissen. Die Oberfläche erreichst du unter **http://127.0.0.1:8110/ui**.

## Anmelden

Oben erscheint eine schmale Anmeldezeile. Trage den API-Schlüssel ein (den die
Serverbetreiberin vergibt) und klicke **Anmelden**. Läuft der Server ohne
Schlüssel (nur lokal), bist du automatisch angemeldet.

**Sprache:** Mit dem Knopf **Deutsch/English** (in der Anmeldezeile und oben
rechts) schaltest du die gesamte Oberfläche zwischen Deutsch und Englisch um —
Menüs, Hilfetexte, Formularbeschriftungen, Knöpfe sowie Lauf-, Ergebnis- und
Fehlermeldungen. Die Auswahl bleibt für den Tab erhalten, und offene Listen
werden beim Umschalten sofort in der neuen Sprache neu aufgebaut.

Die Oberfläche hat sieben Reiter oben: **Anleitung, Läufe, Prüfen, Seeds,
Vokabulare, Referenzen, Aufbereiten**. Ganz links fasst **Anleitung** die drei
typischen Abläufe kurz zusammen (nur KI, hybrid, Daten säubern); danach arbeitest
du dich normalerweise von links nach rechts.

## Der schnellste Weg zu einem synthetischen Datensatz (nur KI, ohne eigene Daten)

1. **Vocabularies** → Vokabular laden. Am schnellsten über die **Default-Knöpfe**
   (Bildungsstufen, Schulfächer, Hochschulfächer, Zielgruppe, Inhaltstyp,
   Inhaltstyp-Gruppen) — ein Klick füllt Adresse, Name und **Metadatenfeld**; dann
   *Fetch vocabulary*. Alternativ eine SkoHub-Adresse einfügen oder eine Datei
   hochladen (JSON-LD **oder** SkoHub-Turtle `.ttl`). Ist gar kein Link/Datei zur
   Hand, kannst du unter **Selbst eintippen (manuell)** die Labels einfügen — ein
   Konzept pro Zeile, entweder nur `Label` oder `Label | URI`. Das optionale
   **Metadatenfeld** (z. B. `properties.ccm:taxonid`) hält fest, zu welcher
   Datensatz-Spalte die Labels gehören. Wichtig: die konkreten Werte kommen aus dem
   gewählten Vokabular — für Schulfächer lade *Schulfächer* (discipline), nicht
   *Hochschulfächer* (beide nutzen `taxonid`, sind aber verschiedene Wertemengen).
2. **Seeds** → „Build a seed set". **Was ist ein Seed?** Ein Seed ist ein kurzer
   Beispiel-Eintrag (Titel, Beschreibung, Schlagwörter), der der KI zeigt, welche
   Art von Material du für ein Label möchtest. Ein paar Seeds pro Fach steuern die
   spätere Generierung, damit sie beim Thema bleibt und trotzdem abwechslungsreich
   ist. Name vergeben, das Vokabular auswählen, Referenzset leer lassen (= reiner
   KI-Modus). Für einzelne Fächer kannst du im Editor unten **„Bootstrap 4 seeds
   (LLM)"** klicken — die KI erzeugt die Beispiel-Einträge selbst.

   Kurzentscheidung, welcher Modus:
   - **Nur KI + Vokabular:** kein eigenes Datenset nötig — Referenzset leer lassen.
   - **Bestand + KI + Vokabular:** zuerst unter *References* deine CSV hochladen
     (wird beim Import von PII bereinigt), dann hier als Referenzset wählen. Die
     Seeds werden aus deinen echten Beispielen destilliert; ein Leakage-Filter hält
     Fast-Kopien deiner Daten aus dem Ergebnis heraus.
3. **Runs** → „New run". Seed-Set wählen, Umfang festlegen (Beispiele pro Fach,
   Länge). Der aufklappbare Hinweis **„Was erzeugt ein Lauf?"** erklärt, welche
   Felder entstehen: pro Eintrag drei Textfelder (Titel, Beschreibung,
   Schlagwörter) und das Konzept als Klassifikations-Label (in das Metadatenfeld
   des Vokabulars). Im Feld **Kontext / Vorgabe** kannst du optional angeben, worum
   es geht (z. B. „Deutsche Schulfächer, Sekundarstufe") — das steuert die
   Generierung, wenn die Labels allein nicht selbsterklärend sind. Klicke zuerst
   **„Dry run (cost estimate)"** — das zeigt die geschätzten KI-Kosten, ohne etwas
   zu erzeugen. Passt es, **„Start run"**. Der Fortschritt aktualisiert sich
   automatisch; du kannst abbrechen und später fortsetzen.
4. **Review** → den Lauf auswählen. Jede Karte ist ein erzeugter Eintrag. Mit der
   Tastatur **a** (annehmen) oder **d** (verwerfen), oder per Knopf. Verworfene
   Einträge werden mit **„Regenerate discarded"** ersetzt.
5. **Runs** → beim fertigen Lauf **CSV** oder **JSONL** herunterladen, **Audit**
   ansehen (enthält Pflicht-Hinweise zur Nutzung) oder **Push to api_v3**.

**Mit eigenen Daten (Hybrid):** Lade unter **References** eine kuratierte CSV
hoch (wird beim Import automatisch von persönlichen Daten bereinigt) und wähle
sie beim Seed-Set-Bau als Referenz. Dann entstehen Seeds aus deinen Daten, und
ein Leakage-Filter verhindert, dass Fast-Kopien deiner Daten im Ergebnis landen.

## Bestehende Datensätze bearbeiten (Reiter „Refine")

Lade oben eine CSV hoch. Sie wird unverändert gespeichert. Wähle sie dann im
Bereich **Operations** aus und trage bei Bedarf die Textspalten/Label-Spalte ein
(die WLO-Standardspalten sind voreingestellt).

- **Analyze** — Überblick: Anzahl Zeilen, Label-Verteilung, Dubletten, leere
  Felder, gefundene persönliche Daten.
- **Training preflight** — simuliert die Aufbereitung der Trainings-API und zeigt
  die *tatsächliche* Trainingsmenge je Label vorab. So siehst du vorher, warum
  aus z. B. 32.728 Zeilen nur 25.068 werden (Dubletten, zu kurze Texte, seltene
  Labels).
- **Filter** — eine Operation wählen (Dubletten entfernen, Längenkorridor,
  Label-Filter, pro Label begrenzen, Markup säubern, persönliche Daten maskieren
  oder entfernen). **Preview** zeigt die Wirkung, ohne etwas zu schreiben;
  **Apply** legt einen neuen Datensatz an. Das Original bleibt unangetastet.
  **Mehrere Bereinigungen nacheinander:** ja — jedes *Apply* erzeugt einen neuen
  Datensatz, der danach automatisch oben ausgewählt ist. Wende einfach den
  nächsten Filter darauf an; unter dem Auswahlfeld siehst du die Liste der bereits
  angewandten Schritte (das Operations-Protokoll).
- **Combine datasets** — mehrere Datensätze auswählen (die oberste Quelle
  „gewinnt" bei doppelten Texten), **Suggest mappings** ordnet die Spalten
  automatisch dem Zielschema zu (bei Bedarf korrigieren), dann zusammenführen.
  Eine `source`-Spalte hält fest, woher jede Zeile stammt.
- **Prepare for training** — teilt den Datensatz fair in **train** und
  **holdout** (kein Text kommt auf beiden Seiten vor → ehrliche Auswertung) und
  kann den Datensatz direkt an die Trainings-API schicken.
- **Label audit** — ein trainiertes Modell gibt eine Zweitmeinung. Zeilen, bei
  denen das Modell dem Gold-Label deutlich widerspricht, landen in einer
  Prüfliste. Es wird **nichts automatisch umgelabelt** — das entscheidet ein
  Mensch.
- **Enrich** — ergänzt fehlende Schlagwörter oder leere Beschreibungen per KI.
  Vorhandene Inhalte werden **nie** überschrieben; jede Ergänzung wird in einer
  Spalte `enriched_fields` vermerkt.

## Wichtig zur Auswertung

Synthetische und angereicherte Zeilen sind **nur zum Training** gedacht. Bewerte
die Qualität immer auf einem echten, textdisjunkten Holdout (den erzeugt
„Prepare for training"). Interne Messwerte auf gemischten Daten sind zu
optimistisch — der Audit-Report weist ausdrücklich darauf hin.
