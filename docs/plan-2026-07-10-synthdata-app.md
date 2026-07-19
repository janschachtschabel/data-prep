# Design: data-prep — Datenaufbereitungs-App (synthetische & veredelte Datensätze)

Status: FREIGEGEBEN, in Umsetzung · Datum: 2026-07-10 · Autor: Claude + Jan
Rev. 2: Vocab-only-Modus (Referenzen optional), erweiterter Refine-Werkzeugkasten,
PII als eigenständiges Werkzeug, Trainingsvorbereitung für api_v3.
Rev. 2.1 (2026-07-10): App-Ordner heißt `data-prep/` (Arbeitstitel war `synthdata`),
env-Prefix `DATAPREP_`. Original dieses Plans: `../../synthdata/docs/plan-2026-07-10-synthdata-app.md`.

## Goal

Eine eigenständige zweite App (eigener Ordner `data-prep/`, eigene UI), die
**veröffentlichbare, vollsynthetische Datensätze** erzeugt (PII-frei,
urheber-neutral, vokabular-ausgewogen) — wahlweise **rein aus SkoHub-Vokabular
+ KI** oder angereichert durch optionale Bestandsdaten — und daneben bestehende
Datensätze **analysiert, filtert, kombiniert, veredelt und für das Training in
api_v3 vorbereitet**.

## Context

api_v3 (Klassifikations-API) bleibt unberührt und wird Konsument der erzeugten
CSVs. Die Kern-Pipeline folgt dem Dokument „Pipeline für synthetische
Bildungsmetadaten V2" (8 Stufen, Seed-Pool-Destillation); dieses Design ergänzt
App-Schale, UI, Betrieb, den referenzlosen Modus und den Werkzeugkasten.
Session-Vorarbeiten fließen ein: Generator-Experiment (Prompt-Rezept),
Holdout-Methodik (`eval_holdout`), Label-Inkonsistenz-Befund (Elektrotechnik),
UI-Muster aus api_v3.

## Betriebsmodi der Generierung (neu in Rev. 2)

| Modus | Eingaben | Seed-Quelle | Leakage-Filter |
|---|---|---|---|
| **vocab-only** | nur SkoHub-Vokabulare | Seeds komplett per LLM aus prefLabel/altLabel + Hierarchie-Kontext (geankert am Oberkonzept) | entfällt (nichts zu leaken); interner Dedupe bleibt |
| **hybrid** | Vokabulare + optionale Referenzsets | destillierte Seeds aus Referenzen (Stufe 2+3) + LLM-Ergänzung; Fächer ohne Bestand wie vocab-only | aktiv gegen alle Referenzen |

**Umfang frei wählbar:** Ziel-N pro Concept (z. B. 50/200/500), Concept-Auswahl
(alle · Teilbaum der Hierarchie · explizite Liste), Längenprofil
(Standard 300–600 Zeichen · Volltext 3.000–6.000 · frei Min/Max), Bildungsstufen-
und Inhaltstyp-Vokabulare zuschaltbar. Referenzsets sind ein **optionales**
Formularfeld — leer = vocab-only.

## Scope

**In scope (v1):**
- 8-Stufen-Pipeline nach Vorlage, mit Stufen 2/3/6 als optionalem Referenzpfad
  (s. Betriebsmodi): Vokabulare → [Referenzen+PII-Scrub → Seed-Destillation] →
  Plan → async Generierung → [Leakage-Filter] → interner Dedupe → Gates →
  Export+Audit.
- Volle UI: Login, Vokabular-/Referenzverwaltung, Run-Cockpit (Modus, Umfang,
  Start, Live-Fortschritt je Stufe, Abbruch, Resume), **Review** (freigeben/
  verwerfen, Nachgenerieren), **Seed-Editor**, Audit-Report, Downloads.
- **Refine-Werkzeugkasten** (Details unten): Analyse, Filterung, Kombination,
  PII-Scan/-Scrub, Label-Audit via api_v3, Trainingsvorbereitung.
