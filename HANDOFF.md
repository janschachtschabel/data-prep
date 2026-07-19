# data-prep — Handoff-Bericht (aktualisiert 2026-07-16)

Kompakter Überblick für den Wiedereinstieg. Ausführliches Protokoll: [TODO.md](TODO.md).
Konventionen & Coding-Workflow: [CLAUDE.md](CLAUDE.md). Design/Plan (Quelle der Wahrheit):
[docs/plan-2026-07-10-synthdata-app.md](docs/plan-2026-07-10-synthdata-app.md).

## Was die App ist

Zweite App neben der Klassifikations-API (api_v3). Sie **erzeugt veröffentlichbare,
vollsynthetische Datensätze** aus SkoHub-Vokabularen + LLM (vocab-only oder hybrid mit
optionalen Referenzen) und **analysiert / filtert / kombiniert / veredelt / bereitet**
bestehende Datensätze für das Training in api_v3 auf. api_v3 bleibt unberührt und
konsumiert die exportierten CSVs.

## Status

**v1 (M1–M11) + v0.2.0 + Whole-Codebase-Audit abgearbeitet + Deployment-Gerüst.**
235 Tests grün, ruff/mypy sauber (42 Module), 8 Router mit 41 API-Endpunkten (via
`/openapi.json`). Jeder Meilenstein wurde test-first gebaut und live gegen echte
Systeme verifiziert (Vokabular-Server, model2vec-Embedding, gpt-5.4-nano/-mini,
api_v3-Modell faecher_ai_cv5). Der Audit (`docs/audits/2026-07-12-audit.md`) ist
geschlossen — siehe Nachtrag 2026-07-16 + `CHANGELOG.md`.

## Meilenstein-Karte (wo was liegt)

| Baustein | Kernmodul(e) | Kurz |
|---|---|---|
| M1 Gerüst | `app/{settings,config,security,main}.py`, `routes/system.py` | FastAPI, Ein-Key-Auth, config.yaml, UI-Shell |
| M2 Vokabulare | `app/vocab.py`, `app/fetch.py`, `routes/vocabs.py` | SKOS-Parser, guarded HTTPS-Fetch, Metadatenfeld je Vokabular |
| M3 PII + Referenzen | `app/pii.py`, `app/reference.py`, `routes/references.py` | PII-Engine (scan/scrub/apply), Referenz-Import mit Eingangs-Scrub |
| M4 LLM | `app/llm.py` | AsyncOpenAI, gpt-5-Kompatibilität, Structured Outputs, Budget-Gate |
| M5 Seeds | `app/seeds.py`, `routes/seeds.py` | Destillation (hybrid) + vocab-only-Bootstrap, Seed-Editor |
| M6 Generierung | `app/{planning,generation,runs}.py`, `routes/runs.py` | Plan/Dry-Run, async Worker, Längenprofile, Abbruch/Resume, Budget-Pause |
| M7 Gates | `app/embeddings.py`, `app/filtering.py` | Leakage-Filter (hybrid) + semantisches Dedupe |
| M8 Export | `app/exporter.py`, `app/apiv3.py`, `routes/exports.py` | api_v3-CSV + JSONL + Audit-Warnblock, guarded api_v3-Push |
| M9 Review | `app/review.py`, `routes/review.py` | Sample-Browser, Approve/Discard (Tastatur a/d), Nachgenerieren |
| M10 Refine | `app/refine/{analyze,filters,combine,prep,label_audit,enrich}.py`, `routes/refine.py` | Analyse+Preflight, 8 Filter, Combine, Holdout-Split, Label-Audit, Enrich |
| M11 Doku | `README.md`, `docs/ui-guide.md` (DE), `.env.example`, `CHANGELOG.md`, `requirements.lock` | + Abnahmetests `tests/test_acceptance.py`, Live-Skript `scripts/acceptance_live.py` |

UI (statisch, kein Build): `app/static/ui/` — `index.html`, `style.css`, `api.js`,
`app.js`, `i18n.js`, plus je Tab `vocab.js`/`references.js`/`seeds.js`/`runs.js`/
`review.js`/`refine.js`. `app/textnorm.py` = **byte-genaue Kopie** von api_v3s
`clean_text`/`split_labels` (Paritätstest).

## Betrieb & Befehle (aus `data-prep/`, venv `.venv`)

