"""Static admin UI: mount, cache policy, and the login/app shell markers."""

from __future__ import annotations

import re


def test_every_i18n_key_has_en_and_de_translation(make_client):
    """No half-translated UI: every data-i18n / data-i18n-placeholder key used in
    the markup must resolve in BOTH language tables."""
    client = make_client(auth_key="secret-1")
    html = client.get("/ui/").text
    js = client.get("/ui/i18n.js").text

    html_keys = set(re.findall(r'data-i18n(?:-placeholder)?="([^"]+)"', html))
    en_block = js.split("en: {", 1)[1].split("de: {", 1)[0]
    de_block = js.split("de: {", 1)[1]
    en_keys = set(re.findall(r'"([\w.-]+)":', en_block))
    de_keys = set(re.findall(r'"([\w.-]+)":', de_block))

    assert not (html_keys - en_keys), f"keys missing English: {sorted(html_keys - en_keys)}"
    assert not (html_keys - de_keys), f"keys missing German: {sorted(html_keys - de_keys)}"


def test_every_js_i18n_t_key_has_en_and_de_translation(make_client):
    """Dynamic strings: every I18n.t("...") key used in a JS module must resolve
    in BOTH language tables (static keys reused from markup count too)."""
    client = make_client(auth_key="secret-1")
    js_i18n = client.get("/ui/i18n.js").text
    en_block = js_i18n.split("en: {", 1)[1].split("de: {", 1)[0]
    de_block = js_i18n.split("de: {", 1)[1]
    en_keys = set(re.findall(r'"([\w.-]+)":', en_block))
    de_keys = set(re.findall(r'"([\w.-]+)":', de_block))

    # Derived from index.html rather than hardcoded: a fixed list silently stops
    # covering new modules, and this test then passes without checking them.
    # tables.js was added while the list still named eight files.
    html = client.get("/ui/").text
    modules = re.findall(r'<script src="([\w.-]+\.js)"', html)
    assert len(modules) >= 8, f"expected the UI's script tags, found {modules}"

    used: set[str] = set()
    for module in modules:
        src = client.get(f"/ui/{module}").text
        used |= set(re.findall(r'I18n\.t\(\s*"([\w.-]+)"', src))
    assert used, "no I18n.t keys found — did the wiring regress?"
    assert not (used - en_keys), f"js keys missing English: {sorted(used - en_keys)}"
    assert not (used - de_keys), f"js keys missing German: {sorted(used - de_keys)}"