- Export: JSONL (kanonisch) + **CSV im api_v3-Schema**; optional direkter
  Upload zu api_v3 (`/datasets/import`, Ziel-URL+Key in config).

**Out of scope (v1, bewusst):**
- Parquet (pyarrow; v2), spaCy-NER (v1: Regex + LLM-PII-Gate; Upgradepfad
  dokumentiert), LLM-Umschreiben bestehender kuratierter Texte (v1 nur
  ADDITIVE Veredelung: fehlende Felder ergänzen, markiert), Multi-User/Rollen.

## Refine-Werkzeugkasten (Rev. 2, Use-Cases 2+3 ausgearbeitet)

Alle Operationen: Vorschau (n vorher/nachher + Beispiel-Diffs) → Anwenden →
neue Datei mit Operations-Protokoll im Header-Kommentar; Original bleibt
unangetastet.

**Analyse (nicht-destruktiv):**
- Label-Verteilung (Support je Concept, Lorenz/Ungleichheit), Textlängen,
  Leer-/Fehlfelder, exakte + semantische Dublettencluster (Embedding),
  Vokabular-Konsistenz (ungültige URIs, DISPLAYNAME-Drift), PII-Scan (aggregierter
  Report, nie Klartext).
- **Trainings-Preflight:** simuliert die api_v3-Aufbereitung (clean_text,
  Mindestlänge, Dedupe, min_samples) und zeigt die *effektive* Trainingsmenge
  je Label vorab — beantwortet „warum 25.068 statt 32.728" vor dem Training.

**Filterung:**
- Dedupe (exakt / semantisch mit Schwelle), Längenkorridor, Zeilen ohne Label,
  Label-Substring-Filter (wie api_v3 `label_filter`), Markup-Bereinigung,
  PII-Drop **oder** PII-Maskierung (wählbar), Cap pro Label (Balancierung).

**Kombination:**
- Mehrere CSVs → Zielschema mit **Spalten-Mapping-Assistent** (Quellspalte →
  api_v3-Spalte, UI-gestützt, Mapping speicherbar), Herkunfts-Spalte je Quelle,
  Konfliktstrategie bei Textdubletten (Priorität nach Quelle, z. B. kuratiert
  schlägt gemint schlägt synthetisch), optionale Balancierung beim Merge.

**Veredelung (additiv, markiert):**
- Keyword-Ergänzung per LLM für Zeilen mit <K Schlagwörtern; Beschreibungs-
  Ergänzung für leere Beschreibungen (aus Titel+Keywords; `enriched_fields`-Spalte
  dokumentiert jede Ergänzung).
- **Label-Audit via api_v3** (direkte Umsetzung des Elektrotechnik-Befunds):
  Datensatz gegen ein trainiertes api_v3-Modell klassifizieren; Zeilen, bei denen
  Vorhersage und Goldlabel stark divergieren (Confidence-Schwellen), landen in
  einer **Prüfliste für die Redaktion** (CSV-Export mit Modell-Zweitmeinung).
  Kein Auto-Umlabeln — Mensch entscheidet.

**Trainingsvorbereitung (api_v3-Brücke):**
- Schema-Mapping → Semikolon-CSV/UTF-8; **stratifizierter, textdisjunkter
  Holdout-Split** (Methodik aus `enrich_tail.py` generalisiert) für ehrliche
  Evaluation; Balance-Report mit min_samples-Empfehlung; optional Push zu
  api_v3 + Trainings-Startlink.

## Approach (Alternativen geprüft)

| Ansatz | Bewertung |
|---|---|
| **A: Eigene FastAPI-App, api_v3-Muster wiederverwendet** | **Gewählt.** Muster erprobt (Settings/Auth/Jobs/UI), kein Framework-Zoo. |
| B: Pipeline in api_v3 integrieren | Verworfen: vermischt Verantwortungen, sprengt Dep-Disziplin (openai), anderes Sicherheitsprofil (URL-Fetch). |
| C: Reines CLI + statischer Report | Verworfen: UI inkl. Review ist ausdrücklich v1-Anforderung. |

