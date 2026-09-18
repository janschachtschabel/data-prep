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
- **Was die Textfelder enthalten** — eine Zeile je gewählter Textspalte. Kreuze
  „mehrere Werte" an, wenn eine Zelle mehrere Angaben enthält (Schlagwörter sind
  kommagetrennt, ein Titel mit Komma bleibt ein Titel), und gib das Trennzeichen
  und die Mindestanzahl an. Der Hinweis daneben ist die eine Zeile, die die KI
  über dieses Feld zu sehen bekommt — je konkreter, desto brauchbarer das
  Ergebnis. Diese Angaben gelten für beide KI-Werkzeuge darunter.
- **Enrich** — füllt Lücken in **einem frei gewählten Feld** per KI: Schlagwörter,
  Beschreibung, oder jede andere Textspalte deines Datensatzes. Vorhandene Inhalte
  werden **nie** überschrieben: eine zu kurze Liste wird ergänzt, nicht ersetzt,
  und eine leere Antwort ändert nichts. Jede Ergänzung wird in einer Spalte
  `enriched_fields` vermerkt. Für die WLO-Spalten sind Schlagwörter schon als
  Liste (mindestens 3) und mit einem passenden Hinweis vorbelegt. Stoppt ein Lauf
  unterwegs an einer Grenze (etwa dem Token-Budget), bleiben die bis dahin
  angereicherten Zeilen erhalten, und das Ergebnis sagt, warum er endete. Sobald
  das Budget es zulässt, füllt ein weiterer Lauf auf dem Ergebnis die übrigen
  Lücken.
- **Mindestanzahl Zeilen je Label** — für ungleich verteilte Datensätze: ein Fach
  mit 300 Zeilen und eines mit 3 sind für das Training nicht dasselbe. Gib die
  Mindestzahl an (z. B. 100) und klicke **Vorschau**: sie zeigt je Label, wie viele
  Zeilen fehlen, wie viele Modellaufrufe das kostet und **wie viel Prozent des
  Labels danach erzeugt wäre** — ohne dass etwas aufgerufen wird. Erst danach lässt
  sich **Erzeugen** klicken.

  Die Vorschau nennt außerdem, wie viele Modellaufrufe der Lauf **höchstens**
  brauchen kann, und das Budget daneben. Passt das nicht, wird der Lauf gar nicht
  erst gestartet. Stoppt ein Lauf unterwegs an einer anderen Grenze (etwa dem
  Token-Budget), bleiben die bis dahin erzeugten Zeilen erhalten, und das Ergebnis
  sagt, warum er endete. „Höchstens erzeugen" begrenzt einen Lauf; ein weiterer
  Lauf auf dem Ergebnis macht weiter, wo der erste aufgehört hat.

  **Am besten erst aufteilen, dann den `_train`-Teil auffüllen.** Die Beispiele
  stammen dann aus Zeilen, die der Holdout nie hatte, und auch seltene Label
  bleiben auswertbar. Ändert sich nach der
  Vorschau etwas am Formular — Datensatz, Felder, Zahlen —, muss sie neu berechnet
  werden, bevor erzeugt wird. Das Ergebnis ist immer ein neuer Datensatz.

  Vier Dinge, die du dazu wissen solltest:
  - Erzeugt wird **aus den echten Zeilen des Labels**. Ein Label, dessen Zeilen
    gar keinen Text enthalten, wird übersprungen statt erfunden — auch dann, wenn
    es nur schon erzeugte Zeilen hat.
  - Jede erzeugte Zeile bekommt die Spalte `generated_for`, jede echte Zeile, die
    als Beispiel diente, die Spalte `example_for`. Beim Aufteilen in train/holdout
    bleiben beide **im Training** — sonst würde man das Modell an Texten prüfen,
    die aus denselben Beispielen geschrieben wurden, an denen es gelernt hat.
    Wer erst auffüllt und dann aufteilt, riskiert deshalb, dass ein Label mit
    wenigen echten Zeilen im Holdout ganz fehlt; das Split-Ergebnis nennt solche
    Label beim Namen.
  - Ein von 3 auf 100 gehobenes Label hat das Modell trotzdem kaum gesehen. Die
    Prozentzahl in der Vorschau sagt dir, wie sehr ein späterer F1-Wert für dieses
    Label über die Generierung spricht statt über deine Daten.
  - Die Kreuzvalidierung von api_v3 beim Training kennt diese Markierungen nicht
    und bezieht erzeugte Zeilen mit ein. Beurteile ein Modell am Holdout.
  - Das Ergebnis nennt, was die Prüfungen verworfen haben (Wiederholungen, zu
    kurz, unvollständig) — auch diese Aufrufe sind bezahlt.

## Beliebige Tabellen bearbeiten (Reiter „Tabellen")

Der Reiter **Refine** setzt voraus, dass schon feststeht, welche Spalte das Label
ist. **Tabellen** ist die Schicht darunter: eine Tabelle als Tabelle, bevor
darüber entschieden wurde.

### Einlesen

CSV, JSON oder JSONL, roh oder gepackt (`.gz`). „Automatisch erkennen" liest das
Format aus der Endung und erkennt gzip an den Bytes — eine als `.csv` benannte,
tatsächlich gepackte Datei funktioniert also trotzdem. Trennzeichen und
Zeichensatz sind für CSV frei wählbar.