```
# Tests / Lint / Typen
.venv\Scripts\python -m pytest tests -q                       # 235 grün (LLM/Embedding/api_v3 gemockt)
.venv\Scripts\python -m pytest tests -m live                  # 1 markierter echter LLM-Smoke (braucht OPENAI_API_KEY)
.venv\Scripts\python -m ruff check app tests
.venv\Scripts\python -m mypy app --config-file pyproject.toml

# Server lokal (langlebig — Preview-MCP-Server sind kurzlebig!)
DATAPREP_AUTH_KEY=dev-key DATAPREP_APIV3_KEY=ui-admin-key .venv/Scripts/python.exe -m app.main
```

**Läuft aktuell:** data-prep auf **http://127.0.0.1:8110/ui** (Login-Key `dev-key`),
api_v3 auf **:8021** (Modell `faecher_ai_cv5`, Admin-Key `ui-admin-key`). Wichtig:
Statische UI-Dateien werden pro Request frisch von der Platte geladen (Hot-Reload);
**Python-Routencode-Änderungen brauchen Serverneustart**.

## Sicherheit / wichtige Invarianten

- Ein-Key-Auth (konstantzeitig) auf allem außer `/health` + statischer UI. Secrets nur aus Env; `config.yaml` nennt nur Env-Variablen-**Namen**, nie Keys.
- **Keyless fail-closed (Audit T4):** ohne konfigurierten Key ist Auth nur für **Loopback** deaktiviert — eine Nicht-Loopback-Anfrage wird mit 403 abgewiesen (hinter Reverse-Proxy ist der Peer der Proxy → Key Pflicht).
- **Hardening-Header:** `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy` + strikte **CSP** (`default-src 'self' …`) auf jeder Nicht-Doc-Antwort (Swagger/ReDoc ausgenommen).
- **Trennungsgarantie:** der Run-Exporter liest nur akzeptierte synthetische Samples — Referenzzeilen erreichen den Export strukturell nie (AST- + Verhaltenstest).
- **PII-Engine** (`app/pii.py`): Regex-Scrub für E-Mail/URL/Telefon/Handle, mask vs. drop, idempotent, Reports nur aggregiert (nie Klartext). Referenz-Import scrubbt **im RAM vor dem Schreiben**. Namen NICHT erkannt (v1; spaCy-Upgradepfad dokumentiert).
- **Bewusste Abweichung von api_v3:** HTTPS-Fetch erlaubt — nur Allowlist (`vocabs.openeduhub.de` + api_v3-Host/localhost), 5-MB-Cap, Timeout, hinter Auth.
- **Preflight-Parität ±0:** `refine/analyze.training_preflight` reproduziert api_v3s effektive Trainingsmenge exakt (25.068 auf data_30k.csv) — hängt an der textnorm-Parität.
- **Ehrliche Evaluation:** synthetische/gemischte Daten sind nur zum Training; bewerten nur auf kuratiertem, textdisjunktem Holdout (der Prepare-Split erzeugt einen). Audit-Report trägt den Pflicht-Warnhinweis.

## config.yaml (nicht-geheime Betriebs-Config)

- `llm.seeds` = gpt-5.4-mini, `llm.bulk` = gpt-5.4-nano (base_url/model/api_key_env wählbar).
- `budgets` (max_llm_calls / max_tokens_total per Run; **max_cross_request_calls / max_cross_request_tokens** = opt-in prozessweite Ceiling, Default 0 = aus, Audit T8).
- `embeddings.model` = JanSchachtschabel/m2v-gte-256-int8-edu, Schwellen leakage 0.90 / dedupe 0.95.
- `api_v3.url` = http://127.0.0.1:8021 (Push/Label-Audit; Key via `DATAPREP_APIV3_KEY`).
- `references.defaults` = **curated-30k → ../api_v3/data/data_30k.csv** (wird beim Start PII-gescrubbt in den Referenz-Store importiert; 32.728 Zeilen, PII in 2.304 maskiert).

## Nachtrag (2026-07-11) — UX- & i18n-Runde (v0.2.0)

Fünf Verbesserungspakete auf v1 + Feedback-Runde + **Begriffs-Bank** (Term Banks: Hybrid-Referenzdaten je Konzept gemint → LLM bereinigt/erweitert → Generierung nutzt rotierende Stichprobe; `app/terms.py`, `POST /seeds/{name}/terms`), test-first + live/deterministisch verifiziert (206 Tests grün, ruff/mypy sauber; Few-Shot-Anzahl und Term-Bank-Umfang [Zeilen/Label + max. Begriffe] getrennt steuerbar):