## Global constraints

- Code/Kommentare/Doku Englisch; UI-Hilfen einfach; Nutzer-Leitfaden Deutsch.
- Dateien < ~300 Zeilen, Split nach Verantwortung; test-first bei Logik.
- Kein Pickle; Secrets nur via Env; Fehler nie verschluckt.
- **Bewusste Abweichung von api_v3:** HTTPS-URL-Fetch erlaubt (Vokabulare,
  api_v3-Push) — Allowlist-Default `vocabs.openeduhub.de` + eigene api_v3-URL,
  5-MB-Cap, Timeout, nur hinter Auth.

## Architecture

### Ordnerstruktur

```
data-prep/
  app/
    __init__.py         # BLAS-Threads=1 vor numpy (model2vec)
    main.py             # App-Factory, /ui-Mount, Security-Header, no-cache für UI
    settings.py         # env DATAPREP_: Pfade, Auth-Key, LLM-Defaults, Limits, api_v3-Ziel
    security.py         # Ein-Key-Auth (konstantzeitig), safe_name
    llm.py              # OpenAI-Wrapper: {base_url, model, api_key_env} je Zweck
                        #   (seeds=gpt-5.4-mini, bulk=gpt-5.4-nano, beides wählbar),
                        #   GPT-5-Parameterkompatibilität (max_completion_tokens,
                        #   Temperatur nur wenn unterstützt, Capability-Map),
                        #   Structured Outputs via Pydantic-JSON-Schema,
                        #   Retry/Backoff, Usage-/Kosten-Logging
    embeddings.py       # model2vec-Wrapper (Leakage-Filter, semantischer Dedupe)
    pii.py              # eine Engine für alle PII-Anwendungen: Regex-Scrub/Scan
                        #   (E-Mail, Telefon, personenbezogene URLs/Handles),
                        #   Maskieren vs. Verwerfen, aggregierte Reports;
                        #   LLM-Stichproben-Gate; spaCy-Upgradepunkt
    vocab.py            # Stufe 1: SKOS/JSON-LD (Datei ODER https-URL + Guards),
                        #   vocab_index, Hierarchie, Teilbaum-Auswahl
    reference.py        # Stufe 2 (OPTIONAL): Laden, Gruppierung, Eingangs-Scrub
    seeds.py            # Stufe 3: Destillation (exakt+semantisch dedupt, LLM-
                        #   Ergänzung) ODER vocab-only-Bootstrap aus Labels+Hierarchie
    planning.py         # Stufe 4: Ist-Stand, Lücken, Concept-Auswahl, Plan
    generation.py       # Stufe 5: asyncio+Semaphore(20), Prompt-Builder,
                        #   Längenprofile, JSONL-Append, Budget-Gate
    filtering.py        # Stufe 6: Leakage-Filter (nur hybrid) + interner Dedupe
    validation.py       # Stufe 7: Schema-/PII-/Vokabular-Gates (verwerfen+zählen)
    exporter.py         # Stufe 8: JSONL + api_v3-CSV, Audit-Markdown, api_v3-Push
    runs.py             # Run-Store (runs/<id>/state.json), Hintergrund-Task,
                        #   stufenweiser Fortschritt, Abbruch + Resume
    refine/             # Werkzeugkasten (eigenes Paket, Dateien je Verantwortung)
      analyze.py        #   Verteilung, Dubletten, Konsistenz, PII-Scan, Preflight
      filters.py        #   Dedupe/Längen/Label/Markup/PII/Cap
      combine.py        #   Mapping-Assistent, Merge, Konfliktstrategie
      enrich.py         #   Keyword-/Beschreibungs-Ergänzung (LLM, markiert)
      label_audit.py    #   Zweitmeinung via api_v3-Predict, Divergenz-Prüfliste
      prep.py           #   Holdout-Split, Balance-Report, Export/Push
    routes/             # system, vocab, reference, seeds, runs, review, refine
    static/ui/          # Tabs: Runs · Review · Seeds · Vocabularies ·
                        #   References · Refine  (Muster/Dateien aus api_v3:
                        #   Login-Zeile, pills.js, Statuspolling, Hilfe-Accordions)
  tests/                # pytest; LLM gemockt, 1 markierter Live-Smoke
  data/  runs/  config.yaml  requirements.txt/.lock  README.md  docs/
```

