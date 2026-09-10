# Design: data-prep — generische Tabellen-Werkbank

Status: ENTWURF, wartet auf Freigabe · Datum: 2026-09-10 · Autor: Claude + Jan
Ergänzt `plan-2026-07-10-synthdata-app.md` (Rev. 2), ersetzt ihn nicht.

## Goal

data-prep um ein fehlendes Stockwerk ergänzen: eine **generische
Tabellen-Werkbank**, die beliebige Spalten kennt — Formate lesen und schreiben,
über Spaltenwerte filtern, Spalten entfernen, zwei Dateien über Schlüssel
verbinden, Dubletten über Schlüssel finden, Zeilen ansehen und durchsuchen,
Spalten statistisch profilieren.

## Context

Heute ist jede Refine-Operation **trainingsdaten-zentriert**: sie setzt
Textspalten und eine Labelspalte voraus (`ctx["text_columns"]`,
`ctx["label_column"]`). Die acht Filter in `app/refine/filters.py` arbeiten
ausnahmslos auf dem kombinierten Text oder auf Labels. Das ist kein Defekt —
es ist die Schicht, für die die App gebaut wurde. Was fehlt, ist die Schicht
**darunter**: Operationen, die eine Tabelle als Tabelle behandeln.

Das tragende Element steht bereits: die Pipeline mit Ops-Historie
(`routes/refine.py:196-202` schreibt in einen Zieldatensatz und schreibt die
Herkunftskette fort). Neue Operationen können dieselbe Form benutzen —
`(df, params, ctx) -> (new_df, stats)` — und sich in dieselbe Historie
einreihen. Deshalb ist das ein Anbau, kein Umbau.

## Scope

**In scope**

- Lesen: CSV (Trennzeichen und Encoding wählbar), `.csv.gz`, JSON, JSONL,
  `.jsonl.gz` — verschachtelte Objekte werden zu Punktpfad-Spalten flach
  geklopft.
- Schreiben: CSV (Semikolon oder Komma), `.csv.gz`, JSON, JSONL — als Download.
- Zeilenfilter über eine **Regelliste** aus Spalte, Operator und Wert,
  verknüpft mit UND oder ODER.
- Spalten behalten, entfernen, umbenennen.
- Join zweier Datensätze über einen oder mehrere Schlüssel.
- Dublettenbericht und Dubletten-Entfernung über wählbare Schlüsselspalten.
- Zeilen-Viewer mit Seitenblättern und Volltextsuche.
- Spaltenprofil: Füllgrad, Kardinalität, häufigste Werte, bei numerischen
  Spalten Kennzahlen.
- LLM-Provider-Auswahl `openai`, `b-api-openai`, `b-api-academiccloud`.
- UI-Angleichung an api_v3: Seitenbreite, Feld- und Layoutoptimierungen.

**Out of scope**

- Eine Ausdruckssprache für Filter. Bewusst nicht: ein eigener Parser plus
  Fehlermeldungen, und `pandas.query()` wäre eine Einfallstür für
  Code-Ausführung. Als spätere Ausbaustufe notiert.
- JSONPath. Das Flachklopfen deckt die WLO-Struktur ab; eine Pfadsprache wäre
  eine neue Abhängigkeit ohne belegten Bedarf.
- Parquet, Excel. Keine Anforderung, jeweils eine neue Abhängigkeit.
- Datentyp-Konvertierung als eigene Operation. Die Regeln casten bedarfsweise.
- Nichts am Generierungszweig (vocabularies, seeds, runs, review) — der bleibt
  unberührt.

## Approach

### Entscheidung 1 — wo die generischen Operationen leben

**A: In `filters.py` mitwachsen.** Kleinster Eingriff auf den ersten Blick.
Aber die Datei ist bei 214 Zeilen, und ihr `ctx`-Vertrag ist label-zentriert;
generische Operationen würden ihn verwässern.

**B (gewählt): Eigene Module neben `filters.py`.** `rules.py` für den
Zeilenfilter, `columns.py` für Spaltenoperationen, `join.py`, `profile.py`.
Alle benutzen denselben Vertrag `(df, params, ctx) -> (new_df, stats)` und
dieselbe Ops-Historie, lesen aus `ctx` aber nur, was sie brauchen. Jede Datei
bleibt klar unter 300 Zeilen, jede hat einen Grund sich zu ändern.

