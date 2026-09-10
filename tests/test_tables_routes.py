"""The table endpoints: run an operation, profile a dataset, download it.

These are the layer below `refine`'s: they never ask which column is the label,
so they work on an export nobody has mapped yet.
"""

from __future__ import annotations

import gzip
import json

HEADERS = {"X-API-Key": "test-key"}

CSV = (
    b"id;titel;jahr;taxonid\n"
    b"1;Bruchrechnen;2015;disc/380\n"
    b"2;Photosynthese;2009;disc/080\n"
    b"3;Wiener Kongress;2020;hoch/1\n"
)


def _client_with_data(make_client, payload: bytes = CSV, name: str = "src"):
    client = make_client()
    r = client.post(
        "/refine/datasets/import",
        files={"file": (f"{name}.csv", payload, "text/csv")},
        data={"name": name}, headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    return client


class TestRunOperation:
    def test_a_rule_filter_previews_without_writing(self, make_client):
        client = _client_with_data(make_client)
        r = client.post("/refine/src/op", headers=HEADERS, json={
            "op": "rules",
            "params": {"rules": [{"column": "jahr", "op": "gte", "value": 2015}]},
        })
        assert r.status_code == 200
        body = r.json()
        assert body["preview"] is True
        assert body["after"] == 2
        assert "kept" not in [d["name"] for d in
                              client.get("/refine/datasets", headers=HEADERS).json()["datasets"]]

    def test_applying_writes_the_target_and_records_the_step(self, make_client):
        client = _client_with_data(make_client)
        r = client.post("/refine/src/op", headers=HEADERS, json={
            "op": "rules", "target": "neuer",
            "params": {"rules": [{"column": "taxonid", "op": "starts_with", "value": "disc/"}]},
        })
        assert r.json()["after"] == 2
        ops = client.get("/refine/neuer/ops", headers=HEADERS).json()["ops"]
        assert [o["filter"] for o in ops] == ["rules"]

    def test_column_operations_run_through_the_same_endpoint(self, make_client):
        client = _client_with_data(make_client)
        r = client.post("/refine/src/op", headers=HEADERS, json={
            "op": "drop_columns", "target": "schlank", "params": {"drop": ["jahr"]},
        })
        assert r.status_code == 200
        assert r.json()["columns_after"] == ["id", "titel", "taxonid"]

    def test_a_pipeline_of_steps_keeps_its_whole_history(self, make_client):
        """The point of the shared apply step, seen from the outside."""
        client = _client_with_data(make_client)
        client.post("/refine/src/op", headers=HEADERS, json={
            "op": "drop_columns", "target": "s1", "params": {"drop": ["jahr"]}})
        client.post("/refine/s1/op", headers=HEADERS, json={
            "op": "rules", "target": "s2",
            "params": {"rules": [{"column": "taxonid", "op": "starts_with", "value": "disc/"}]}})
        ops = client.get("/refine/s2/ops", headers=HEADERS).json()["ops"]
        assert [o["filter"] for o in ops] == ["drop_columns", "rules"]

    def test_an_unknown_operation_lists_the_known_ones(self, make_client):
        client = _client_with_data(make_client)
        r = client.post("/refine/src/op", headers=HEADERS,
                        json={"op": "zaubern", "params": {}})
        assert r.status_code == 400
        assert "rules" in r.json()["detail"]

    def test_a_bad_rule_is_a_400_not_a_500(self, make_client):
        client = _client_with_data(make_client)
        r = client.post("/refine/src/op", headers=HEADERS, json={
            "op": "rules", "params": {"rules": [{"column": "nope", "op": "eq", "value": "x"}]},
        })
        assert r.status_code == 400
        assert "nope" in r.json()["detail"]

    def test_an_unknown_dataset_is_a_404(self, make_client):
        client = _client_with_data(make_client)
        r = client.post("/refine/gibtsnicht/op", headers=HEADERS,
                        json={"op": "rules", "params": {"rules": []}})
        assert r.status_code == 404

    def test_it_needs_a_key(self, make_client):
        client = _client_with_data(make_client)
        assert client.post("/refine/src/op", json={"op": "rules", "params": {}}).status_code == 401


class TestProfile:
    def test_it_describes_every_column(self, make_client):
        client = _client_with_data(make_client)
        r = client.get("/refine/src/profile", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["rows"] == 3
        assert [c["name"] for c in body["columns"]] == ["id", "titel", "jahr", "taxonid"]

    def test_a_numeric_column_reports_its_range(self, make_client):
        client = _client_with_data(make_client)
        jahr = next(c for c in client.get("/refine/src/profile", headers=HEADERS).json()["columns"]
                    if c["name"] == "jahr")
        assert jahr["kind"] == "numeric"
        assert jahr["numeric"]["min"] == 2009

    def test_top_n_is_honoured(self, make_client):
        client = _client_with_data(make_client)
        r = client.get("/refine/src/profile?top_n=1", headers=HEADERS)
        assert all(len(c["top_values"]) <= 1 for c in r.json()["columns"])

    def test_an_unknown_dataset_is_a_404(self, make_client):
        client = _client_with_data(make_client)
        assert client.get("/refine/nix/profile", headers=HEADERS).status_code == 404


class TestDownload:
    def test_the_default_is_the_semicolon_csv_api_v3_reads(self, make_client):
        client = _client_with_data(make_client)
        r = client.get("/refine/src/download", headers=HEADERS)
        assert r.status_code == 200
        assert r.text.splitlines()[0] == "id;titel;jahr;taxonid"

    def test_the_filename_is_offered_for_saving(self, make_client):
        client = _client_with_data(make_client)
        r = client.get("/refine/src/download", headers=HEADERS)
        assert "src.csv" in r.headers["content-disposition"]

    def test_a_comma_separated_csv_is_available(self, make_client):
        client = _client_with_data(make_client)
        r = client.get("/refine/src/download?separator=,", headers=HEADERS)
        assert r.text.splitlines()[0] == "id,titel,jahr,taxonid"

    def test_jsonl_is_available(self, make_client):
        client = _client_with_data(make_client)
        r = client.get("/refine/src/download?format=jsonl", headers=HEADERS)
        first = json.loads(r.text.splitlines()[0])
        assert first["titel"] == "Bruchrechnen"
        assert "src.jsonl" in r.headers["content-disposition"]

    def test_gzip_is_available_and_really_compressed(self, make_client):
        client = _client_with_data(make_client)
        r = client.get("/refine/src/download?format=csv.gz", headers=HEADERS)
        assert gzip.decompress(r.content).decode("utf-8").startswith("id;titel")
        assert "src.csv.gz" in r.headers["content-disposition"]

    def test_an_unsupported_format_is_a_400_naming_what_is_supported(self, make_client):
        client = _client_with_data(make_client)
        r = client.get("/refine/src/download?format=parquet", headers=HEADERS)
        assert r.status_code == 400
        assert "csv" in r.json()["detail"]

    def test_an_unknown_dataset_is_a_404(self, make_client):
        client = _client_with_data(make_client)
        assert client.get("/refine/nix/download", headers=HEADERS).status_code == 404

    def test_it_needs_a_key(self, make_client):
        client = _client_with_data(make_client)
        assert client.get("/refine/src/download").status_code == 401

    def test_a_round_trip_through_download_and_import_is_lossless(self, make_client):
        """The export is only useful if it can come back; this is the whole
        format layer proven end to end."""
        client = _client_with_data(make_client)
        blob = client.get("/refine/src/download?format=jsonl", headers=HEADERS).content
        r = client.post(
            "/refine/datasets/import",
            files={"file": ("back.jsonl", blob, "application/json")},
            data={"name": "zurueck"}, headers=HEADERS,
        )
        assert r.json() == {"name": "zurueck", "rows": 3,
                            "columns": ["id", "titel", "jahr", "taxonid"]}


def test_every_writable_format_has_a_media_type():
    """Pinned as a test rather than a module-level assert: asserts are stripped
    under `python -O`, and a missing entry would then be a 500 instead of a
    clear failure here."""
    from app.routes.tables import _MEDIA_TYPES
    from app.tabular import SUPPORTED_WRITE

    assert set(_MEDIA_TYPES) == set(SUPPORTED_WRITE)


class TestDuplicates:
    DOPPELT = (
        b"url;titel\n"
        b"a.de;Erst\n"
        b"b.de;Zwei\n"
        b"a.de;Drei\n"
        b";Vier\n"
        b";Fuenf\n"
    )

    def test_the_report_counts_groups_and_what_is_removable(self, make_client):
        client = _client_with_data(make_client, self.DOPPELT)
        r = client.get("/refine/src/duplicates?keys=url", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["duplicate_groups"] == 1
        assert body["removable_rows"] == 1
        assert body["empty_key_rows"] == 2

    def test_several_keys_are_passed_as_repeated_parameters(self, make_client):
        client = _client_with_data(make_client, self.DOPPELT)
        r = client.get("/refine/src/duplicates?keys=url&keys=titel", headers=HEADERS)
        assert r.json()["keys"] == ["url", "titel"]
        assert r.json()["duplicate_groups"] == 0

    def test_removing_duplicates_runs_through_the_operation_endpoint(self, make_client):
        client = _client_with_data(make_client, self.DOPPELT)
        r = client.post("/refine/src/op", headers=HEADERS, json={
            "op": "dedupe_keys", "target": "ohne", "params": {"keys": ["url"]}})
        assert r.status_code == 200
        assert r.json()["removed"] == 1
        assert r.json()["after"] == 4  # both empty-URL rows survive

    def test_an_unknown_key_is_a_400(self, make_client):
        client = _client_with_data(make_client, self.DOPPELT)
        r = client.get("/refine/src/duplicates?keys=nope", headers=HEADERS)
        assert r.status_code == 400
        assert "nope" in r.json()["detail"]

    def test_no_key_is_a_400(self, make_client):
        client = _client_with_data(make_client, self.DOPPELT)
        assert client.get("/refine/src/duplicates", headers=HEADERS).status_code == 400

    def test_an_unknown_dataset_is_a_404(self, make_client):
        client = _client_with_data(make_client, self.DOPPELT)
        assert client.get("/refine/nix/duplicates?keys=url", headers=HEADERS).status_code == 404

    def test_it_needs_a_key(self, make_client):
        client = _client_with_data(make_client, self.DOPPELT)
        assert client.get("/refine/src/duplicates?keys=url").status_code == 401