### Datenfluss mit optionalem Referenzpfad

```
                         ┌──(optional) data/referenz.csv → PII-Scrub → Destillation ┐
SkoHub-Vokabulare ───────┤                                     └→ Leakage-Index     │
   └→ vocab_index → Seeds (aus Referenzen ODER vocab-only-Bootstrap)                │
        → Plan (Umfang/Auswahl) → Generierung → [Leakage nur hybrid] → Dedupe/Gates
        → runs/<id>/samples.jsonl → Review → Export (JSONL/CSV/Push) + Audit
```
Trennungsgarantie unverändert: `exporter` liest nur `samples.jsonl`
(Status `passed|approved`) — Referenzzeilen erreichen den Export strukturell
nie; ein Test pinnt das.

### Schemata (fixiert)

Sample (JSONL, kanonisch): wie Rev. 1 (`id`, `title`, `description`,
`keywords[]`, `discipline`, `educational_context`, `lrt`, `length_profile`,
`synthetic: true`, `status`, `meta{model, seed_sample, sim_max, tokens}`).
CSV-Export mappt auf api_v3-Spalten inkl. `_DISPLAYNAME` und `source=synthetic`.
Refine-Ausgaben führen `source`- und (bei Veredelung) `enriched_fields`-Spalten.

### Dependencies (jede begründet)

| Paket | Warum |
|---|---|
| fastapi, uvicorn, pydantic, pydantic-settings, python-multipart, pyyaml | App-Schale wie api_v3 |
| **openai** | Async-Client + Structured Outputs (bringt httpx mit → Vokabular-Fetch + api_v3-Push ohne Extra-Paket) |
| **model2vec** + numpy | Embeddings für Leakage/Dedupe (CPU, torch-frei; Modell aus config, Default `JanSchachtschabel/m2v-gte-…`) |
| pandas | CSV/JSONL, Refine-Operationen |
| — kein spaCy/pyarrow/slowapi in v1 | dokumentierte v2-Kandidaten |

## Non-functional

- **Security:** Ein-Key-Auth auf allem außer `/health` + statischer UI;
  URL-Fetch-Guards (HTTPS, Allowlist, 5 MB, Timeout); Upload-Limits;
  Security-Header; PII nie im Klartext in Logs/Reports.
- **Kostenkontrolle:** Dry-Run (Plan + Kostenschätzung ohne API-Call), hartes
  Budget je Lauf (`max_llm_calls`, `max_tokens_total`) → pausiert statt
  weiterzubrennen; Ist-Kosten im Audit.
- **Robustheit:** JSONL-Append + state.json → Resume nach Absturz; Fehler je
  Task geloggt + begrenzt requeued.
- **Beobachtbarkeit:** echte Zähler je Stufe (kein eingefrorenes Prozent),
  Verwerfungsgründe live, Diversitäts-Kennzahl (mittlere paarweise Similarity).

## Risks

| Risiko | Gegenmaßnahme |
|---|---|
| Distribution-Shift bei reiner Synthetik (Vorlage §6; im vocab-only-Modus maximal) | Audit-Report erzwingt Warnhinweis + Nutzungsregeln (Testsplit nur echt, Mischtraining); `eval_holdout`-Methodik verlinkt |
| Prototyp-Kollaps | Seed-Rotation, „vermeide diese Titel"-Kontext, semantischer Dedupe, Diversitäts-Kennzahl im Report |
| LLM-Kosten | Budget-Gates, Dry-Run, nano-Default |
| PII rutscht durch Regex | LLM-Stichproben-Gate (konfigurierbarer Anteil), Report macht Promptversagen sichtbar, spaCy-Pfad |
| `gpt-5.4-*`-Parameterdrift / fremde base_urls | Capability-Map + Live-Smoke (skip ohne Key); alle Parameter konfigurierbar |
| Vokabular-URL down | Datei-Fallback + Cache des letzten Stands |
| Falsches Auto-Umlabeln beim Label-Audit | bewusst NUR Prüfliste, nie automatische Änderung |