1. **Vokabular manuell + Turtle** — `app/vocab_formats.py` (tokenizer-basierter SkoHub-TTL-Reader ohne neue Dependency + Paste-Liste), Routen `POST /vocabs/manual` und `.ttl`-Zweig in `POST /vocabs/import`; beide erzeugen dieselbe JSON-LD, die `parse_vocabulary` validiert. Live geprüft (manuell + .ttl mit Hierarchie).
2. **Generierungs-Kontext** — `RunRequest.guidance` fließt in den Generierungs-Prompt und in die Run-`params` (resume-fest). E2E-Test pinnt: Guidance landet in params UND im echten LLM-Prompt.
3. **Feld-Beispiele** — Schulfächer-vs-Hochschulfächer-Klärung + `taxonid`-Platzhalter im Vokabular-Tab.
4. **Anleitung-Tab** — neuer erster Reiter mit den drei Abläufen (nur KI / hybrid / säubern).
5. **Voll-i18n** — statische UND dynamische Texte DE/EN: statisch über `data-i18n`/`data-i18n-placeholder` (innerHTML aus vertrauenswürdigem In-Code-Dictionary), dynamisch (Fehler/Status/Ergebnisse, Listenzeilen + Buttons, Confirms) über `I18n.t()` mit `{param}`-Interpolation + Neu-Rendern beim Umschalten, Backend-Status via `I18n.st()`. Zwei Tests pinnen die Schlüssel-Vollständigkeit beider Sprachen; i18n-Engine per Node-Smoke laufzeitgeprüft.

## Offene Punkte / bewusst v2

- **Sprachumschalter:** komplette Oberfläche DE/EN — statisch **und dynamisch** (siehe Nachtrag Punkt 5). Weitere Sprachen = ein weiterer Dictionary-Block in `i18n.js`.
- v2-Kandidaten (im Plan): spaCy-NER-PII, Parquet, LLM-Umschreiben kuratierter Texte, Multi-User/Rollen.
- Ohne Codebezug (beim Betreiber): Repo anlegen, Image bauen + hochladen (GHCR via `docker.yml`), deployen; Basis-Image vor Prod per Digest pinnen (Hinweis oben im `Dockerfile`).

## Nachtrag (2026-07-16) — Audit abgearbeitet + Deployment-Gerüst

Der Whole-Codebase-Audit (`docs/audits/2026-07-12-audit.md`, 77/100, Conditional)
ist geschlossen. Alle Code-Findings behoben (test-first, je rot→grün; Detail je
Finding im `CHANGELOG.md`): **T1** O(N²)-Dedupe, **T3** Event-Loop-Offload heavy
Refine-Ops + thread-sicherer Encoder-Load-Lock (`embeddings.py`), **T4** Keyless
fail-closed, **T6** `run_store`-Extraktion, **T7**
Helper-Konsolidierung + `vocab_turtle`-Split, **T8** opt-in Cross-Request-Ceiling
(`llm.SpendLedger`, Default aus), **T9** `combine` 500→400, **CSP**, UI-`res.ok`,
i18n-a11y + Korrektheits-Cluster + Doku-Drift. **T5** (unbegrenztes Per-Run-Budget)
und **CSV-Injection** sind bewusste Won't-Fix-Entscheidungen (dokumentiert).

**Deployment vorbereitet** (Entscheidung: die zwei Apps getrennt halten): `Dockerfile`
(python:3.12-slim, non-root, healthcheck, single worker, 8110), `.dockerignore`,
`docker-compose.yml`, GitHub Actions `.github/workflows/ci.yml` (ruff+mypy+pytest+
OpenAPI-Smoke) und `docker.yml` (getakteter GHCR-Build/Push) — nach api_v3-Muster,
angepasst auf 3.12/8110/`DATAPREP_`/`requirements.lock`. Neue Module seit v0.2.0:
`app/run_store.py`, `app/vocab_turtle.py`, `app/samples.py` (toleranter Reader).

## Erinnerungen (persistente Memory)

`~/.claude/.../memory/`: `data-prep-app.md`, `data-prep-preflight-parity.md`,
`faecher-ai-cv5-production-model.md`, `apiv3-training-memory-pressure.md` (+ MEMORY.md-Index).