**C: Plugin-Registry, die `filters.py` ablöst.** Über Bedarf. Es gibt genau
zwei Familien, nicht beliebig viele.

### Entscheidung 2 — eigener Endpunkt oder `/filter` erweitern

`app/routes/refine.py` hat **332 Zeilen** und liegt damit bereits über der
Grenze von rund 300 Zeilen, die die Konstitution setzt. Weitere Endpunkte dort
einzubauen ist keine Option. Der Schnitt ist also vorgegeben, nicht
Geschmackssache:

- `app/routes/refine.py` behält die trainingsdaten-zentrierten Endpunkte.
- `app/routes/tables.py` (neu) bekommt Import, Download, Viewer, Profil,
  generische Operationen und Join.

Beide schreiben in **dieselbe** Ops-Historie, damit eine Pipeline Label-Filter
und Tabellen-Operationen mischen kann. Die gemeinsame Logik "Vorschau oder
anwenden, dann Operation protokollieren" wandert dabei in eine Hilfsfunktion
`app/refine/apply.py`, die beide Routen benutzen — sonst stünde sie zweimal da.

### Entscheidung 3 — b-api anbinden

Der Skill `wlo-b-api-llm` (gemessen 21.08.2026) hält fest: die b-api ist
OpenAI-**kompatibel** im Pfadschema
`/api/v1/llm/{academiccloud|openai}/chat/completions`, authentifiziert aber
über **`X-API-KEY`**, nicht über `Authorization: Bearer`.

**A: Zweiter, handgeschriebener httpx-Client.** Unnötig — das Wire-Format ist
identisch.

**B (gewählt): Vorhandenen `openai.AsyncOpenAI` weiterbenutzen**, mit
`default_headers` für `X-API-KEY` und einer aus dem Provider abgeleiteten
`base_url`. Keine neue Abhängigkeit, der gesamte bestehende Pfad — Budgets,
Ledger, Retries, Testseam — gilt unverändert.

Der Provider ist **operator-kontrolliert** und bleibt es: er wird aus
`config.yaml` gelesen, nicht aus einem Request-Header. Das ist dieselbe
SSRF-Begründung, mit der `base_url` heute schon nicht pro Request
überschreibbar ist.

## Global constraints

Wörtlich aus `CLAUDE.md`, jede Aufgabe hat sie einzuhalten:

- Python 3.12 oder neuer, FastAPI, pydantic v2, pandas. **Keine neue
  Abhängigkeit ohne schriftliche Begründung** — dieser Plan braucht keine.
- Code, Kommentare und Docs **englisch**; Kommentare begründen das *Warum*.
  UI-Hilfetexte und Benutzerhandbuch **deutsch**.
- Test-first für Logik: roter Test, dann Implementierung, dann grün. Nie einen
  Test abschwächen, damit er besteht.
- Dateien unter rund 300 Zeilen, Schnitt nach Verantwortung. Routen bleiben
  dünn und delegieren.
- Kein pickle. Secrets nur über Umgebungsvariablen, nie geloggt, nie
  committet. Fehler werden nie verschluckt.
- Benutzergelieferte Namen durch `security.safe_name`; API-Key-Vergleich in
  konstanter Zeit.
- Single-Worker: Run-Store und Hintergrundaufgaben sind prozesslokal.
- `app/__init__.py` deckelt BLAS-Threads **vor** dem numpy-Import — das bleibt
  das Erste, was das Paket tut.

## Architecture

### Files

