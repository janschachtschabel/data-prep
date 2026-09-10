/* Lightweight i18n: swaps the text of [data-i18n] elements (and the placeholder
   of [data-i18n-placeholder] elements) between English and German, persisted per
   tab. Covers the navigation shell, help texts, form labels, buttons and the
   descriptive option lists.

   innerHTML is used on purpose: every DICT value below is a developer-authored
   constant in THIS file (never user- or API-supplied), so there is no injection
   surface — and it lets the help text keep its <strong>/<em>/<code> markup.
   External data elsewhere (vocab labels, samples) is still rendered via
   textContent in the feature modules. */
"use strict";

const I18n = (() => {
  const KEY = "dataprep-lang";
  const DICT = {
    en: {
      // --- shell ---
      "topbar.subtitle": "dataset workshop",
      "login.signin": "Sign in",
      "login.hint": "Enter the server's API key to build and refine datasets. If the server runs" +
        " without a key (local use), you are signed in automatically.",
      "topbar.authoff": "auth disabled — local use",
      "topbar.signout": "Sign out",
      "lang.toggle": "Deutsch",
      "tab.guide": "Guide", "tab.runs": "Runs", "tab.review": "Review", "tab.seeds": "Seeds",
      "tab.vocabularies": "Vocabularies", "tab.references": "References", "tab.refine": "Refine",
      "h2.guide": "Start here", "h2.runs": "Generation runs", "h2.review": "Review",
      "h2.seeds": "Seeds", "h2.vocabularies": "Vocabularies", "h2.references": "References",
      "h2.refine": "Refine",
      "btn.reload": "Reload",

      // --- AI access (per-request OpenAI key + model) ---
      "llm.summary": "AI access — OpenAI key & model",
      "llm.intro": `Enter your own OpenAI API key and model for the AI features (seed bootstrap,
        generation, enrichment). They are kept in <strong>this browser tab only</strong> and sent
        with each AI request — never stored on the server.`,
      "llm.key": "OpenAI API key",
      "llm.model": "Model",
      "llm.model.hint": "Leave empty to use the server's configured model.",
      "llm.save": "Save",
      "llm.clear": "Clear",

      // --- guide ---
      "guide.intro": `This workshop does two things: it <strong>generates</strong> publishable,
        fully-synthetic datasets from a vocabulary + AI, and it <strong>refines</strong> existing
        datasets for training. Pick the workflow that matches your situation — you normally move
        left to right through the tabs.`,
      "guide.ai.h": "1 · Create a dataset with AI only (no own data)",
      "guide.ai.body": `Best for a fully synthetic, shareable set. <strong>Vocabularies</strong>
        (load the label list, e.g. Schulfächer) → <strong>Seeds</strong> (build a seed set, leave
        the reference empty = vocab-only; let the AI bootstrap examples) → <strong>Runs</strong>
        (dry-run for the cost, then start; add <em>Context / guidance</em> so it knows what the
        entries are about) → <strong>Review</strong> (approve / discard) → download CSV or push to
        api_v3.`,
      "guide.hybrid.h": "2 · Blend your existing data into an AI set (hybrid)",
      "guide.hybrid.body": `Best when you have a curated CSV and want synthetic data in its style.
        <strong>References</strong> (upload the CSV — it is PII-scrubbed on import) →
        <strong>Seeds</strong> (pick that reference set; seeds are distilled from your rows) →
        <strong>Runs</strong> → <strong>Review</strong> → export. A leakage filter keeps
        near-copies of your data out of the result, so the output stays publishable.`,
      "guide.clean.h": "3 · Clean up existing data",
      "guide.clean.body": `Best for tidying a dataset before training. Go to <strong>Refine</strong>:
        <em>Analyze</em> and <em>Training preflight</em> show what you have and the effective
        training set; then apply filters (dedupe, length, balance, mask PII, …) — each
        <em>Apply</em> writes a new dataset, so you can stack steps. <em>Prepare for training</em>
        makes an honest, text-disjoint holdout split.`,
      "guide.eval": `Reminder: synthetic and enriched rows are for <strong>training only</strong>.
        Judge quality on a real, text-disjoint holdout — never on the synthetic rows themselves.`,

      // --- runs ---
      "intro.runs": "Generate synthetic samples from a seed set — vocab-only or hybrid, the seeds" +
        " decide. Always dry-run first to see the cost estimate.",
      "intro.runs.models": "Configured models:",
      "run.explain.summary": "What does a run generate? (fields, label, length)",
      "run.explain.fields": `Each run produces catalog-style entries for every selected concept of
        the seed set's vocabulary. Per entry it writes three text fields — <strong>title</strong>,
        <strong>description</strong>, <strong>keywords</strong> — and one classification label: the
        concept itself, exported to the vocabulary's metadata field (e.g.
        <code>properties.ccm:taxonid</code>). These three fields are fixed because they map to the
        training columns of api_v3.`,
      "run.explain.example": `<strong>Example — school subjects:</strong> vocabulary
        <em>Schulfächer</em> (field <code>taxonid</code>), concept “Mathematik” → title
        “Bruchrechnung Klasse 6”, a 300–600-character description, keywords “Brüche, kürzen,
        Hauptnenner”. Length is set by the profile below.`,
      "run.explain.guidance": `Use <em>Context / guidance</em> below when the labels alone are not
        self-explanatory — it steers what the entries are about (e.g. “German school subjects,
        secondary level; realistic teaching materials”).`,
      "run.newrun.h": "New run",
      "run.seedset": "Seed set",
      "run.concepts.legend": "Concepts",
      "run.mode.all": "all", "run.mode.subtree": "subtree of…", "run.mode.list": "explicit list",
      "run.perconcept": "Samples per concept",
      "run.batch": "Items per LLM call",
      "run.profile": "Length profile",
      "run.profile.kurz": "short (120–300 chars)",
      "run.profile.standard": "standard (300–600 chars)",
      "run.profile.lang": "long (800–2000 chars)",
      "run.profile.volltext": "full text (3000–6000 chars)",
      "run.profile.custom": "custom…",
      "run.min": "Min chars", "run.max": "Max chars",
      "run.root.ph": "a concept URI from the vocabulary (not the scheme URL)",
      "run.uris.ph": "one concept URI per line",
      "nav.sections": "Sections",
      "run.root.aria": "Subtree root URI",
      "run.uris.aria": "Concept URIs",
      "run.concurrency": `Concurrent LLM calls <span class="muted">(parallel workers — effective max
        is the number of concepts)</span>`,
      "run.guidance": `Context / guidance <span class="muted">(optional — what is this about?)</span>`,
      "run.guidance.ph": "e.g. German school subjects (secondary level). Generate realistic" +
        " teaching-material catalog entries; keep each entry about exactly one subject.",
      "run.dry.btn": "Dry run (cost estimate)", "run.start.btn": "Start run",
      "run.list.h": "Runs",
      "run.empty": "No runs yet — build a seed set, then start here.",

      // --- review ---
      "intro.review": `Approve or discard generated samples. Focus a card and press <kbd>a</kbd> to
        approve or <kbd>d</kbd> to discard. Discarding reopens a slot; <em>Regenerate</em> resumes
        the run to refill it.`,
      "review.run": "Run", "review.status": "Status",
      "review.status.all": "all", "review.status.passed": "passed",
      "review.status.approved": "approved", "review.status.discarded": "discarded",
      "review.minsim": "Min. similarity",
      "review.concept": `Concept URI <span class="muted">(optional)</span>`,
      "review.apply.btn": "Apply filters", "review.reload.btn": "Reload run list",
      "review.regen.btn": "Regenerate discarded",
      "review.empty": "Choose a run to review its samples.",

      // --- seeds ---
      "intro.seeds": `<strong>A seed is a short example entry</strong> (title, description, keywords)
        that shows the AI the kind of material you want for one label. Per concept a handful of
        seeds steer the later generation so it stays on-topic and varied. You build a
        <em>seed set</em> here, review and edit it, then a Run generates the full dataset from it.`,
      "seeds.help.summary": "Which mode should I use? (walk through the use cases)",
      "seeds.help.vocabonly": `<strong>Only AI + vocabulary (vocab-only):</strong> you have no own
        data, or want a fully synthetic, publishable dataset. Leave <em>Reference set</em> empty.
        The AI writes the seeds itself from each label and its place in the hierarchy. In the editor
        you can press <em>Bootstrap</em> to generate seeds for a concept.`,
      "seeds.help.hybrid": `<strong>Existing data + AI + vocabulary (hybrid):</strong> you have a
        curated CSV and want the synthetic data to match its style. First upload the CSV under
        <em>References</em> (it is PII-scrubbed on import), then pick it as the <em>Reference
        set</em> here. Seeds are distilled from your real examples; during the Run a leakage filter
        keeps near-copies of your data out of the output.`,
      "seeds.help.mixed": `<strong>Mixed:</strong> subjects that have reference rows are seeded from
        them; subjects without any are bootstrapped by the AI — automatically, in one seed set.`,
      "seeds.build.h": "Build a seed set",
      "seed.name": "Name", "seed.vocab": "Vocabulary",
      "seed.reference": `Reference set <span class="muted">(optional — empty = vocab-only)</span>`,
      "seed.perconcept": `Few-shot seeds per concept <span class="muted">(5–10 is enough — the AI's
        style examples)</span>`,
      "seed.build.btn": "Build seed set",
      "seed.tb.legend": "Term bank (from the reference)",
      "seed.tb.help": `Separate from the few-shot seeds: keywords + entities mined per concept from
        many reference rows, deduplicated. The run uses them to steer and vary the content; the AI
        can refine each concept's list later.`,
      "seed.tb.rows": `Rows per label to mine <span class="muted">(100–1000)</span>`,
      "seed.tb.topn": "Max terms per label",
      "seed.kwcols": `Keyword columns <span class="muted">(optional, comma-separated — default: the
        reference's keyword column)</span>`,
      "seed.termcols": `Term/entity columns <span class="muted">(optional — default: title +
        description)</span>`,
      "seed.terms.h": "Term bank (keywords + entities)",
      "seed.terms.help": `Terms mined from the reference for this concept. The AI can clean the list
        and add missing on-topic terms; the run uses it to steer and vary the generated content.`,
      "seed.terms.context": `Context for the cleanup <span class="muted">(optional — e.g. school,
        secondary level)</span>`,
      "seed.terms.btn": "Refine terms (LLM)",
      "seeds.list.h": "Seed sets", "seed.empty": "None yet — build one above.",
      "seed.concept": "Concept",
      "seed.none": "No seeds for this concept yet — add some below or let the AI bootstrap it from" +
        " the vocabulary context.",
      "seed.th.title": "Title", "seed.th.desc": "Description", "seed.th.keywords": "Keywords",
      "seed.th.source": "Source",
      "seed.add.title": "New seed — title", "seed.add.desc": "Description",
      "seed.add.keywords": "Keywords", "seed.add.btn": "Add to list",
      "seed.save.btn": "Save concept seeds", "seed.bootstrap.btn": "Bootstrap 4 seeds (LLM)",

      // --- vocabularies ---
      "intro.vocab": "A vocabulary is the list of labels a dataset can use (e.g. the school" +
        " subjects). Every seed set and every generated dataset is built on one. Start with a" +
        " default below, or load any SkoHub vocabulary by URL / file.",
      "vocab.presets.h": "Default vocabularies (one click)",
      "vocab.presets.help": `The common WLO facets. Click one to fill the fetch form, then press
        <em>Fetch vocabulary</em>.`,
      "vocab.presets.note": `<strong>Which field?</strong> The subtitle after each name is the
        dataset column its labels belong to. <em>Schulfächer</em> and <em>Hochschulfächer</em> both
        use <code>taxonid</code> but are different value sets — for a school-subjects dataset load
        <em>Schulfächer</em> (discipline), whose URIs are school subjects only, not university
        ones.`,
      "vocab.url.h": "Load from URL",
      "vocab.url": `Vocabulary URL <span class="muted">(https, allowed hosts only)</span>`,
      "vocab.fetch.name": `Name <span class="muted">(optional — derived from the URL if empty)</span>`,
      "vocab.field.help": `Metadata field <span class="muted">(optional — the dataset column these
        labels belong to, e.g. properties.ccm:taxonid)</span>`,
      "vocab.fetch.btn": "Fetch vocabulary",
      "vocab.upload.h": "Upload file",
      "vocab.upload.file": `Vocabulary file <span class="muted">(SKOS JSON-LD, or SkoHub Turtle
        .ttl)</span>`,
      "vocab.name.opt": `Name <span class="muted">(optional — filename if empty)</span>`,
      "vocab.upload.btn": "Upload vocabulary",
      "vocab.manual.h": "Type it in (manual)",
      "vocab.manual.help": `No SkoHub link or file at hand? Paste the labels — one concept per line.
        A line is either just a <code>Label</code>, or <code>Label | URI</code> (order does not
        matter) when you already know the concept's URI. Blank lines and lines starting with
        <code>#</code> are ignored.`,
      "vocab.name": "Name",
      "vocab.manual.text": "Concepts (one per line)",
      "vocab.manual.lang": "Label language",
      "vocab.field.opt": `Metadata field <span class="muted">(optional)</span>`,
      "vocab.manual.btn": "Create vocabulary",
      "vocab.loaded.h": "Loaded vocabularies",
      "vocab.empty": "None yet — fetch or upload one above.",

      // --- references ---
      "intro.references": `Optional curated reference sets for hybrid runs. Every upload is
        PII-scrubbed <em>before</em> it is stored (e-mails, phone numbers, URLs, handles are
        masked). Reference data feeds seeds and the leakage filter — it is never exported.`,
      "ref.upload.h": "Upload reference CSV",
      "ref.file": `CSV file <span class="muted">(semicolon-separated, UTF-8)</span>`,
      "ref.name": `Name <span class="muted">(optional — filename if empty)</span>`,
      "ref.textcols": `Text columns <span class="muted">(optional, comma-separated — default:
        cclom:title, cclom:general_description, cclom:general_keyword)</span>`,
      "ref.labelcol": `Label column <span class="muted">(optional — default: ccm:taxonid)</span>`,
      "ref.upload.btn": "Upload & scrub",
      "ref.loaded.h": "Loaded reference sets",
      "ref.empty": "None yet — hybrid runs are optional; without references the app works vocab-only.",

      // --- refine ---
      "intro.refine": `Work on existing datasets. Analyze distribution and PII, and run a training
        preflight that simulates the classification API's data preparation to show the
        <em>effective</em> training set before you train.`,
      "refine.upload.h": "Upload dataset",
      "refine.file": `CSV file <span class="muted">(semicolon-separated, UTF-8; stored as-is)</span>`,
      "refine.name": `Name <span class="muted">(optional — filename if empty)</span>`,
      "refine.upload.btn": "Upload",
      "refine.datasets.h": "Datasets", "refine.empty": "None yet — upload one above.",
      "refine.ops.h": "Operations",
      "refine.ops.help": `Operations stack: every <em>Apply</em> writes a <strong>new</strong>
        dataset and leaves the original untouched. To clean data in several steps, apply one
        operation, then apply the next to its result — the result is selected automatically, and the
        list of applied steps is shown below.`,
      "refine.dataset": "Dataset",
      "refine.textcols": `Text columns <span class="muted">(comma-separated; blank = WLO defaults)</span>`,
      "refine.labelcol": "Label column",
      "refine.labelfilter": `Label filter <span class="muted">(substring)</span>`,
      "refine.minsamples": `Min samples <span class="muted">(preflight; blank = auto)</span>`,
      "refine.analyze.btn": "Analyze", "refine.preflight.btn": "Training preflight",
      "refine.filter.legend": "Filter", "refine.filter.op": "Operation",
      "refine.op.dedupe_exact": "dedupe — exact duplicate texts",
      "refine.op.dedupe_semantic": "dedupe — semantic (embedding similarity)",
      "refine.op.length": "length corridor",
      "refine.op.drop_no_label": "drop rows without label",
      "refine.op.label_filter": "keep only labels containing…",
      "refine.op.cap_per_label": "cap rows per label (balance)",
      "refine.op.markup": "clean markup in text columns",
      "refine.op.pii": "PII — mask or drop",
      "refine.threshold": "Similarity threshold",
      "refine.lenmin": "Min chars", "refine.lenmax": "Max chars",
      "refine.substr": "Label substring", "refine.cap": "Max per label",
      "refine.piimode": "Mode", "refine.pii.mask": "mask", "refine.pii.drop": "drop row",
      "refine.target": `New dataset name <span class="muted">(for Apply)</span>`,
      "refine.preview.btn": "Preview", "refine.apply.btn": "Apply → new dataset",
      "refine.combine.legend": "Combine datasets",
      "refine.combine.help": `Pick sources in priority order (top wins on duplicate texts), map their
        columns to the target schema, then merge into a new dataset with a <code>source</code>
        column.`,
      "refine.combine.suggest.btn": "Suggest mappings",
      "refine.combine.target": "New dataset name", "refine.combine.btn": "Combine → new dataset",
      "refine.prep.legend": "Prepare for training",
      "refine.prep.help": `Stratified, text-disjoint holdout split (a holdout text never leaks into
        training) — honest evaluation. Uses the dataset and columns selected above.`,
      "prep.fraction": "Holdout fraction", "prep.seed": "Seed",
      "prep.target": `Output name <span class="muted">(_train / _holdout)</span>`,
      "prep.split.btn": "Split → train + holdout", "prep.push.btn": "Push selected dataset → api_v3",
      "refine.audit.legend": "Label audit (second opinion)",
      "refine.audit.help": `Ask a trained api_v3 model to review the labels. Rows where the model
        confidently disagrees with the gold label are listed for the editorial team — nothing is
        relabeled automatically.`,
      "audit.model": "api_v3 model name", "audit.conf": "Confidence ≥",
      "audit.topk": "Gold must be in top-k", "audit.btn": "Run label audit",
      "refine.enrich.legend": "Enrich (additive, LLM)",
      "refine.enrich.help": `Fill gaps only — never overwrites existing content. Every change is
        recorded in an <code>enriched_fields</code> column.`,
      "enrich.mode": "Mode",
      "enrich.mode.keywords": "add keywords (rows below the minimum)",
      "enrich.mode.description": "add description (empty ones)",
      "enrich.min": "Min keywords", "enrich.target": "New dataset name",
      "enrich.btn": "Enrich → new dataset",

      // --- dynamic (JS runtime strings, via I18n.t) ---
      "js.btn.view": "View", "js.btn.open": "Open", "js.btn.delete": "Delete",
      "js.btn.remove": "Remove", "js.btn.cancel": "Cancel", "js.btn.resume": "Resume",
      "js.btn.details": "Details", "js.btn.audit": "Audit",
      "js.err.request": "Request failed.", "js.err.requestFailed": "Request failed ({status})",
      "js.err.keyInvalid": "API key invalid — please sign in again.",
      "js.err.deleteFailed": "Delete failed.", "js.err.import": "Import failed.",
      "js.err.actionFailed": "{label} failed.",
      "js.err.vocabs": "Could not load vocabularies.", "js.err.details": "Could not load details.",
      "js.err.refs": "Could not load reference sets.", "js.err.seeds": "Could not load seed sets.",
      "js.err.runs": "Could not load runs.", "js.err.datasets": "Could not load datasets.",
      "js.err.loadList": "Could not load the list.",
      "js.st.running": "running", "js.st.paused": "paused", "js.st.completed": "completed",
      "js.st.cancelled": "cancelled", "js.st.failed": "failed", "js.st.passed": "passed",
      "js.st.approved": "approved", "js.st.discarded": "discarded", "js.st.interrupted": "interrupted",
      "js.app.models": "seeds → {seeds} · bulk → {bulk}",
      "js.app.cfgUnavailable": "configuration unavailable",
      "js.llm.saved": "Saved — your key is used for AI requests in this tab.",
      "js.llm.cleared": "Cleared — falling back to the server's key (if any).",
      "js.llm.usingOwn": "Using your key · model {model}.",
      "js.llm.usingServer": "Using the server's configured key.",
      "js.llm.none": "No AI key yet — enter one to use the AI features.",
      "js.llm.defaultModel": "server default",
      "js.login.enterKey": "Please enter an API key.", "js.login.failed": "Sign-in failed.",
      "js.vocab.row": "{name} — {title} ({count} concepts)",
      "js.vocab.field": " · field: {field}",
      "js.vocab.confirmDelete": "Delete vocabulary \"{name}\"?",
      "js.vocab.detailTitle": "{title} — {count} concepts ({langs})",
      "js.ref.row": "{name} — {rows} rows, {groups} concepts, PII scrubbed in {pii} rows",
      "js.ref.fields": " · fields: {cols} → {label}",
      "js.ref.confirmDelete": "Delete reference set \"{name}\"?",
      "js.ref.imported": "Imported \"{name}\": {rows} rows, {groups} concepts, PII masked in {pii} rows.",
      "js.seeds.phVocab": "— choose vocabulary —", "js.seeds.phRef": "— none (vocab-only) —",
      "js.seeds.row": "{name} — vocab {vocab}, {seeded}/{total} concepts seeded ({seeds} seeds), {withTerms} with terms",
      "js.seeds.confirmDelete": "Delete seed set \"{name}\"?",
      "js.seeds.errOpen": "Could not open seed set.",
      "js.seeds.editTitle": "Edit \"{name}\" (vocab: {vocab})",
      "js.seeds.conceptOption": "{label} ({n} seeds)",
      "js.seeds.errBuild": "Build failed.", "js.seeds.errSave": "Save failed.",
      "js.seeds.errBootstrap": "Bootstrap failed.", "js.seeds.generating": "Generating…",
      "js.seeds.added": "Added {n} seeds (LLM calls: {calls}, tokens: {tokens}).",
      "js.seeds.noTerms": "(no terms yet — build from a reference, or refine to generate them)",
      "js.seeds.refining": "Refining…",
      "js.seeds.termsRefined": "Term bank: {n} terms (LLM calls: {calls}, tokens: {tokens}).",
      "js.seeds.errTerms": "Refining the term bank failed.",
      "js.runs.phSeedset": "— choose seed set —",
      "js.runs.row": "{id} — {status}, {gen}/{target} samples", "js.runs.rowMsg": " · {message}",
      "js.runs.pushBtn": "Push to api_v3",
      "js.runs.confirmPush": "Push run \"{id}\" to the configured api_v3?",
      "js.runs.pushed": "Pushed {pushed} ({rows} rows) — api_v3 answered: {api}",
      "js.runs.details": "{id}: {status} · generated {gen}, discarded length {dl}, duplicates {dup} · LLM calls {calls}, tokens {tokens} · skipped batches {failed}",
      "js.runs.estimate": "Plan: {concepts} concepts, target {target} samples · ~{calls} LLM calls, ~{tokens} tokens (budget: {bcalls} calls / {btokens} tokens) · length {low}-{high} chars.",
      "js.runs.errDry": "Dry run failed.", "js.runs.errStart": "Start failed.",
      "js.runs.cancelling": "Cancelling — the run stops after the in-flight calls finish.",
      "js.runs.showingLast": "Showing the {shown} most recent of {total} runs.",
      "js.review.phRun": "— choose run —",
      "js.review.runOption": "{id} ({status}, {gen} samples)",
      "js.review.noMatch": "No samples match these filters.",
      "js.review.count": "{total} samples, showing {shown}.",
      "js.review.errSamples": "Could not load samples.",
      "js.review.badge": "{status} · {concept} · sim {sim}",
      "js.review.approve": "Approve (a)", "js.review.discard": "Discard (d)",
      "js.review.sampleStatus": "Sample {status}.",
      "js.review.confirmRegen": "Regenerate discarded samples? This resumes the run to refill kept-sample slots.",
      "js.review.regenStarted": "Regeneration started — reload samples in a moment.",
      "js.review.errRegen": "Regenerate failed.", "js.review.errUpdate": "Could not update the sample.",
      "js.refine.phDataset": "— choose dataset —",
      "js.refine.noOps": "No operations applied yet — this is an original upload.",
      "js.refine.appliedSteps": "Applied steps: ", "js.refine.fromSource": " (from {source})",
      "js.refine.sourceCheckbox": " {name} ({rows} rows)",
      "js.refine.row": "{name} — {rows} rows, {cols} columns",
      "js.refine.confirmDelete": "Delete dataset \"{name}\"?",
      "js.refine.chooseDataset": "Choose a dataset first.", "js.refine.opFailed": "Operation failed.",
      "js.refine.analyze": "{rows} rows · {labels} labels · {dupes} exact duplicates · {empty} empty texts · {nolabel} without label · text length {lmin}–{lmax} (mean {lmean}) · PII in {pii} rows {counts}",
      "js.refine.preview": "Preview", "js.refine.applied": "Applied → \"{target}\"",
      "js.refine.filterResult": "{mode}: {filter} · {before} → {after} rows (removed {removed}, changed {changed}).",
      "js.refine.chainHint": "\"{target}\" is now selected above — apply another filter to chain, or continue in another section.",
      "js.refine.removedMark": "— removed —",
      "js.refine.preflight": "Effective training rows: {eff} of {raw} raw ({labels} learnable labels at min_samples {min}). Kept after cleaning {kept}; dropped {short} too short, {nolabel} without label, {dup} duplicates.",
      "js.refine.selectSource": "Select at least one source dataset.",
      "js.refine.errSuggest": "Suggest failed.",
      "js.refine.mapHead": "{name} → target columns", "js.refine.mapNone": "— none —",
      "js.refine.enterCombineName": "Enter a name for the combined dataset.",
      "js.refine.suggestFirst": "Suggest mappings first.",
      "js.refine.combined": "Combined → \"{target}\": {rowsIn} rows in, {rowsOut} out ({conflicts} conflicts resolved).",
      "js.refine.errCombine": "Combine failed.",
      "js.refine.enterSplitName": "Enter an output name for the split.",
      "js.refine.split": "Split \"{target}\": {train} train + {holdout} holdout ({pct}% held out). Recommended min_samples: {rec}.",
      "js.refine.enterApplyName": "Enter a new dataset name to apply.",
      "js.refine.choosePush": "Choose a dataset to push.",
      "js.refine.confirmPush": "Push dataset \"{name}\" to the configured api_v3?",
      "js.refine.pushed": "Pushed {pushed} ({rows} rows) — api_v3: {api}", "js.refine.errPush": "Push failed.",
      "js.refine.enterModel": "Enter the api_v3 model name.",
      "js.refine.audit": "Audited {rows} rows · {flagged} flagged for review (model confidently disagrees with the gold label).",
      "js.refine.enterEnrichName": "Enter a name for the enriched dataset.",
      "js.refine.enriching": "Enriching…",
      "js.refine.enriched": "Enriched {enriched} of {rows} rows ({mode}) → \"{target}\". LLM calls: {calls}, tokens: {tokens}.",
      "js.refine.thLabel": "label", "js.refine.thSupport": "support",
      "js.refine.thBefore": "before", "js.refine.thAfter": "after",
      "js.refine.thSource": "source", "js.refine.thRowsIn": "rows in", "js.refine.thRowsKept": "rows kept",
      "js.refine.thEffective": "effective samples", "js.refine.thTrainSupport": "train support",
      "js.refine.thGold": "gold label", "js.refine.thModelTop": "model top",
      "js.refine.thConfidence": "confidence", "js.refine.thRanking": "model ranking",
      // --- tables tab (the generic table workbench) ---
      "tab.tables": "Tables",
      "h2.tables": "Tables",
      "intro.tables": "Work on a table as a table — before anything has been decided about which " +
        "column is the label. Read CSV, JSON or JSONL (plain or gzipped), look at the rows, filter by " +
        "column value, drop columns, find duplicates, join two files, and export in the format you need.",
      "tables.import.h": "Import a table",
      "tables.file": "File <span class=\"muted\">(CSV, JSON, JSONL — also .gz)</span>",
      "tables.format": "Format",
      "tables.format.auto": "Detect automatically",
      "tables.separator": "Separator <span class=\"muted\">(CSV)</span>",
      "tables.encoding": "Encoding",
      "tables.name": "Name <span class=\"muted\">(optional — filename if empty)</span>",
      "tables.import.btn": "Import",
      "tables.import.helpsum": "About nested JSON",
      "tables.import.help": "Nested objects become columns with dot paths, so " +
        "<code>properties.cclom:title</code> is a plain column you can filter on. Lists of simple " +
        "values are joined; a list of objects is kept as JSON text, because there is no sensible " +
        "column for it. Whatever you import is stored as semicolon CSV, so every later step works " +
        "the same way regardless of where the data came from.",
      "tables.dataset.h": "Dataset",
      "tables.dataset": "Dataset",
      "tables.empty": "No datasets yet — import one above.",
      "tables.export.format": "Export as",
      "tables.export.csv": "CSV, semicolon (api_v3)",
      "tables.export.csvgz": "CSV, semicolon, gzip",
      "tables.export.separator": "Separator <span class=\"muted\">(CSV)</span>",
      "tables.download.btn": "Download",
      "tables.view.h": "Rows",
      "tables.search": "Search <span class=\"muted\">(any visible column)</span>",
      "tables.columns": "Show columns <span class=\"muted\">(comma-separated; blank = all)</span>",
      "tables.view.btn": "Show rows",
      "tables.prev": "Previous",
      "tables.next": "Next",
      "tables.profile.h": "Column profile",
      "tables.profile.help": "How full each column is, how many different values it holds, which ones " +
        "dominate, and — where a column holds numbers — its range.",
      "tables.profile.btn": "Profile columns",
      "js.tables.working": "Working…",
      "js.tables.loading": "Loading…",
      "js.tables.phDataset": "— choose a dataset —",
      "js.tables.shape": "{rows} rows × {cols} columns",
      "js.tables.chain": "Steps applied:",
      "js.tables.imported": "Imported: {rows} rows, {cols} columns.",
      "js.tables.showing": "Showing {from}–{to} of {matched} matching rows ({total} in total).",
      "js.tables.noRows": "This dataset has no rows.",
      "js.tables.noMatch": "No row matches that search.",
      "js.tables.profiled": "{cols} columns over {rows} rows.",
      "js.tables.col.name": "Column",
      "js.tables.col.filled": "Filled",
      "js.tables.col.distinct": "Distinct",
      "js.tables.col.kind": "Type",
      "js.tables.col.top": "Most common",
      "js.tables.col.range": "Range",
      "js.tables.kind.numeric": "numeric",
      "js.tables.kind.text": "text",
      "js.tables.kind.empty": "empty",
      "js.tables.errDatasets": "Could not load the dataset list.",
      "js.tables.errImport": "Import failed.",
      "js.tables.errRows": "Could not load the rows.",
      "js.tables.errProfile": "Could not profile the columns.",
      "js.tables.errDownload": "Download failed.",
    },

    de: {
      // --- shell ---
      "topbar.subtitle": "Datenwerkstatt",
      "login.signin": "Anmelden",
      "login.hint": "Gib den API-Schlüssel des Servers ein, um Datensätze zu erstellen und" +
        " aufzubereiten. Läuft der Server ohne Schlüssel (lokale Nutzung), bist du automatisch" +
        " angemeldet.",
      "topbar.authoff": "Anmeldung deaktiviert — lokale Nutzung",
      "topbar.signout": "Abmelden",
      "lang.toggle": "English",
      "tab.guide": "Anleitung", "tab.runs": "Läufe", "tab.review": "Prüfen", "tab.seeds": "Seeds",
      "tab.vocabularies": "Vokabulare", "tab.references": "Referenzen", "tab.refine": "Aufbereiten",
      "h2.guide": "Los geht's", "h2.runs": "Generierungsläufe", "h2.review": "Prüfen",
      "h2.seeds": "Seeds", "h2.vocabularies": "Vokabulare", "h2.references": "Referenzen",
      "h2.refine": "Aufbereiten",
      "btn.reload": "Neu laden",

      // --- KI-Zugang (OpenAI-Schlüssel + Modell pro Anfrage) ---
      "llm.summary": "KI-Zugang — OpenAI-Schlüssel & Modell",
      "llm.intro": `Gib deinen eigenen OpenAI-API-Schlüssel und ein Modell für die KI-Funktionen
        (Seed-Bootstrap, Generierung, Anreicherung) ein. Sie werden <strong>nur in diesem
        Browser-Tab</strong> gehalten und mit jeder KI-Anfrage gesendet — nie auf dem Server
        gespeichert.`,
      "llm.key": "OpenAI-API-Schlüssel",
      "llm.model": "Modell",
      "llm.model.hint": "Leer lassen, um das auf dem Server konfigurierte Modell zu nutzen.",
      "llm.save": "Speichern",
      "llm.clear": "Löschen",

      // --- guide ---
      "guide.intro": `Diese Werkstatt kann zweierlei: sie <strong>erzeugt</strong> veröffentlichbare,
        voll­synthetische Datensätze aus einem Vokabular + KI, und sie <strong>bereitet</strong>
        bestehende Datensätze fürs Training <strong>auf</strong>. Wähle den Ablauf, der zu deiner
        Situation passt — normalerweise arbeitest du dich von links nach rechts durch die Reiter.`,
      "guide.ai.h": "1 · Einen Datensatz nur mit KI erstellen (ohne eigene Daten)",
      "guide.ai.body": `Ideal für einen voll­synthetischen, teilbaren Datensatz.
        <strong>Vokabulare</strong> (die Label-Liste laden, z. B. Schulfächer) →
        <strong>Seeds</strong> (ein Seed-Set bauen, Referenz leer lassen = nur Vokabular; die KI
        erzeugt Beispiele selbst) → <strong>Läufe</strong> (Probelauf für die Kosten, dann starten;
        füge <em>Kontext / Vorgabe</em> hinzu, damit klar ist, worum es geht) →
        <strong>Prüfen</strong> (annehmen / verwerfen) → CSV herunterladen oder an api_v3 senden.`,
      "guide.hybrid.h": "2 · Bestehende Daten in ein KI-Set einarbeiten (hybrid)",
      "guide.hybrid.body": `Ideal, wenn du eine kuratierte CSV hast und synthetische Daten in ihrem
        Stil möchtest. <strong>Referenzen</strong> (die CSV hochladen — sie wird beim Import von
        PII bereinigt) → <strong>Seeds</strong> (dieses Referenzset wählen; Seeds werden aus deinen
        Zeilen destilliert) → <strong>Läufe</strong> → <strong>Prüfen</strong> → exportieren. Ein
        Leakage-Filter hält Fast-Kopien deiner Daten aus dem Ergebnis heraus, damit die Ausgabe
        veröffentlichbar bleibt.`,
      "guide.clean.h": "3 · Bestehende Daten säubern",
      "guide.clean.body": `Ideal, um einen Datensatz vor dem Training aufzuräumen. Gehe zu
        <strong>Aufbereiten</strong>: <em>Analyze</em> und <em>Training preflight</em> zeigen, was
        du hast und wie groß die effektive Trainingsmenge ist; dann wende Filter an (Dubletten,
        Länge, Balance, PII maskieren, …) — jedes <em>Apply</em> schreibt einen neuen Datensatz, du
        kannst Schritte also stapeln. <em>Prepare for training</em> erzeugt einen ehrlichen,
        textdisjunkten Holdout-Split.`,
      "guide.eval": `Hinweis: synthetische und angereicherte Zeilen sind <strong>nur zum
        Training</strong>. Bewerte die Qualität auf einem echten, textdisjunkten Holdout — niemals
        auf den synthetischen Zeilen selbst.`,

      // --- runs ---
      "intro.runs": "Erzeuge synthetische Beispiele aus einem Seed-Set — nur Vokabular oder hybrid," +
        " die Seeds entscheiden. Mach immer zuerst einen Probelauf für die Kostenschätzung.",
      "intro.runs.models": "Konfigurierte Modelle:",
      "run.explain.summary": "Was erzeugt ein Lauf? (Felder, Label, Länge)",
      "run.explain.fields": `Jeder Lauf erzeugt katalogartige Einträge für jedes ausgewählte Konzept
        des Vokabulars aus dem Seed-Set. Pro Eintrag schreibt er drei Textfelder —
        <strong>Titel</strong>, <strong>Beschreibung</strong>, <strong>Schlagwörter</strong> — und
        ein Klassifikations-Label: das Konzept selbst, exportiert in das Metadatenfeld des
        Vokabulars (z. B. <code>properties.ccm:taxonid</code>). Diese drei Felder sind fest, weil
        sie auf die Trainingsspalten von api_v3 abgebildet werden.`,
      "run.explain.example": `<strong>Beispiel — Schulfächer:</strong> Vokabular
        <em>Schulfächer</em> (Feld <code>taxonid</code>), Konzept „Mathematik“ → Titel
        „Bruchrechnung Klasse 6“, eine Beschreibung mit 300–600 Zeichen, Schlagwörter „Brüche,
        kürzen, Hauptnenner“. Die Länge legt das Profil unten fest.`,
      "run.explain.guidance": `Nutze <em>Kontext / Vorgabe</em> unten, wenn die Labels allein nicht
        selbsterklärend sind — sie steuert, worum die Einträge gehen (z. B. „Deutsche Schulfächer,
        Sekundarstufe; realistische Unterrichtsmaterialien“).`,
      "run.newrun.h": "Neuer Lauf",
      "run.seedset": "Seed-Set",
      "run.concepts.legend": "Konzepte",
      "run.mode.all": "alle", "run.mode.subtree": "Teilbaum von…", "run.mode.list": "explizite Liste",
      "run.perconcept": "Beispiele pro Konzept",
      "run.batch": "Einträge pro LLM-Aufruf",
      "run.profile": "Längenprofil",
      "run.profile.kurz": "kurz (120–300 Zeichen)",
      "run.profile.standard": "Standard (300–600 Zeichen)",
      "run.profile.lang": "lang (800–2000 Zeichen)",
      "run.profile.volltext": "Volltext (3000–6000 Zeichen)",
      "run.profile.custom": "frei…",
      "run.min": "Min. Zeichen", "run.max": "Max. Zeichen",
      "run.root.ph": "eine Konzept-URI aus dem Vokabular (nicht die Schema-URL)",
      "run.uris.ph": "eine Konzept-URI pro Zeile",
      "nav.sections": "Bereiche",
      "run.root.aria": "Wurzel-URI des Teilbaums",
      "run.uris.aria": "Konzept-URIs",
      "run.concurrency": `Gleichzeitige LLM-Aufrufe <span class="muted">(parallele Worker — effektives
        Maximum ist die Anzahl der Konzepte)</span>`,
      "run.guidance": `Kontext / Vorgabe <span class="muted">(optional — worum geht es?)</span>`,
      "run.guidance.ph": "z. B. Deutsche Schulfächer (Sekundarstufe). Erzeuge realistische" +
        " Lernmaterial-Katalogeinträge; jeder Eintrag genau zu einem Fach.",
      "run.dry.btn": "Probelauf (Kostenschätzung)", "run.start.btn": "Lauf starten",
      "run.list.h": "Läufe",
      "run.empty": "Noch keine Läufe — baue ein Seed-Set und starte hier.",

      // --- review ---
      "intro.review": `Nimm erzeugte Beispiele an oder verwirf sie. Fokussiere eine Karte und drücke
        <kbd>a</kbd> zum Annehmen oder <kbd>d</kbd> zum Verwerfen. Verwerfen öffnet einen Platz
        wieder; <em>Regenerate</em> setzt den Lauf fort, um ihn nachzufüllen.`,
      "review.run": "Lauf", "review.status": "Status",
      "review.status.all": "alle", "review.status.passed": "bestanden",
      "review.status.approved": "angenommen", "review.status.discarded": "verworfen",
      "review.minsim": "Min. Ähnlichkeit",
      "review.concept": `Konzept-URI <span class="muted">(optional)</span>`,
      "review.apply.btn": "Filter anwenden", "review.reload.btn": "Laufliste neu laden",
      "review.regen.btn": "Verworfene neu erzeugen",
      "review.empty": "Wähle einen Lauf, um seine Beispiele zu prüfen.",

      // --- seeds ---
      "intro.seeds": `<strong>Ein Seed ist ein kurzer Beispiel-Eintrag</strong> (Titel,
        Beschreibung, Schlagwörter), der der KI zeigt, welche Art Material du für ein Label
        möchtest. Ein paar Seeds pro Konzept steuern die spätere Generierung, damit sie beim Thema
        bleibt und abwechslungsreich ist. Du baust hier ein <em>Seed-Set</em>, prüfst und
        bearbeitest es, dann erzeugt ein Lauf den vollständigen Datensatz daraus.`,
      "seeds.help.summary": "Welchen Modus soll ich nutzen? (die Anwendungsfälle durchgehen)",
      "seeds.help.vocabonly": `<strong>Nur KI + Vokabular (vocab-only):</strong> du hast keine
        eigenen Daten oder willst einen voll­synthetischen, veröffentlichbaren Datensatz. Lass
        <em>Referenzset</em> leer. Die KI schreibt die Seeds selbst aus jedem Label und seiner
        Stellung in der Hierarchie. Im Editor kannst du <em>Bootstrap</em> drücken, um Seeds für ein
        Konzept zu erzeugen.`,
      "seeds.help.hybrid": `<strong>Bestandsdaten + KI + Vokabular (hybrid):</strong> du hast eine
        kuratierte CSV und willst, dass die synthetischen Daten zu ihrem Stil passen. Lade die CSV
        zuerst unter <em>Referenzen</em> hoch (beim Import PII-bereinigt), dann wähle sie hier als
        <em>Referenzset</em>. Seeds werden aus deinen echten Beispielen destilliert; während des
        Laufs hält ein Leakage-Filter Fast-Kopien deiner Daten aus der Ausgabe heraus.`,
      "seeds.help.mixed": `<strong>Gemischt:</strong> Fächer mit Referenzzeilen werden aus ihnen
        geseedet; Fächer ohne welche werden von der KI gebootstrappt — automatisch, in einem
        Seed-Set.`,
      "seeds.build.h": "Seed-Set bauen",
      "seed.name": "Name", "seed.vocab": "Vokabular",
      "seed.reference": `Referenzset <span class="muted">(optional — leer = nur Vokabular)</span>`,
      "seed.perconcept": `Few-shot-Seeds pro Konzept <span class="muted">(5–10 genügen — die
        Stil-Beispiele für die KI)</span>`,
      "seed.build.btn": "Seed-Set bauen",
      "seed.tb.legend": "Begriffs-Bank (aus der Referenz)",
      "seed.tb.help": `Getrennt von den Few-Shot-Seeds: Schlagwörter + Entitäten je Konzept aus
        vielen Referenzzeilen gemint, dedupliziert. Der Lauf nutzt sie zum Steuern und Variieren der
        Inhalte; die KI kann die Liste je Konzept später verfeinern.`,
      "seed.tb.rows": `Zeilen je Label zum Minen <span class="muted">(100–1000)</span>`,
      "seed.tb.topn": "Max. Begriffe je Label",
      "seed.kwcols": `Schlagwort-Spalten <span class="muted">(optional, kommagetrennt — Standard:
        die Schlagwort-Spalte der Referenz)</span>`,
      "seed.termcols": `Begriffs-/Entitätsspalten <span class="muted">(optional — Standard: Titel +
        Beschreibung)</span>`,
      "seed.terms.h": "Begriffs-Bank (Schlagwörter + Entitäten)",
      "seed.terms.help": `Aus der Referenz für dieses Konzept gewonnene Begriffe. Die KI kann die
        Liste bereinigen und passende Begriffe ergänzen; der Lauf nutzt sie zum Steuern und
        Variieren der erzeugten Inhalte.`,
      "seed.terms.context": `Kontext für die Bereinigung <span class="muted">(optional — z. B. Schule,
        Sekundarstufe)</span>`,
      "seed.terms.btn": "Begriffe verfeinern (LLM)",
      "seeds.list.h": "Seed-Sets", "seed.empty": "Noch keine — baue oben eines.",
      "seed.concept": "Konzept",
      "seed.none": "Noch keine Seeds für dieses Konzept — füge unten welche hinzu oder lass die KI" +
        " es aus dem Vokabular-Kontext bootstrappen.",
      "seed.th.title": "Titel", "seed.th.desc": "Beschreibung", "seed.th.keywords": "Schlagwörter",
      "seed.th.source": "Quelle",
      "seed.add.title": "Neuer Seed — Titel", "seed.add.desc": "Beschreibung",
      "seed.add.keywords": "Schlagwörter", "seed.add.btn": "Zur Liste hinzufügen",
      "seed.save.btn": "Konzept-Seeds speichern", "seed.bootstrap.btn": "4 Seeds bootstrappen (LLM)",

      // --- vocabularies ---
      "intro.vocab": "Ein Vokabular ist die Liste der Labels, die ein Datensatz nutzen kann (z. B." +
        " die Schulfächer). Jedes Seed-Set und jeder erzeugte Datensatz baut auf einem auf. Beginne" +
        " mit einem Default unten oder lade ein beliebiges SkoHub-Vokabular per URL / Datei.",
      "vocab.presets.h": "Standard-Vokabulare (ein Klick)",
      "vocab.presets.help": `Die gängigen WLO-Facetten. Klicke eine an, um das Formular zu füllen,
        dann drücke <em>Fetch vocabulary</em>.`,
      "vocab.presets.note": `<strong>Welches Feld?</strong> Der Untertitel hinter jedem Namen ist die
        Datensatz-Spalte, zu der seine Labels gehören. <em>Schulfächer</em> und
        <em>Hochschulfächer</em> nutzen beide <code>taxonid</code>, sind aber verschiedene
        Wertemengen — für einen Schulfächer-Datensatz lade <em>Schulfächer</em> (discipline), dessen
        URIs nur Schulfächer sind, keine Hochschulfächer.`,
      "vocab.url.h": "Per URL laden",
      "vocab.url": `Vokabular-URL <span class="muted">(nur https, erlaubte Hosts)</span>`,
      "vocab.fetch.name": `Name <span class="muted">(optional — sonst aus der URL abgeleitet)</span>`,
      "vocab.field.help": `Metadatenfeld <span class="muted">(optional — die Datensatz-Spalte, zu der
        diese Labels gehören, z. B. properties.ccm:taxonid)</span>`,
      "vocab.fetch.btn": "Vokabular abrufen",
      "vocab.upload.h": "Datei hochladen",
      "vocab.upload.file": `Vokabular-Datei <span class="muted">(SKOS JSON-LD oder SkoHub Turtle
        .ttl)</span>`,
      "vocab.name.opt": `Name <span class="muted">(optional — sonst Dateiname)</span>`,
      "vocab.upload.btn": "Vokabular hochladen",
      "vocab.manual.h": "Selbst eintippen (manuell)",
      "vocab.manual.help": `Kein SkoHub-Link oder keine Datei zur Hand? Füge die Labels ein — ein
        Konzept pro Zeile. Eine Zeile ist entweder nur ein <code>Label</code> oder
        <code>Label | URI</code> (Reihenfolge egal), wenn du die URI schon kennst. Leere Zeilen und
        Zeilen, die mit <code>#</code> beginnen, werden ignoriert.`,
      "vocab.name": "Name",
      "vocab.manual.text": "Konzepte (eines pro Zeile)",
      "vocab.manual.lang": "Label-Sprache",
      "vocab.field.opt": `Metadatenfeld <span class="muted">(optional)</span>`,
      "vocab.manual.btn": "Vokabular erstellen",
      "vocab.loaded.h": "Geladene Vokabulare",
      "vocab.empty": "Noch keine — rufe oben eines ab oder lade eines hoch.",

      // --- references ---
      "intro.references": `Optionale kuratierte Referenzsets für hybride Läufe. Jeder Upload wird
        <em>vor</em> dem Speichern von PII bereinigt (E-Mails, Telefonnummern, URLs, Handles werden
        maskiert). Referenzdaten speisen Seeds und den Leakage-Filter — sie werden nie exportiert.`,
      "ref.upload.h": "Referenz-CSV hochladen",
      "ref.file": `CSV-Datei <span class="muted">(semikolongetrennt, UTF-8)</span>`,
      "ref.name": `Name <span class="muted">(optional — sonst Dateiname)</span>`,
      "ref.textcols": `Textspalten <span class="muted">(optional, kommagetrennt — Standard:
        cclom:title, cclom:general_description, cclom:general_keyword)</span>`,
      "ref.labelcol": `Label-Spalte <span class="muted">(optional — Standard: ccm:taxonid)</span>`,
      "ref.upload.btn": "Hochladen & bereinigen",
      "ref.loaded.h": "Geladene Referenzsets",
      "ref.empty": "Noch keine — hybride Läufe sind optional; ohne Referenzen arbeitet die App nur" +
        " mit dem Vokabular.",

      // --- refine ---
      "intro.refine": `Arbeite an bestehenden Datensätzen. Analysiere Verteilung und PII und mache
        einen Training-Preflight, der die Datenaufbereitung der Klassifikations-API simuliert und
        die <em>effektive</em> Trainingsmenge vor dem Training zeigt.`,
      "refine.upload.h": "Datensatz hochladen",
      "refine.file": `CSV-Datei <span class="muted">(semikolongetrennt, UTF-8; wird unverändert
        gespeichert)</span>`,
      "refine.name": `Name <span class="muted">(optional — sonst Dateiname)</span>`,
      "refine.upload.btn": "Hochladen",
      "refine.datasets.h": "Datensätze", "refine.empty": "Noch keine — lade oben einen hoch.",
      "refine.ops.h": "Operationen",
      "refine.ops.help": `Operationen stapeln sich: jedes <em>Apply</em> schreibt einen
        <strong>neuen</strong> Datensatz und lässt das Original unangetastet. Um Daten in mehreren
        Schritten zu säubern, wende eine Operation an, dann die nächste auf ihr Ergebnis — das
        Ergebnis ist automatisch ausgewählt, und die Liste der angewandten Schritte steht unten.`,
      "refine.dataset": "Datensatz",
      "refine.textcols": `Textspalten <span class="muted">(kommagetrennt; leer = WLO-Standard)</span>`,
      "refine.labelcol": "Label-Spalte",
      "refine.labelfilter": `Label-Filter <span class="muted">(Teilzeichenkette)</span>`,
      "refine.minsamples": `Min. Beispiele <span class="muted">(Preflight; leer = automatisch)</span>`,
      "refine.analyze.btn": "Analysieren", "refine.preflight.btn": "Training-Preflight",
      "refine.filter.legend": "Filter", "refine.filter.op": "Operation",
      "refine.op.dedupe_exact": "Dubletten — exakt gleiche Texte",
      "refine.op.dedupe_semantic": "Dubletten — semantisch (Embedding-Ähnlichkeit)",
      "refine.op.length": "Längenkorridor",
      "refine.op.drop_no_label": "Zeilen ohne Label entfernen",
      "refine.op.label_filter": "nur Labels behalten, die … enthalten",
      "refine.op.cap_per_label": "Zeilen pro Label begrenzen (Balance)",
      "refine.op.markup": "Markup in Textspalten säubern",
      "refine.op.pii": "PII — maskieren oder entfernen",
      "refine.threshold": "Ähnlichkeitsschwelle",
      "refine.lenmin": "Min. Zeichen", "refine.lenmax": "Max. Zeichen",
      "refine.substr": "Label-Teilzeichenkette", "refine.cap": "Max. pro Label",
      "refine.piimode": "Modus", "refine.pii.mask": "maskieren", "refine.pii.drop": "Zeile entfernen",
      "refine.target": `Name des neuen Datensatzes <span class="muted">(für Apply)</span>`,
      "refine.preview.btn": "Vorschau", "refine.apply.btn": "Apply → neuer Datensatz",
      "refine.combine.legend": "Datensätze kombinieren",
      "refine.combine.help": `Wähle Quellen in Prioritätsreihenfolge (die oberste gewinnt bei
        doppelten Texten), ordne ihre Spalten dem Zielschema zu, dann führe sie zu einem neuen
        Datensatz mit einer <code>source</code>-Spalte zusammen.`,
      "refine.combine.suggest.btn": "Zuordnungen vorschlagen",
      "refine.combine.target": "Name des neuen Datensatzes",
      "refine.combine.btn": "Kombinieren → neuer Datensatz",
      "refine.prep.legend": "Fürs Training vorbereiten",
      "refine.prep.help": `Stratifizierter, textdisjunkter Holdout-Split (ein Holdout-Text gelangt
        nie ins Training) — ehrliche Auswertung. Nutzt den oben gewählten Datensatz und die
        Spalten.`,
      "prep.fraction": "Holdout-Anteil", "prep.seed": "Seed",
      "prep.target": `Ausgabename <span class="muted">(_train / _holdout)</span>`,
      "prep.split.btn": "Splitten → train + holdout", "prep.push.btn": "Gewählten Datensatz → api_v3 senden",
      "refine.audit.legend": "Label-Audit (Zweitmeinung)",
      "refine.audit.help": `Lass ein trainiertes api_v3-Modell die Labels prüfen. Zeilen, bei denen
        das Modell dem Gold-Label deutlich widerspricht, werden für die Redaktion aufgelistet —
        nichts wird automatisch umgelabelt.`,
      "audit.model": "api_v3-Modellname", "audit.conf": "Konfidenz ≥",
      "audit.topk": "Gold muss in den Top-k sein", "audit.btn": "Label-Audit ausführen",
      "refine.enrich.legend": "Anreichern (additiv, LLM)",
      "refine.enrich.help": `Nur Lücken füllen — überschreibt nie bestehende Inhalte. Jede Änderung
        wird in einer <code>enriched_fields</code>-Spalte vermerkt.`,
      "enrich.mode": "Modus",
      "enrich.mode.keywords": "Schlagwörter ergänzen (Zeilen unter dem Minimum)",
      "enrich.mode.description": "Beschreibung ergänzen (leere)",
      "enrich.min": "Min. Schlagwörter", "enrich.target": "Name des neuen Datensatzes",
      "enrich.btn": "Anreichern → neuer Datensatz",

      // --- dynamic (JS runtime strings, via I18n.t) ---
      "js.btn.view": "Ansehen", "js.btn.open": "Öffnen", "js.btn.delete": "Löschen",
      "js.btn.remove": "Entfernen", "js.btn.cancel": "Abbrechen", "js.btn.resume": "Fortsetzen",
      "js.btn.details": "Details", "js.btn.audit": "Audit",
      "js.err.request": "Anfrage fehlgeschlagen.", "js.err.requestFailed": "Anfrage fehlgeschlagen ({status})",
      "js.err.keyInvalid": "API-Schlüssel ungültig — bitte erneut anmelden.",
      "js.err.deleteFailed": "Löschen fehlgeschlagen.", "js.err.import": "Import fehlgeschlagen.",
      "js.err.actionFailed": "{label} fehlgeschlagen.",
      "js.err.vocabs": "Vokabulare konnten nicht geladen werden.",
      "js.err.details": "Details konnten nicht geladen werden.",
      "js.err.refs": "Referenzsets konnten nicht geladen werden.",
      "js.err.seeds": "Seed-Sets konnten nicht geladen werden.",
      "js.err.runs": "Läufe konnten nicht geladen werden.",
      "js.err.datasets": "Datensätze konnten nicht geladen werden.",
      "js.err.loadList": "Die Liste konnte nicht geladen werden.",
      "js.st.running": "läuft", "js.st.paused": "pausiert", "js.st.completed": "abgeschlossen",
      "js.st.cancelled": "abgebrochen", "js.st.failed": "fehlgeschlagen", "js.st.passed": "bestanden",
      "js.st.approved": "angenommen", "js.st.discarded": "verworfen", "js.st.interrupted": "unterbrochen",
      "js.app.models": "Seeds → {seeds} · Bulk → {bulk}",
      "js.app.cfgUnavailable": "Konfiguration nicht verfügbar",
      "js.llm.saved": "Gespeichert — dein Schlüssel wird in diesem Tab für KI-Anfragen genutzt.",
      "js.llm.cleared": "Gelöscht — es gilt wieder der Server-Schlüssel (falls vorhanden).",
      "js.llm.usingOwn": "Dein Schlüssel · Modell {model}.",
      "js.llm.usingServer": "Der auf dem Server konfigurierte Schlüssel wird genutzt.",
      "js.llm.none": "Noch kein KI-Schlüssel — gib einen ein, um die KI-Funktionen zu nutzen.",
      "js.llm.defaultModel": "Server-Standard",
      "js.login.enterKey": "Bitte gib einen API-Schlüssel ein.", "js.login.failed": "Anmeldung fehlgeschlagen.",
      "js.vocab.row": "{name} — {title} ({count} Konzepte)",
      "js.vocab.field": " · Feld: {field}",
      "js.vocab.confirmDelete": "Vokabular „{name}“ löschen?",
      "js.vocab.detailTitle": "{title} — {count} Konzepte ({langs})",
      "js.ref.row": "{name} — {rows} Zeilen, {groups} Konzepte, PII in {pii} Zeilen bereinigt",
      "js.ref.fields": " · Felder: {cols} → {label}",
      "js.ref.confirmDelete": "Referenzset „{name}“ löschen?",
      "js.ref.imported": "„{name}“ importiert: {rows} Zeilen, {groups} Konzepte, PII in {pii} Zeilen maskiert.",
      "js.seeds.phVocab": "— Vokabular wählen —", "js.seeds.phRef": "— keins (nur Vokabular) —",
      "js.seeds.row": "{name} — Vokabular {vocab}, {seeded}/{total} Konzepte geseedet ({seeds} Seeds), {withTerms} mit Begriffen",
      "js.seeds.confirmDelete": "Seed-Set „{name}“ löschen?",
      "js.seeds.errOpen": "Seed-Set konnte nicht geöffnet werden.",
      "js.seeds.editTitle": "„{name}“ bearbeiten (Vokabular: {vocab})",
      "js.seeds.conceptOption": "{label} ({n} Seeds)",
      "js.seeds.errBuild": "Bauen fehlgeschlagen.", "js.seeds.errSave": "Speichern fehlgeschlagen.",
      "js.seeds.errBootstrap": "Bootstrap fehlgeschlagen.", "js.seeds.generating": "Erzeuge…",
      "js.seeds.added": "{n} Seeds hinzugefügt (LLM-Aufrufe: {calls}, Tokens: {tokens}).",
      "js.seeds.noTerms": "(noch keine Begriffe — aus einer Referenz bauen oder per Verfeinern erzeugen)",
      "js.seeds.refining": "Verfeinere…",
      "js.seeds.termsRefined": "Begriffs-Bank: {n} Begriffe (LLM-Aufrufe: {calls}, Tokens: {tokens}).",
      "js.seeds.errTerms": "Verfeinern der Begriffs-Bank fehlgeschlagen.",
      "js.runs.phSeedset": "— Seed-Set wählen —",
      "js.runs.row": "{id} — {status}, {gen}/{target} Beispiele", "js.runs.rowMsg": " · {message}",
      "js.runs.pushBtn": "An api_v3 senden",
      "js.runs.confirmPush": "Lauf „{id}“ an das konfigurierte api_v3 senden?",
      "js.runs.pushed": "{pushed} gesendet ({rows} Zeilen) — api_v3 antwortete: {api}",
      "js.runs.details": "{id}: {status} · erzeugt {gen}, verworfen (Länge) {dl}, Dubletten {dup} · LLM-Aufrufe {calls}, Tokens {tokens} · übersprungene Batches {failed}",
      "js.runs.estimate": "Plan: {concepts} Konzepte, Ziel {target} Beispiele · ~{calls} LLM-Aufrufe, ~{tokens} Tokens (Budget: {bcalls} Aufrufe / {btokens} Tokens) · Länge {low}-{high} Zeichen.",
      "js.runs.errDry": "Probelauf fehlgeschlagen.", "js.runs.errStart": "Start fehlgeschlagen.",
      "js.runs.cancelling": "Wird abgebrochen — der Lauf stoppt, sobald die laufenden Aufrufe fertig sind.",
      "js.runs.showingLast": "Zeige die {shown} neuesten von {total} Läufen.",
      "js.review.phRun": "— Lauf wählen —",
      "js.review.runOption": "{id} ({status}, {gen} Beispiele)",
      "js.review.noMatch": "Keine Beispiele passen zu diesen Filtern.",
      "js.review.count": "{total} Beispiele, {shown} angezeigt.",
      "js.review.errSamples": "Beispiele konnten nicht geladen werden.",
      "js.review.badge": "{status} · {concept} · sim {sim}",
      "js.review.approve": "Annehmen (a)", "js.review.discard": "Verwerfen (d)",
      "js.review.sampleStatus": "Beispiel {status}.",
      "js.review.confirmRegen": "Verworfene Beispiele neu erzeugen? Das setzt den Lauf fort, um die Plätze angenommener Beispiele nachzufüllen.",
      "js.review.regenStarted": "Neuerzeugung gestartet — lade die Beispiele gleich neu.",
      "js.review.errRegen": "Neu erzeugen fehlgeschlagen.",
      "js.review.errUpdate": "Das Beispiel konnte nicht aktualisiert werden.",
      "js.refine.phDataset": "— Datensatz wählen —",
      "js.refine.noOps": "Noch keine Operationen angewandt — das ist ein Original-Upload.",
      "js.refine.appliedSteps": "Angewandte Schritte: ", "js.refine.fromSource": " (aus {source})",
      "js.refine.sourceCheckbox": " {name} ({rows} Zeilen)",
      "js.refine.row": "{name} — {rows} Zeilen, {cols} Spalten",
      "js.refine.confirmDelete": "Datensatz „{name}“ löschen?",
      "js.refine.chooseDataset": "Wähle zuerst einen Datensatz.", "js.refine.opFailed": "Operation fehlgeschlagen.",
      "js.refine.analyze": "{rows} Zeilen · {labels} Labels · {dupes} exakte Dubletten · {empty} leere Texte · {nolabel} ohne Label · Textlänge {lmin}–{lmax} (Mittel {lmean}) · PII in {pii} Zeilen {counts}",
      "js.refine.preview": "Vorschau", "js.refine.applied": "Angewandt → „{target}“",
      "js.refine.filterResult": "{mode}: {filter} · {before} → {after} Zeilen (entfernt {removed}, geändert {changed}).",
      "js.refine.chainHint": "„{target}“ ist jetzt oben ausgewählt — wende einen weiteren Filter an (Verkettung) oder mache in einem anderen Bereich weiter.",
      "js.refine.removedMark": "— entfernt —",
      "js.refine.preflight": "Effektive Trainingszeilen: {eff} von {raw} roh ({labels} lernbare Labels bei min_samples {min}). Nach Bereinigung behalten {kept}; verworfen {short} zu kurz, {nolabel} ohne Label, {dup} Dubletten.",
      "js.refine.selectSource": "Wähle mindestens einen Quell-Datensatz.",
      "js.refine.errSuggest": "Vorschlag fehlgeschlagen.",
      "js.refine.mapHead": "{name} → Zielspalten", "js.refine.mapNone": "— keine —",
      "js.refine.enterCombineName": "Gib einen Namen für den kombinierten Datensatz ein.",
      "js.refine.suggestFirst": "Erst Zuordnungen vorschlagen.",
      "js.refine.combined": "Kombiniert → „{target}“: {rowsIn} Zeilen rein, {rowsOut} raus ({conflicts} Konflikte gelöst).",
      "js.refine.errCombine": "Kombinieren fehlgeschlagen.",
      "js.refine.enterSplitName": "Gib einen Ausgabenamen für den Split ein.",
      "js.refine.split": "Split „{target}“: {train} train + {holdout} holdout ({pct}% zurückgehalten). Empfohlene min_samples: {rec}.",
      "js.refine.enterApplyName": "Gib einen Namen für den neuen Datensatz ein.",
      "js.refine.choosePush": "Wähle einen Datensatz zum Senden.",
      "js.refine.confirmPush": "Datensatz „{name}“ an das konfigurierte api_v3 senden?",
      "js.refine.pushed": "{pushed} gesendet ({rows} Zeilen) — api_v3: {api}", "js.refine.errPush": "Senden fehlgeschlagen.",
      "js.refine.enterModel": "Gib den api_v3-Modellnamen ein.",
      "js.refine.audit": "{rows} Zeilen geprüft · {flagged} zur Prüfung markiert (Modell widerspricht dem Gold-Label deutlich).",
      "js.refine.enterEnrichName": "Gib einen Namen für den angereicherten Datensatz ein.",
      "js.refine.enriching": "Reichere an…",
      "js.refine.enriched": "{enriched} von {rows} Zeilen angereichert ({mode}) → „{target}“. LLM-Aufrufe: {calls}, Tokens: {tokens}.",
      "js.refine.thLabel": "Label", "js.refine.thSupport": "Belegung",
      "js.refine.thBefore": "vorher", "js.refine.thAfter": "nachher",
      "js.refine.thSource": "Quelle", "js.refine.thRowsIn": "Zeilen rein", "js.refine.thRowsKept": "Zeilen behalten",
      "js.refine.thEffective": "effektive Beispiele", "js.refine.thTrainSupport": "Train-Belegung",
      "js.refine.thGold": "Gold-Label", "js.refine.thModelTop": "Modell-Top",
      "js.refine.thConfidence": "Konfidenz", "js.refine.thRanking": "Modell-Ranking",
      // --- Tabellen-Tab (die generische Tabellen-Werkbank) ---
      "tab.tables": "Tabellen",
      "h2.tables": "Tabellen",
      "intro.tables": "Eine Tabelle als Tabelle bearbeiten — bevor entschieden ist, welche Spalte " +
        "das Label ist. CSV, JSON oder JSONL lesen (auch gepackt), Zeilen ansehen, nach Spaltenwerten " +
        "filtern, Spalten entfernen, Dubletten finden, zwei Dateien verbinden und im gewünschten " +
        "Format ausgeben.",
      "tables.import.h": "Tabelle einlesen",
      "tables.file": "Datei <span class=\"muted\">(CSV, JSON, JSONL — auch .gz)</span>",
      "tables.format": "Format",
      "tables.format.auto": "Automatisch erkennen",
      "tables.separator": "Trennzeichen <span class=\"muted\">(CSV)</span>",
      "tables.encoding": "Zeichensatz",
      "tables.name": "Name <span class=\"muted\">(optional — sonst der Dateiname)</span>",
      "tables.import.btn": "Einlesen",
      "tables.import.helpsum": "Zu verschachteltem JSON",
      "tables.import.help": "Verschachtelte Objekte werden zu Spalten mit Punktpfaden, " +
        "<code>properties.cclom:title</code> ist danach also eine ganz normale Spalte zum Filtern. " +
        "Listen einfacher Werte werden zusammengeführt; eine Liste von Objekten bleibt als " +
        "JSON-Text stehen, weil es dafür keine sinnvolle Spalte gibt. Alles Eingelesene wird als " +
        "Semikolon-CSV gespeichert — jeder weitere Schritt arbeitet dadurch gleich, unabhängig " +
        "davon, woher die Daten kamen.",
      "tables.dataset.h": "Datensatz",
      "tables.dataset": "Datensatz",
      "tables.empty": "Noch keine Datensätze — oben einen einlesen.",
      "tables.export.format": "Ausgeben als",
      "tables.export.csv": "CSV, Semikolon (api_v3)",
      "tables.export.csvgz": "CSV, Semikolon, gzip",
      "tables.export.separator": "Trennzeichen <span class=\"muted\">(CSV)</span>",
      "tables.download.btn": "Herunterladen",
      "tables.view.h": "Zeilen",
      "tables.search": "Suche <span class=\"muted\">(alle sichtbaren Spalten)</span>",
      "tables.columns": "Spalten zeigen <span class=\"muted\">(kommagetrennt; leer = alle)</span>",
      "tables.view.btn": "Zeilen anzeigen",
      "tables.prev": "Zurück",
      "tables.next": "Weiter",
      "tables.profile.h": "Spaltenprofil",
      "tables.profile.help": "Wie voll jede Spalte ist, wie viele verschiedene Werte sie enthält, " +
        "welche überwiegen — und bei Zahlenspalten deren Wertebereich.",
      "tables.profile.btn": "Spalten profilieren",
      "js.tables.working": "Arbeitet…",
      "js.tables.loading": "Lädt…",
      "js.tables.phDataset": "— Datensatz wählen —",
      "js.tables.shape": "{rows} Zeilen × {cols} Spalten",
      "js.tables.chain": "Angewandte Schritte:",
      "js.tables.imported": "Eingelesen: {rows} Zeilen, {cols} Spalten.",
      "js.tables.showing": "Zeige {from}–{to} von {matched} passenden Zeilen ({total} insgesamt).",
      "js.tables.noRows": "Dieser Datensatz hat keine Zeilen.",
      "js.tables.noMatch": "Keine Zeile passt zu dieser Suche.",
      "js.tables.profiled": "{cols} Spalten über {rows} Zeilen.",
      "js.tables.col.name": "Spalte",
      "js.tables.col.filled": "Gefüllt",
      "js.tables.col.distinct": "Verschieden",
      "js.tables.col.kind": "Typ",
      "js.tables.col.top": "Häufigste",
      "js.tables.col.range": "Bereich",
      "js.tables.kind.numeric": "numerisch",
      "js.tables.kind.text": "Text",
      "js.tables.kind.empty": "leer",
      "js.tables.errDatasets": "Die Datensatzliste konnte nicht geladen werden.",
      "js.tables.errImport": "Das Einlesen ist fehlgeschlagen.",
      "js.tables.errRows": "Die Zeilen konnten nicht geladen werden.",
      "js.tables.errProfile": "Die Spalten konnten nicht profiliert werden.",
      "js.tables.errDownload": "Der Download ist fehlgeschlagen.",
    },
  };

  function current() {
    const stored = sessionStorage.getItem(KEY);
    if (stored === "de" || stored === "en") return stored;
    return (navigator.language || "en").toLowerCase().startsWith("de") ? "de" : "en";
  }

  function t(key, params) {
    const table = DICT[current()] || DICT.en;
    let value = table[key] !== undefined ? table[key] : (DICT.en[key] !== undefined ? DICT.en[key] : key);
    if (params) value = value.replace(/\{(\w+)\}/g, (m, k) => (params[k] !== undefined ? String(params[k]) : m));
    return value;
  }

  // Translate a backend status value (running/paused/passed/…); unknown → raw.
  function st(status) {
    const value = t("js.st." + status);
    return value === "js.st." + status ? status : value;
  }

  function apply(lang) {
    const table = DICT[lang] || DICT.en;
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const value = table[el.dataset.i18n];
      if (value !== undefined) el.innerHTML = value;  // trusted in-code constants — see file header
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      const value = table[el.dataset.i18nPlaceholder];
      if (value !== undefined) el.placeholder = value;
    });
    document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
      const value = table[el.dataset.i18nAria];
      if (value !== undefined) el.setAttribute("aria-label", value);
    });
    sessionStorage.setItem(KEY, lang);
  }

  function init() {
    apply(current());
    // Both views (login bar + app top bar) carry a toggle. On change we also
    // notify feature modules so their already-rendered lists re-render in the
    // new language (static [data-i18n] is handled by apply() above).
    document.querySelectorAll("#lang-btn, #lang-btn-app").forEach((btn) =>
      btn.addEventListener("click", () => {
        apply(current() === "de" ? "en" : "de");
        window.dispatchEvent(new Event("dataprep-lang-changed"));
      }));
  }

  return { init, apply, current, t, st };
})();

document.addEventListener("DOMContentLoaded", I18n.init);
