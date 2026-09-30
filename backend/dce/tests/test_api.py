"""T8.1–T8.3 (subset, D-051): API over persisted runs, background runs, decision log."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dce.api import app as api
from dce.tests.test_payload import runs  # noqa: F401  (module fixture: two runs on tiny_world)


@pytest.fixture(scope="module")
def client(runs: tuple[Any, list[Any]]) -> Iterator[TestClient]:  # noqa: F811
    engine, _ = runs
    api.app.dependency_overrides[api.get_engine] = lambda: engine
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_datasets_and_runs(client: TestClient, runs: tuple[Any, list[Any]]) -> None:  # noqa: F811
    assert client.get("/api/v1/datasets").json() == []  # fixtures hidden by default (rule 6)
    ds = client.get("/api/v1/datasets", params={"include_fixtures": True}).json()
    assert [d["world_id"] for d in ds] == ["tiny_world"]
    assert {r["mode"] for r in ds[0]["runs"]} == {"STABILITY", "GROWTH"}
    rows = client.get("/api/v1/runs", params={"world": "tiny_world", "mode": "GROWTH"}).json()
    assert [r["run_id"] for r in rows] == [runs[1][1].run_id]


def test_run_payload_and_sections(client: TestClient, runs: tuple[Any, list[Any]]) -> None:  # noqa: F811
    rid = runs[1][0].run_id
    full = client.get(f"/api/v1/runs/{rid}").json()
    assert full["run"]["run_id"] == rid
    sec = client.get(f"/api/v1/runs/{rid}/risk").json()
    assert sec["run_id"] == rid and sec["dataset_hash"] and "alerts" in sec["risk"]
    assert client.get(f"/api/v1/runs/{rid}/nope").status_code == 404
    assert client.get("/api/v1/runs/run_missing").status_code == 404


def test_narrative_without_llm_is_template(
    client: TestClient,
    runs: tuple[Any, list[Any]],  # noqa: F811
) -> None:
    n = client.get(f"/api/v1/runs/{runs[1][0].run_id}/narrative").json()
    assert n["source"] == "template" and n["headline"] and n["facts"]


def test_compare_modes(client: TestClient) -> None:
    c = client.get("/api/v1/compare", params={"world": "tiny_world"}).json()
    assert [m["mode"] for m in c["modes"]] == ["STABILITY", "GROWTH"]
    assert all(m["kpis"] and m["allocation"] for m in c["modes"])
    assert client.get("/api/v1/compare", params={"world": "nope"}).status_code == 404


def test_decision_log_roundtrip_and_validation(
    client: TestClient,
    runs: tuple[Any, list[Any]],  # noqa: F811
) -> None:
    rid = runs[1][0].run_id
    body = {
        "run_id": rid,
        "kind": "coman",
        "key": "coman:X:1",
        "summary": "activate co-man",
        "action": "reject",
        "reason": "partner cannot start before February",
    }
    assert client.post("/api/v1/decisions", json=body).status_code == 201
    assert client.post("/api/v1/decisions", json=body | {"action": "accept"}).status_code == 201
    rows = client.get("/api/v1/decisions", params={"run_id": rid}).json()
    assert [r["action"] for r in rows] == ["accept", "reject"]
    assert rows[0]["rec_id"] == f"{rid}:coman:coman:X:1"
    assert client.post("/api/v1/decisions", json=body | {"action": "maybe"}).status_code == 422
    assert client.post("/api/v1/decisions", json=body | {"reason": ""}).status_code == 422
    assert client.post("/api/v1/decisions", json=body | {"run_id": "run_x"}).status_code == 404


def test_background_run_errors(client: TestClient) -> None:
    assert client.post("/api/v1/runs", json={"world_id": "nope"}).status_code == 404
    r = client.post("/api/v1/runs", json={"world_id": "tiny_world", "modes": ["WHATEVER"]})
    assert r.status_code == 422
    assert client.get("/api/v1/jobs/job_9999").status_code == 404


def test_background_run_completes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Job lifecycle with the pipeline stubbed (the real run is covered by test_payload)."""
    from dce import service

    def fake_run_modes(
        engine: Any, world: str, modes: list[str], seed: Any, on_start: Any
    ) -> list[Any]:
        on_start("run_fake", modes[0])
        return [service.RunRecord("run_fake", world, modes[0], "succeeded", None)]

    monkeypatch.setattr(service, "run_modes", fake_run_modes)
    job = client.post("/api/v1/runs", json={"world_id": "tiny_world", "modes": ["GROWTH"]}).json()
    for _ in range(50):
        st = client.get(f"/api/v1/jobs/{job['job_id']}").json()
        if st["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert st["status"] == "succeeded" and st["runs"][0]["run_id"] == "run_fake"


def test_scenarios_resolve_without_touching_base_runs(
    client: TestClient,
    runs: tuple[Any, list[Any]],  # noqa: F811
) -> None:
    """T9.3 executor: levers edit copies of the cached upstream, re-solve, persist as a child run."""
    import numpy as np

    from dce.service import _upstream

    _, recs = runs
    before = {k: v.capacity.paths.copy() for k, v in _upstream.items()}
    base = client.get(f"/api/v1/runs/{recs[0].run_id}").json()
    r = client.post(
        "/api/v1/scenarios",
        json={"world_id": "tiny_world", "levers": {"mode": "STABILITY", "capacity_pct": -60}},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["parent_run_id"] == recs[0].run_id
    assert body["changes"] == ["in-house capacity -60% in weeks 1–13"]
    sc = client.get(f"/api/v1/runs/{body['run_id']}").json()
    assert sc["run"]["kind"] == "scenario" and sc["run"]["levers"]["capacity_pct"] == -60
    assert sc["kpis"]["capacity_planned_kg"] < base["kpis"]["capacity_planned_kg"]
    assert sc["run"]["forecast_hash"] == base["run"]["forecast_hash"]  # demand untouched
    # the cached upstream is never mutated, and scenario runs never replace base plans
    assert all(np.array_equal(before[k], _upstream[k].capacity.paths) for k in before)
    latest = client.get("/api/v1/runs", params={"world": "tiny_world"}).json()
    assert {x["run_id"] for x in latest} == {x.run_id for x in recs}


def test_scenario_errors(client: TestClient) -> None:
    post = lambda body: client.post("/api/v1/scenarios", json=body).status_code  # noqa: E731
    assert post({"world_id": "nope"}) == 404
    assert post({"world_id": "tiny_world", "levers": {"mode": "NOPE"}}) == 422
    assert post({"world_id": "tiny_world", "levers": {"capacity_pct": -99}}) == 422
    assert (
        post({"world_id": "tiny_world", "levers": {"account_id": "X", "account_demand_pct": 50}})
        == 422
    )
    assert (
        post(
            {
                "world_id": "tiny_world",
                "levers": {"mode": "GROWTH", "mode_overrides": {"q_capacity": 0.2}},
            }
        )
        == 422
    )


def test_zero_volume_candidate_equals_baseline(runs: tuple[Any, list[Any]]) -> None:  # noqa: F811
    """ARCH §9.8: onboarding a zero-volume candidate leaves the plan unchanged."""
    import numpy as np

    from dce.ingest import load_dataset, load_history_window
    from dce.onboarding.simulate import Candidate
    from dce.runner import build_run_config, plan_stage
    from dce.service import dataset_for_world, load_upstream

    engine, _ = runs
    ds = dataset_for_world(engine, "tiny_world")
    t = load_dataset(ds.dataset_hash)
    w = load_history_window(ds.dataset_hash, t)
    up = load_upstream(ds.dataset_hash, t, w)
    run = build_run_config("STABILITY")
    region = up.forecast.series["region_id"].drop_nulls()[0]
    zero = Candidate.model_construct(
        volume_kg_per_month=0.0, price_inr_per_kg=600.0, penalty_inr_per_kg=0.0,
        region_id=region, start_month=0, ramp="full", reach=0.5,
    )  # fmt: skip
    base = plan_stage(t, w, run, up)
    with_zero = plan_stage(t, w, run, up, zero)
    assert with_zero.plan.objective == pytest.approx(base.plan.objective, rel=1e-9, abs=1e-6)
    assert np.allclose(with_zero.plan.x, base.plan.x)
    assert np.allclose(with_zero.plan.y[:-1], base.plan.y)


def test_onboarding_api_declines_oversized_and_validates(client: TestClient) -> None:
    region = client.get("/api/v1/datasets", params={"include_fixtures": True}).json()[0]
    payload = client.get(f"/api/v1/runs/{region['runs'][0]['run_id']}").json()
    body: dict[str, Any] = {
        "world_id": "tiny_world",
        "mode": "STABILITY",
        "candidate": {
            "volume_kg_per_month": 1e5,
            "price_inr_per_kg": 700,
            "region_id": payload["run"]["regions"][0],
        },
    }
    r = client.post("/api/v1/onboarding/simulate", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["recommendation"]["class"] in ("decline", "phase")
    assert any("capacity" in x for x in out["recommendation"]["reasons"])
    assert len(out["options"]) >= 3 and out["run_id"]
    assert client.get(f"/api/v1/runs/{out['run_id']}").json()["run"]["levers"]["candidate"]
    bad = body | {"candidate": body["candidate"] | {"volume_kg_per_month": -5}}
    assert client.post("/api/v1/onboarding/simulate", json=bad).status_code == 422
    assert (
        client.post("/api/v1/onboarding/simulate", json=body | {"mode": "NOPE"}).status_code == 422
    )
    assert (
        client.post("/api/v1/onboarding/simulate", json=body | {"world_id": "x"}).status_code == 404
    )