| Datei | Zeilen (Schätzung) | Verantwortung |
|---|---|---|
| `app/tabular.py` | ~160 neu | Bytes zu DataFrame und zurück: Format erkennen, CSV/JSON/JSONL/gz lesen und schreiben, verschachtelte Objekte flach klopfen |
| `app/refine/rules.py` | ~150 neu | Regelliste zu boolescher Maske zu gefilterten Zeilen; die fünfzehn Operatoren |
| `app/refine/columns.py` | ~90 neu | Spalten behalten, entfernen, umbenennen |
| `app/refine/join.py` | ~130 neu | Join zweier Datensätze über mehrere Schlüssel, mit Kollisions- und Explosionsbericht |
| `app/refine/profile.py` | ~140 neu | Spaltenprofil und Dublettenbericht über Schlüssel |
| `app/refine/apply.py` | ~60 neu | Gemeinsames "Vorschau oder anwenden und protokollieren" für beide Routen |
| `app/routes/tables.py` | ~200 neu | Import mit Formatoptionen, Download, Zeilen-Viewer, Profil, generische Operationen, Join |
| `app/llm_providers.py` | ~80 neu | Provider zu base_url, Auth-Header und Modellfähigkeiten |
| `app/refine/store.py` | +30 | `read_csv` weicht `tabular.read_table`; Schreiben unverändert |
| `app/refine/filters.py` | +25 | `dedupe_keys` kommt zur Dedupe-Familie |
| `app/config.py` | +25 | `provider`, `verbosity`, `reasoning_effort`, `b_api_base_url` |
| `app/llm.py` | -20/+15 | Fähigkeitslogik wandert nach `llm_providers.py`; `_get_client` setzt den Auth-Header |
| `app/main.py` | +2 | neuen Router einhängen |
| `app/static/ui/tables.js` | ~320 neu | Tabellen-Tab: Import, Viewer, Regelbau, Spalten, Join, Profil |
| `app/static/ui/style.css` | +20 | `--page-max: 1200px`, Tabellen- und Feldoptimierungen wie api_v3 |
| `app/static/ui/i18n.js` | +~60 | neue Schlüssel, deutsch und englisch |

Keine geplante Datei endet über 300 Zeilen. `refine.py` und `llm.py` liegen
heute darüber und werden durch diesen Plan **kleiner**, nicht größer.

### Data flow

```
Upload (CSV | CSV.gz | JSON | JSONL | JSONL.gz)
   -> tabular.read_table(format, separator, encoding, flatten)
   -> DataFrame (alles Strings, Punktpfad-Spalten)
   -> store.save_dataset            [data/refine/<name>.csv, immer Semikolon, UTF-8]

Operation (Regeln | Spalten | Dedupe-Schlüssel | Join)
   -> refine.apply.preview_or_apply(op, df, params, ctx)
   -> (new_df, stats)
   -> Vorschau: nur stats  |  Anwenden: neues Ziel plus fortgeschriebene ops-Historie

Ausgabe
   -> tabular.write_table(format, separator) -> Download
   -> oder apiv3.push_csv (unverändert)
```

Der interne Speicher bleibt bewusst **eine Form**: Semikolon-CSV, UTF-8, alles
Strings. Formate sind Sache der Ränder — Import und Download — nicht des
Kerns; sonst müsste jede Operation jedes Format kennen.

### Interfaces