**Verschachteltes JSON wird flach geklopft.** Ein WLO-Export verpackt jede
Eigenschaft in eine Liste; daraus wird `properties.cclom:title` als ganz normale
Spalte. Listen einfacher Werte werden zusammengeführt. Eine Liste von *Objekten*
bleibt als JSON-Text stehen — dafür gibt es keine sinnvolle Spalte, und
stillschweigend zu verwerfen wäre schlimmer als eine hässliche Zelle.

Ein solcher Export hat leicht über hundert Spalten. Der übliche nächste Schritt
ist deshalb „Nur diese Spalten behalten".

### Ansehen

Die Zeilenansicht blättert in Seiten zu 50 und durchsucht die **sichtbaren**
Spalten. Die Statuszeile nennt beides — „Zeige 1–50 von 30 passenden Zeilen (120
insgesamt)" —, damit erkennbar ist, ob die Suche das getroffen hat, was gemeint
war.

**Spaltenprofil** beantwortet „was ist hier eigentlich drin": Füllgrad,
Kardinalität, die häufigsten Werte und bei Zahlenspalten deren Wertebereich. Eine
Spalte gilt als numerisch, wenn 80 % ihrer gefüllten Zellen sich als Zahl lesen
lassen; wie viele das waren, steht daneben.

### Ändern

Jedes **Anwenden** schreibt einen **neuen** Datensatz und lässt das Original
unberührt — ein falscher Schritt kostet also nichts. Das Ergebnis wird
ausgewählt, sodass der nächste Schritt darauf aufsetzt; die angewandten Schritte
stehen über den Karten. **Vorschau** zeigt, was passieren würde, ohne zu
schreiben.

**Nichts wird ungefragt überschrieben.** Ist der Zielname schon vergeben,
fragt die App nach: „… existiert bereits. Überschreiben?". Bei *Abbrechen*
bleibt alles, wie es war. Das gilt überall, wo etwas unter einem Namen
gespeichert wird — Import, Anwenden, Aufteilen, Anreichern, Zusammenführen,
Referenzen, Vokabulare und Seed-Sets. Nur wer als Ziel denselben Datensatz
wählt, auf dem er gerade arbeitet, wird nicht gefragt: das ist Weiterarbeiten,
kein Überschreiben. Beim Neuaufbau eines Seed-Sets lohnt die Frage besonders,
denn dabei gehen alle von Hand gepflegten und per KI erzeugten Seeds verloren.

**Zeilen filtern.** Regeln aus Spalte, Vergleich und Wert, verknüpft mit UND oder
ODER. Wichtig zu wissen: der Speicher ist reiner Text, `"9"` wäre also größer als
`"10"`. Deshalb entscheidet der **Wert der Regel** — eine Zahl vergleicht
numerisch, ein Text vergleicht als Text. `jahr ≥ 2015` rechnet, `datum ≥
2026-01-01` vergleicht buchstabenweise, was ein ISO-Datum genau will. Zellen, die
sich nicht als Zahl lesen lassen, passen nie auf eine Zahlenregel; wie viele das
waren, meldet die Statuszeile. Das ist der Hinweis darauf, dass eine Regel auf
der falschen Spalte sitzt.

`beginnt mit` ist der Weg zu einem URI-Grundstamm — im `taxonid`-Feld stecken
zwei Vokabulare, und nur das Präfix trennt Schul- von Hochschulfächern.

**Spalten** behalten, entfernen oder umbenennen (`alt=neu`). Die angegebene
Reihenfolge wird die neue Spaltenreihenfolge der Ausgabe.

**Dubletten** über eine oder mehrere Schlüsselspalten. „Dubletten zählen" ist
zerstörungsfrei und nennt genau die Zeilenzahl, die „Dubletten entfernen" wegnähme.
Eine Zeile mit **unvollständigem** Schlüssel gilt nie als Dublette: fehlt bei
`(url, quelle)` die URL, wäre sonst jede Zeile derselben Quelle betroffen. Wie
viele Zeilen so aussehen, wird mitgemeldet — meist ein Zeichen für einen
schlechten Schlüssel.

**Verbinden** zweier Datensätze über `hier=dort`. **Erst Größe prüfen** rechnet
aus, wie groß das Ergebnis würde, *ohne* es zu bauen: wiederholt sich der
Schlüssel auf beiden Seiten, vervielfachen sich die Zeilen, und genau das ist die
Falle. Die Prüfung sagt es vorher; oberhalb von fünf Millionen Zeilen wird der
Join abgelehnt statt versucht.

### Ausgeben

CSV mit wählbarem Trennzeichen, gepackt, JSON oder JSONL. Die Vorgabe ist die
Semikolon-CSV, die api_v3 liest.

## Wichtig zur Auswertung

Synthetische und angereicherte Zeilen sind **nur zum Training** gedacht. Bewerte
die Qualität immer auf einem echten, textdisjunkten Holdout (den erzeugt
„Prepare for training"). Interne Messwerte auf gemischten Daten sind zu
optimistisch — der Audit-Report weist ausdrücklich darauf hin.
