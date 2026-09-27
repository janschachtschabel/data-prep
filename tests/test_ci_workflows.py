"""The CI runs its gate once per event, and never publishes an image without it.

Two workflows ran the same gate on every push and pull request -- ci.yml, and docker.yml's
`test` job in front of the image build. On a private repository the Actions minutes are the
account's, shared by every private repository, and they ran out on 2026-09-27: GitHub stopped
starting the jobs at all. The second copy existed for one invariant -- an image is only built
from code that passed the gate, release tags included -- and that invariant is kept by the
dependency between the jobs instead of by running the suite twice.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _workflows() -> dict[str, dict]:
    loaded = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        # YAML 1.1 reads the bare key `on` as the boolean True.
        workflow["on"] = workflow.pop(True, workflow.get("on"))
        loaded[path.name] = workflow
    return loaded


def _runs_the_gate(job: dict) -> bool:
    return any("pytest" in str(step.get("run", "")) for step in job.get("steps", []))


def _publishes_an_image(job: dict) -> bool:
    return any("docker/build-push-action" in str(step.get("uses", "")) for step in job.get("steps", []))


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def test_every_event_runs_the_gate_exactly_once():
    for event in ("push", "pull_request", "workflow_dispatch"):
        gates = [
            f"{name}:{job_id}"
            for name, workflow in _workflows().items()
            if event in workflow["on"]
            for job_id, job in workflow["jobs"].items()
            if _runs_the_gate(job)
        ]
        assert len(gates) == 1, f"a {event} runs the test suite {len(gates)} times: {gates}"


def test_no_image_is_built_without_the_gate():
    builders = 0
    for name, workflow in _workflows().items():
        jobs = workflow["jobs"]
        for job_id, job in jobs.items():
            if _publishes_an_image(job):
                builders += 1
                assert any(_runs_the_gate(jobs[need]) for need in _needs(job)), (
                    f"{name}:{job_id} builds an image without needing the gate"
                )
    assert builders, "no job builds the image any more"


def test_a_release_tag_runs_the_gate_before_its_image():
    """The reason the second gate existed: ci.yml did not run on tags."""
    tagged = [
        workflow for workflow in _workflows().values()
        if "v*.*.*" in ((workflow["on"].get("push") or {}).get("tags") or [])
    ]
    assert tagged, "no workflow runs on a release tag"
    for workflow in tagged:
        jobs = workflow["jobs"].values()
        assert any(_runs_the_gate(job) for job in jobs)
        assert any(_publishes_an_image(job) for job in jobs)