```python
# app/tabular.py
SUPPORTED_READ  = ("csv", "csv.gz", "json", "jsonl", "jsonl.gz")
SUPPORTED_WRITE = ("csv", "csv.gz", "json", "jsonl")

def sniff_format(filename: str, raw: bytes) -> str
def flatten_record(obj: dict, *, list_separator: str = ",") -> dict[str, str]
def read_table(raw: bytes, *, fmt: str = "auto", separator: str = ";",
               encoding: str = "utf-8", flatten: bool = True,
               list_separator: str = ",") -> pd.DataFrame
def write_table(df: pd.DataFrame, *, fmt: str = "csv",
                separator: str = ";") -> bytes

# app/refine/rules.py
OPERATORS = ("eq", "ne", "lt", "lte", "gt", "gte", "between", "in", "not_in",
             "contains", "starts_with", "ends_with", "regex",
             "is_empty", "not_empty")

def evaluate(df: pd.DataFrame, rules: list[dict], *,
             combine: str = "and") -> pd.Series          # boolesche Maske
def filter_rows(df, params, ctx) -> tuple[pd.DataFrame, dict]

# app/refine/columns.py
def select_columns(df, params, ctx) -> tuple[pd.DataFrame, dict]   # params: keep[]
def drop_columns(df, params, ctx)   -> tuple[pd.DataFrame, dict]   # params: drop[]
def rename_columns(df, params, ctx) -> tuple[pd.DataFrame, dict]   # params: mapping{}

# app/refine/join.py
def key_cardinality(left: pd.DataFrame, right: pd.DataFrame,
                    keys: list[dict]) -> dict
def join_datasets(left: pd.DataFrame, right: pd.DataFrame, *,
                  keys: list[dict],            # [{"left": "id", "right": "uid"}]
                  how: str = "left",           # inner | left | right | outer
                  suffix: str = "_right",
                  coalesce: bool = False,
                  max_rows: int = 5_000_000) -> tuple[pd.DataFrame, dict]

# app/refine/profile.py
def profile_columns(df: pd.DataFrame, *, top_n: int = 10) -> dict
def duplicate_report(df: pd.DataFrame, keys: list[str], *,
                     examples: int = 5) -> dict

# app/refine/apply.py
def preview_or_apply(settings, source: str, target: str | None,
                     op_name: str, df, params: dict, ctx: dict,
                     runner) -> dict

# app/llm_providers.py
PROVIDERS = ("openai", "b-api-openai", "b-api-academiccloud")

def resolve_base_url(provider: str, explicit: str | None,
                     b_api_base_url: str) -> str
def auth_headers(provider: str, key: str) -> dict[str, str]
def capabilities(model: str) -> dict
```

### Data model

Kein Schemawechsel im Speicher. Zwei Ergänzungen in `config.yaml`:

```yaml
llm:
  b_api_base_url: https://b-api.prod.openeduhub.net
  seeds:
    provider: openai              # openai | b-api-openai | b-api-academiccloud
    model: gpt-5.6-luna
    verbosity: low
    reasoning_effort: low
    api_key_env: OPENAI_API_KEY
  bulk:
    provider: openai
    model: gpt-5.6-luna
    verbosity: low
    reasoning_effort: low
    api_key_env: OPENAI_API_KEY
```

`base_url` bleibt erlaubt und gewinnt, wenn gesetzt — sonst leitet der Provider
sie ab. Für die beiden b-api-Provider ist die Env-Variable `B_API_KEY`.

Die Ops-Historie bekommt Einträge derselben Form wie heute
(`op`, `params`, `source`, `before`, `after`, `removed`, `changed`), damit eine
gemischte Pipeline in einer Liste lesbar bleibt.

### Dependencies

**Keine neue.** `gzip`, `json`, `io` und `re` sind Standardbibliothek;
`pandas`, `pydantic`, `fastapi`, `openai` und `httpx` sind vorhanden. Das ist
die schriftliche Begründung, die `CLAUDE.md` verlangt: es gab nichts zu
begründen.

## Non-functional

- **Performance.** Jede Ganzrahmen-Operation läuft über `asyncio.to_thread`,
  wie `analyze` und `filter` es heute tun — sonst blockiert ein Join über
  400.000 Zeilen die Gesundheitsprüfung und das Fortschritts-Polling. Der
  Viewer liest **nur die angeforderte Seite** und zählt die Treffer, statt den
  ganzen Rahmen zu serialisieren.
- **Grenzen.** Upload weiterhin über `max_upload_mb` gedeckelt. Der Join
  bekommt eine harte Ausgabe-Obergrenze (Vorgabe 5.000.000 Zeilen) und bricht
  mit einer erklärenden 400 ab, statt den Speicher zu füllen: ein n-zu-m-Join
  über einen unsauberen Schlüssel multipliziert Zeilen.
- **Security.** Alle neuen Endpunkte hinter derselben Auth wie die
  bestehenden. Datensatznamen durch `safe_name`. Kein URL-Fetch: Import bleibt
  Datei-Upload. `regex` ist auf 200 Zeichen Musterlänge begrenzt — ein
  bösartiges Muster kann sonst katastrophal backtracken; das Risiko ist unten
  benannt und angenommen.
- **i18n.** Alle neuen Texte über `i18n.js`, deutsch und englisch, semantische
  Schlüssel wie im Bestand (etwa `tables.filter.operator.starts_with`). Keine
  fest verdrahteten Zeichenketten.