def test_ui_serves_index_html(make_client):
    client = make_client(auth_key="secret-1")
    r = client.get("/ui/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # Shell markers the app boot depends on: login bar, app view, all six tabs.
    for marker in ("login-form", "view-app", "data-tab=\"guide\"", "data-tab=\"runs\"",
                   "data-tab=\"review\"", "data-tab=\"seeds\"", "data-tab=\"vocabularies\"",
                   "data-tab=\"references\"", "data-tab=\"refine\""):
        assert marker in r.text, f"missing UI marker: {marker}"


def test_ui_has_guide_tab_covering_the_three_workflows(make_client):
    """A start/help landing that walks the AI-only, hybrid, and clean-up flows."""
    client = make_client(auth_key="secret-1")
    html = client.get("/ui/").text
    assert 'id="tab-guide"' in html and 'data-i18n="tab.guide"' in html
    for key in ("guide.ai.h", "guide.hybrid.h", "guide.clean.h"):
        assert f'data-i18n="{key}"' in html, f"missing guide section: {key}"
    # Guide is the default landing: it is the tab marked current.
    assert 'data-tab="guide" aria-current="page"' in html


def test_ui_assets_always_revalidate(make_client):
    """api_v3 lesson: without no-cache, browsers keep executing a stale app.js."""
    client = make_client(auth_key="secret-1")
    assert client.get("/ui/").headers.get("Cache-Control") == "no-cache"
    assert client.get("/ui/app.js").headers.get("Cache-Control") == "no-cache"
    assert client.get("/health").headers.get("Cache-Control") != "no-cache"


def test_ui_can_be_disabled(make_client):
    client = make_client(auth_key="secret-1", ui_enabled="false")
    assert client.get("/ui/").status_code == 404


def test_ui_has_language_switcher(make_client):
    """A German/English toggle over the navigation shell (data-i18n + i18n.js)."""
    client = make_client(auth_key="secret-1")
    html = client.get("/ui/").text
    assert 'src="i18n.js"' in html
    assert 'data-i18n="tab.runs"' in html and 'data-i18n="h2.refine"' in html
    assert 'id="lang-btn"' in html

    js = client.get("/ui/i18n.js").text
    assert '"de"' in js and "Läufe" in js and "Aufbereiten" in js  # nav headings (German)
    # Coverage now reaches help texts and form labels, not just menu headings.
    assert "Neuer Lauf" in js          # a form/button label
    assert "Metadatenfeld" in js       # a help/label with the (optional) hint
    assert 'data-i18n-placeholder' in client.get("/ui/").text  # placeholders translate too


def test_css_declares_color_scheme_and_themes_selects(make_client):
    """Native <select>/options went dark-on-dark: fix pins color-scheme + an
    explicit color on selects so they follow the theme."""
    client = make_client(auth_key="secret-1")
    css = client.get("/ui/style.css").text
    # The 'color-scheme' PROPERTY (not the prefers-color-scheme media feature)
    # makes native controls (option lists, scrollbars) follow the theme.
    assert "color-scheme: dark" in css or "color-scheme: light" in css
    # select must be covered by the color-inherit rule so its text is themed.
    assert "input, button, textarea, select" in css


def test_seed_term_bank_columns_show_default_hints(make_client):
    """The term-bank column fields must show the WLO default column names, so it
    is clear what to enter (and it stays editable)."""
    client = make_client(auth_key="secret-1")
    html = client.get("/ui/").text
    assert 'placeholder="properties.cclom:general_keyword"' in html
    assert "properties.cclom:title, properties.cclom:general_description" in html


def test_ui_clarifies_which_values_belong_in_the_metadata_field(make_client):
    """The taxonid confusion: Schulfächer vs Hochschulfächer share the field but
    not the value set — the UI must say so and offer a concrete field example."""
    client = make_client(auth_key="secret-1")
    html = client.get("/ui/").text
    assert 'data-i18n="vocab.presets.note"' in html
    assert 'placeholder="properties.ccm:taxonid"' in html


def test_run_guidance_field_has_a_german_default(make_client):
    """The context field ships with an editable default so a run is steered out
    of the box (German school-subject catalog entries)."""
    client = make_client(auth_key="secret-1")
    html = client.get("/ui/").text
    assert "Erzeuge realistische Lernmaterial-Katalogeinträge" in html


def test_ui_runs_tab_explains_output_and_offers_guidance(make_client):
    """A run must explain what it generates (fields + classification label) and
    let the user steer vocab-only generation with free-text context."""
    client = make_client(auth_key="secret-1")
    html = client.get("/ui/").text
    assert 'id="run-guidance"' in html
    assert 'data-i18n="run.explain.summary"' in html


def test_ui_offers_manual_entry_and_turtle_upload(make_client):
    """Fallbacks when a JSON-LD link is not at hand: paste a concept list, or
    upload SkoHub Turtle."""
    client = make_client(auth_key="secret-1")
    html = client.get("/ui/").text
    assert 'id="vocab-manual-form"' in html and 'id="vocab-manual-text"' in html
    assert ".ttl" in html  # the upload file input accepts Turtle now


def test_ui_offers_vocabulary_presets_with_metadata_fields(make_client):
    """One-click default vocabularies for the common WLO facets, each carrying
    its associated metadata field so it is pre-filled on fetch."""
    client = make_client(auth_key="secret-1")
    html = client.get("/ui/").text
    for url in (
        "vocabs/educationalContext/index.json",
        "vocabs/discipline/index.json",
        "vocabs/hochschulfaechersystematik/index.json",
        "vocabs/intendedEndUserRole/index.json",
        "vocabs/new_lrt/index.json",
        "vocabs/new_lrt_aggregated/index.json",
    ):
        assert url in html, f"missing preset: {url}"
    for field in ("properties.ccm:taxonid", "properties.ccm:educationalcontext",
                  "properties.ccm:oeh_lrt", "properties.ccm:oeh_lrt_aggregated",
                  "properties.ccm:educationalintendedenduserrole"):
        assert field in html, f"missing preset field: {field}"
    assert 'id="vocab-field"' in html  # the fetch form has a metadata-field input


def test_every_column_kind_the_profile_returns_has_a_translation(make_client):
    """tables.js builds these keys dynamically (`js.tables.kind.${c.kind}`), so
    the regex-based parity test above cannot see them. They are enumerated here
    against the values profile_columns can actually return, which ties the UI's
    labels to the backend rather than to a guess."""
    client = make_client(auth_key="secret-1")
    js_i18n = client.get("/ui/i18n.js").text
    en_block = js_i18n.split("en: {", 1)[1].split("de: {", 1)[0]
    de_block = js_i18n.split("de: {", 1)[1]

    for kind in ("numeric", "text", "empty"):
        key = f'"js.tables.kind.{kind}"'
        assert key in en_block, f"{key} missing English"
        assert key in de_block, f"{key} missing German"