## Meilensteine (abhängigkeitsgeordnet; Schritt 0 je Paket: /better-coding-workflow, UI-Pakete + /better-coding-frontend)

- **M1 Gerüst:** settings/security/main/health, UI-Shell+Login, config.yaml, Test-Grundgerüst.
- **M2 Vokabulare:** SKOS-Parser (Datei+URL+Guards, Fixture: echtes
  educationalContext-JSON-LD), Hierarchie/Teilbaum, Route+Tab.
- **M3 PII-Engine + Referenzen (optional-Pfad):** `pii.py` (test-first:
  E-Mail/Telefon/URL/Handle-Fälle, Maskieren vs. Droppen, Idempotenz),
  Referenz-Upload/Gruppierung mit Eingangs-Scrub.
- **M4 LLM-Schicht:** Wrapper, Capability-Map, Structured Outputs (Pydantic),
  Budget-Zähler; gemockte Tests + Live-Smoke.
- **M5 Seeds:** Destillation (hybrid) **und vocab-only-Bootstrap**; Seed-Store;
  Seed-Editor (API+UI). Test: Fach ohne Bestand → Hierarchie-Anker.
- **M6 Plan & Generierung:** Umfang/Concept-Auswahl, async Worker,
  Längenprofile, Run-Store, Abbruch/Resume, Budget-Pause. Test: Resume,
  Längenkorridor, Plan-Determinismus.
- **M7 Filter & Gates:** Leakage (nur hybrid), interner Dedupe, 3 Gates;
  **Trennungsgarantie-Test**.
- **M8 Export & Audit:** JSONL/CSV, Audit-Markdown, Downloads, api_v3-Push,
  Report-Ansicht.
- **M9 Review-UI:** Browser (Filter Fach/Status/Similarity), Approve/Discard
  (Tastatur), Nachgenerieren.
- **M10 Refine-Werkzeugkasten:** analyze (inkl. Preflight) → filters → combine
  (Mapping-Assistent) → prep (Holdout-Split, Balance) → label_audit → enrich.
  Jede Op mit Vorschau + Protokoll; UI-Tab.
- **M11 Härtung & Doku:** Limits, .env.example, README, docs/ui-guide.md (DE),
  CHANGELOG, Abnahmelauf.

## Verifikation (Abnahmekriterien v1)

1. Modultests grün (LLM gemockt), ruff/mypy sauber; Trennungsgarantie-Test.
2. **End-to-End vocab-only:** 3 Vokabulare per URL, Lauf N=5 für einen
   Hierarchie-Teilbaum ohne jede Referenzdatei → Audit + CSV, in api_v3
   smoke-trainierbar.
3. **End-to-End hybrid:** Referenz-CSV mit präparierter PII (E-Mail/Name) →
   erscheint weder in Seeds noch Output; Leakage-Filter verwirft eingeschleuste
   Fast-Kopie; Report zählt beides.
4. Review-Flow: verwerfen → nachgenerieren → Export enthält nur passed/approved.
5. Refine: Preflight-Zahlen stimmen mit api_v3-Trainingslog überein (±0);
   Kombination zweier Sets mit Mapping + Dedupe korrekt; Label-Audit erzeugt
   Prüfliste mit Divergenz-Fällen auf einem präparierten Set.

## Open questions

Keine blockierenden. Während M2 zu fixieren: exakte JSON-LD-Feldnamen der drei
SkoHub-Vokabulare (aus der Beispiel-URL ableitbar); während M10: bevorzugte
Divergenz-Schwellen für den Label-Audit (Default: Gold nicht in Top-3 UND
Top-Vorhersage ≥ 0.8).