- **UI-Qualitätsboden.** Für jede neue Ansicht: Lade-, Leer-, Fehler- und
  Teilzustand; Tastaturbedienbarkeit; Kontrast nach WCAG 2.2 AA. Die
  Tabellenansicht scrollt in einem eigenen `overflow-x`-Behälter, damit die
  Seite selbst nie waagerecht scrollt.
- **Privacy.** Keine Drittanbieter-Ressourcen, alles selbst gehostet — wie
  heute. Die Viewer-Suche geht als Query-Parameter und wird deshalb **nicht**
  geloggt.
- **Observability.** Jede Operation protokolliert Vorher- und Nachher-Zahlen in
  die Ops-Historie; das ist die Spur, an der eine Pipeline nachvollziehbar
  bleibt.

## Risks

| Risiko | Wirkung | Gegenmaßnahme |
|---|---|---|
| n-zu-m-Join lässt Zeilen explodieren | Speicher voll, Prozess tot | Harte Obergrenze plus Vorab-Bericht zur Schlüsselkardinalität **vor** dem Ausführen |
| Alles-Strings-Speicher macht Zahlenvergleiche zweideutig | "9" wäre größer als "10" | Regeln casten mit `pd.to_numeric(errors="coerce")`, sobald der Regelwert numerisch ist; nicht castbare Zeilen matchen **nicht**, und die Statistik meldet, wie viele das waren |
| Flachklopfen erzeugt hunderte Spalten | UI unbrauchbar | Import meldet die Spaltenzahl; Spaltenauswahl ist eine eigene Operation direkt danach; der Viewer zeigt wählbare Spalten |
| Listen von Objekten sind nicht sinnvoll flach zu klopfen | Datenverlust | Als JSON-Text in der Zelle belassen und im Importbericht ausweisen — dokumentierte Grenze, kein stiller Verlust |
| b-api-Modell-IDs ändern sich ohne Ankündigung (belegt: `deepseek-v4-flash` wurde zu `deepseek-v4-flash-0731`, der alte Name antwortet seither mit 503) | Läufe brechen ab | Die Providerauswahl in der UI listet `/models` live; die Fehlermeldung nennt die 503-Ursache |
| `re` kann katastrophal backtracken | Ein Kern brennt | Musterlänge auf 200 Zeichen begrenzt, Operation im Thread, hinter Auth. Angenommen. |
| `refine.py` und `llm.py` sind schon über der Zeilengrenze | Weiteres Wachstum verschlimmert es | Dieser Plan verkleinert beide; das ist Teil der Aufgaben, nicht Beiwerk |

## Open questions

Keine. Die vier offenen Punkte — JSON-Tiefe, Filterform, Stufung, b-api — sind
am 2026-09-10 entschieden.

## Task list

Jede Stufe beginnt mit **Schritt 0: `/better-coding-workflow` aufrufen**
(zusätzlich `/better-coding-frontend`, wo UI berührt wird). Skills entladen
sich — nicht darauf verlassen, dass sie noch im Kontext sind.

### Stufe A — Fundament (8 Aufgaben)

Ziel: beliebige Tabelle rein, filtern, Spalten wegwerfen, profilieren, wieder
raus.

- **A0** Schritt 0: `/better-coding-workflow`
- **A1** `app/tabular.py`: `flatten_record` und `sniff_format`. Test zuerst:
  verschachteltes WLO-Objekt wird zu `properties.cclom:title`; Liste von
  Skalaren wird verbunden; Liste von Objekten bleibt JSON-Text.
- **A2** `app/tabular.py`: `read_table` für CSV, CSV.gz, JSON, JSONL und
  JSONL.gz, mit wählbarem Trennzeichen und Encoding. Test: dieselben Daten in
  fünf Formaten ergeben denselben Rahmen.
- **A3** `app/tabular.py`: `write_table`. Test: `read_table(write_table(df))`
  ist verlustfrei.
- **A4** `store.read_csv` auf `tabular.read_table` umstellen; der
  Import-Endpunkt bekommt `format`, `separator`, `encoding` und `flatten` als
  Formularfelder. Test: der Semikolon-Import verhält sich unverändert
  (Regression), Komma-CSV funktioniert jetzt zusätzlich.
