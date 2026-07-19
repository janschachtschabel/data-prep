"""Manual live acceptance run — NOT part of the pytest suite.

Drives the REAL end-to-end vocab-only flow against a running data-prep server,
the real LLM and the real api_v3, culminating in a dataset push. The
deterministic, mocked equivalents live in tests/test_acceptance.py and run in
CI; this script is the human-in-the-loop smoke against live services.

Prerequisites:
- data-prep running (default http://127.0.0.1:8110), OPENAI_API_KEY set,
  config.yaml api_v3.url pointing at a running api_v3 with an admin key in
  DATAPREP_APIV3_KEY.
- A vocabulary URL reachable on the fetch allowlist.

Usage (from data-prep/):
    .venv/Scripts/python scripts/acceptance_live.py \
        --base http://127.0.0.1:8110 --key dev-key \
        --vocab-url https://vocabs.openeduhub.de/w3id.org/openeduhub/vocabs/educationalContext/index.json \
        --per-subject 5 --push
"""

from __future__ import annotations

import argparse
import time

import httpx


def _wait(client: httpx.Client, run_id: str, timeout: float = 600) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/runs/{run_id}").json()
        if state["status"] != "running":
            return state
        print(f"  ... {state['status']} {state['counters']['generated']}/{state['params']['target']}")
        time.sleep(3)
    raise SystemExit("run did not finish in time")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8110")
    ap.add_argument("--key", default="dev-key")
    ap.add_argument("--vocab-url", required=True)
    ap.add_argument("--per-subject", type=int, default=5)
    ap.add_argument("--push", action="store_true", help="push the export to the configured api_v3")
    args = ap.parse_args()

    client = httpx.Client(base_url=args.base, headers={"X-API-Key": args.key}, timeout=600)

    print("1. fetch vocabulary")
    vocab = client.post("/vocabs/fetch", json={"url": args.vocab_url, "name": "acc"}).json()
    print(f"   loaded {vocab['name']}: {vocab['concept_count']} concepts")

    print("2. build vocab-only seed set")
    client.post("/seeds/build", json={"name": "acc", "vocab": "acc", "per_concept": 4}).raise_for_status()

    print("3. start vocab-only run")
    run_id = client.post("/runs", json={
        "seed_set": "acc", "selection": {"mode": "all"},
        "per_concept": args.per_subject, "batch_size": 5, "length_profile": {"name": "standard"},
    }).json()["run_id"]
    state = _wait(client, run_id)
    print(f"   {state['status']}: {state['counters']['generated']} samples, "
          f"{state['usage']['calls']} LLM calls, {state['usage']['tokens_total']} tokens")

    print("4. export + audit")
    csv = client.get(f"/runs/{run_id}/export.csv").text
    audit = client.get(f"/runs/{run_id}/audit.md").text
    assert "source" in csv.splitlines()[0] and "Nutzungshinweise" in audit
    print(f"   CSV {len(csv.splitlines()) - 1} rows; audit warning block present")

    if args.push:
        print("5. push to api_v3")
        pushed = client.post(f"/runs/{run_id}/push").json()
        print(f"   {pushed['pushed']}: {pushed['rows']} rows -> {pushed['api_v3']}")

    print("done. (Remember: evaluate only on a curated holdout - see the audit report.)")


if __name__ == "__main__":
    main()