- **A5** `app/refine/rules.py`: `evaluate` mit allen fünfzehn Operatoren und
  UND/ODER. Test zuerst, je Operator ein Fall plus der
  Zahlen-als-String-Fall.
- **A6** `app/refine/columns.py`: behalten, entfernen, umbenennen.
- **A7** `app/refine/profile.py`: `profile_columns`.
- **A8** `app/refine/apply.py` und `app/routes/tables.py`: Import, Download,
  generischer Operationen-Endpunkt, Profil. `refine.py` benutzt `apply.py` mit
  und wird dabei kürzer.

### Stufe B — Zusammenführen und ansehen (6 Aufgaben)

- **B0** Schritt 0: `/better-coding-workflow`
- **B1** `app/refine/profile.py`: `duplicate_report` über mehrere Schlüssel.
- **B2** `filters.py`: `dedupe_keys` in der Dedupe-Familie.
- **B3** `app/refine/join.py`: `key_cardinality` — der Vorab-Bericht.
- **B4** `app/refine/join.py`: `join_datasets` mit vier Join-Arten,
  Kollisionsauflösung und Obergrenze.
- **B5** Join-Endpunkt in `routes/tables.py`.
- **B6** Zeilen-Viewer: `GET /tables/{name}/rows` mit Seitenblättern,
  Spaltenauswahl und Suche — nach dem Muster von `review.py:34`.

### Stufe C — LLM-Provider und UI (8 Aufgaben)

- **C0** Schritt 0: `/better-coding-workflow` **und**
  `/better-coding-frontend`
- **C1** `app/llm_providers.py`: `resolve_base_url`, `auth_headers` und
  `capabilities`, aus `llm.py` herausgelöst und verhaltensgleich. Test: ein
  b-api-Provider erzeugt `X-API-KEY`, OpenAI erzeugt keinen.
- **C2** `config.py`: `provider`, `verbosity`, `reasoning_effort`,
  `b_api_base_url`; Vorgabemodell `gpt-5.6-luna`, beides `low`.
- **C3** `llm.py`: `_get_client` benutzt `auth_headers`; `complete` reicht
  `verbosity` und `reasoning_effort` für die gpt-5-Familie durch. Test über
  den vorhandenen `httpx.MockTransport`-Seam — kein Netz.
- **C4** UI: Providerauswahl in der LLM-Leiste, mit Live-Liste aus `/models`.
- **C5** `style.css`: `--page-max: 1200px`, Tabellenbehälter mit eigenem
  waagerechten Scrollen, Feldoptimierungen wie in api_v3.
- **C6** `tables.js` und `i18n.js`: der Tabellen-Tab mit Import, Viewer,
  Regelbau, Spalten, Join und Profil, samt Lade-, Leer- und Fehlerzustand.
- **C7** `docs/ui-guide.md` und `README.md` nachziehen; `TODO.md`
  fortschreiben.

## Abweichungen in der Umsetzung (Stufe A, 2026-09-10)

Der Plan ist die Quelle der Wahrheit, also stehen die Abweichungen hier statt
nur in den Commits:

- **`flatten` als Schalter gestrichen.** Verschachtelung wird immer flach
  geklopft. Gegen eine sehr breite Tabelle steht die Spaltenauswahl, die es nun
  gibt — ein zweiter Notausgang waere spekulativ gewesen (YAGNI).
- **`read_table` bekam `filename`.** Ohne den Dateinamen kann `fmt="auto"` die
  Endung nicht lesen.
- **`preview_or_apply` ohne `runner`-Rueckruf.** Der Aufrufer fuehrt seine
  Operation selbst aus und uebergibt das Ergebnis; die Dispatch-Registry
  `TABLE_OPS` liegt daneben im selben Modul. Einfacher als der geplante
  Rueckruf, und die beiden Routen brauchen ohnehin unterschiedliche Kontexte.
- **Das Profil ist ein `GET`, kein `POST`.** Es liest nur und braucht keinen
  Rumpf.
- **Der Import zog schon in A4 nach `routes/tables.py` um, nicht erst in A8.**
  `routes/refine.py` lag mit 332 Zeilen bereits ueber der Grenze; ihn fuer die
  Formatoptionen weiter wachsen zu lassen war keine Option.
- **Zusaetzlich behoben, vom Plan nicht erfasst:** `store.load_dataset` las den
  Speicher ohne `keep_default_na=False` zurueck. Ein als `""` gespeichertes Feld
  kam als `NaN` wieder, und der Text `"NA"` wurde ein fehlender Wert. Ohne diese
  Korrektur haette der neue `is_empty`-Operator je nach Zeitpunkt etwas anderes
  bedeutet.

### Stufe B (2026-09-10)

- **B1 und B2 liegen in `refine/duplicates.py`**, nicht in `profile.py` und
  `filters.py`. Bericht und Entfernen teilen die Schluesselbildung und sind eine
  Verantwortung; `filters.py` ist ausserdem die label-zentrierte Schicht.
- **`refine/keys.py` kam hinzu** (nicht im Plan). Dubletten und Join muessen sich
  darueber einig sein, was ein Schluessel IST — sonst widerspricht der
  Dublettenbericht dem Join, der ihm folgt.
- **`refine/view.py` kam hinzu** (im Plan lag der Viewer in der Route). Suchen
  und Blaettern ist Logik, und Routen bleiben laut Konstitution duenn.
- **Die Join-Vorschau liefert die Kardinalitaet, kein materialisiertes
  Ergebnis.** Fuer einen Join ist das die ehrliche Vorschau: die Groesse ist die
  gefaehrliche Groesse, und sie zu berechnen kostet nichts.
- **Unpassende Zeilen werden unabhaengig von der Join-Art gezaehlt**, damit ein
  Left-Join die verworfenen Rechts-Zeilen nicht verschweigt.

## Verification plan

| Anforderung | Wie geprüft | Erfolg sieht so aus |
|---|---|---|
| Fünf Eingabeformate | `pytest tests/test_tabular.py` | dieselben Daten in fünf Formaten ergeben einen identischen Rahmen |
| Runde verlustfrei | `test_tabular.py::test_roundtrip` | `read(write(df))` gleicht `df` |
| Regelfilter | `pytest tests/test_rules.py` | je Operator ein Fall grün, inklusive "9" gegen 10 |
| Spaltenoperationen | `pytest tests/test_columns.py` | unbekannte Spalte ergibt 400, nicht 500 |
| Join | `pytest tests/test_join.py` | vier Arten; n-zu-m meldet die Explosion **vor** der Ausführung |
| Dubletten über Schlüssel | `pytest tests/test_profile.py` | Bericht nennt Gruppen und Beispiele |
| Viewer und Suche | `pytest tests/test_tables_routes.py` | Seitenblättern stabil, Suche trifft, Gesamtzahl stimmt |
| Spaltenprofil | `pytest tests/test_profile.py` | Füllgrad, Kardinalität, Top-Werte, Zahlen-Kennzahlen |
| b-api-Auth | `pytest tests/test_llm_providers.py` | b-api erzeugt `X-API-KEY`, OpenAI keinen; über MockTransport |
| gpt-5.6-luna | `tests/test_llm.py` | `max_completion_tokens`, keine `temperature`, `verbosity` und `reasoning_effort` gesetzt |
| UI-Breite | Browser-Prüfung gegen api_v3 | 1200px, Tabelle scrollt im eigenen Behälter, die Seite nie waagerecht |
| Keine Regression | `pytest tests -q` | **die 243 vorhandenen Tests bleiben grün** |
| Konstitution | `ruff check app tests`, `mypy app --config-file pyproject.toml` | sauber; keine Datei über 300 Zeilen |

**Regressionsrisiko.** Der einzige Eingriff in bestehendes Verhalten ist A4,
die Umstellung von `store.read_csv` auf `tabular.read_table`. Alles andere
kommt hinzu. Deshalb hat A4 einen ausdrücklichen Regressionstest, der den
heutigen Semikolon-Import festnagelt, **bevor** die Umstellung passiert.
