import json
import time

import pytest
from fastapi.testclient import TestClient

from sparkjury.api import create_app
from sparkjury.api import dgx as dgx_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    app = create_app(tmp_path / "runs", probe_endpoints=False)
    with TestClient(app) as c:
        yield c


def _wait(client, run_id, timeout=60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = client.get(f"/runs/{run_id}").json()
        if not m.get("running") and m.get("status") in ("ok", "failed"):
            return m
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_start_demo_run_and_read_everything(client):
    r = client.post("/runs", json={"demo": True, "run_id": "api-1"})
    assert r.status_code == 202 and r.json()["run_id"] == "api-1"
    # duplicate while running (or finished quickly) -> 409 or accepted second run; only assert on a clearly running one
    m = _wait(client, "api-1")
    assert m["status"] == "ok" and m["stages"]["REPORT"]["status"] == "ok" and "traceback" not in m

    runs = client.get("/runs").json()
    assert runs[0]["run_id"] == "api-1" and runs[0]["status"] == "ok" and runs[0]["n_badcases"] == 5

    # SSE replay of a finished run ends with run_end
    with client.stream("GET", "/runs/api-1/events") as s:
        body = "".join(s.iter_text())
    assert body.startswith("event: run_start") and "event: degraded" in body and body.rstrip().endswith("}")
    assert body.count("event: stage_end") == 7 and "event: run_end" in body
    evs = client.get("/runs/api-1/events.json?since=0").json()
    assert evs[-1]["kind"] == "run_end" and evs[0]["seq"] == 1
    assert len(client.get("/runs/api-1/events.json?since=%d" % (evs[-1]["seq"] - 2)).json()) == 2

    card = client.get("/runs/api-1/card").json()
    assert card["totals"]["n_badcases"] == 5 and card["clusters"][0]["rank"] == 1
    html = client.get("/runs/api-1/card.html")
    assert html.status_code == 200 and "<!DOCTYPE html>" in html.text

    cl = client.get("/runs/api-1/clusters").json()
    assert cl["n_badcases"] == 5 and "badcases" not in cl and cl["clusters"][0]["label"]
    top = cl["clusters"][0]["cluster_id"]
    members = client.get(f"/runs/api-1/traces?cluster={top}").json()
    assert len(members) == cl["clusters"][0]["size"] and members[0]["feature_text"]

    rows = client.get("/runs/api-1/traces").json()
    assert len(rows) == 14 and sum(1 for x in rows if x["env_failure"]) == 1
    failed = client.get("/runs/api-1/traces?failed=true").json()
    assert len(failed) == 5

    d = client.get("/runs/api-1/traces/retail_task_002-t1").json()
    assert d["trace"]["task_id"] == "retail_task_002" and "modify_user_address" in d["transcript"]
    assert d["panel"] and len(d["panel"]["verdicts"]) == 12 and d["decision"]["arbitrations"]
    assert client.get("/runs/api-1/traces/nope").status_code == 404

    # PM confirmation
    assert client.get("/runs/api-1/confirm").json() is None
    c = client.post("/runs/api-1/confirm", json={"cluster_id": top, "note": "start here"}).json()
    assert c["cluster_id"] == top and "sparkjury regress" in c["next"]
    assert client.get("/runs/api-1/confirm").json()["note"] == "start here"
    assert client.post("/runs/api-1/confirm", json={"cluster_id": 999}).status_code == 404

    # regress against itself
    rg = client.get("/runs/api-1/regress?before=api-1").json()
    assert rg["verdict"] == "unchanged" and rg["delta_pass_rate"] == 0


def test_second_run_and_regress_between_runs(client):
    client.post("/runs", json={"demo": True, "run_id": "a"})
    _wait(client, "a")
    r = client.post("/runs", json={"demo": True, "run_id": "b", "stages": ["INGEST", "PRECHECK", "EVALSET", "SCORE", "ARBITRATE", "CLUSTER", "REPORT"], "evalset_limit": 6})
    assert r.status_code == 202
    m = _wait(client, "b")
    assert m["stages"]["EVALSET"]["n_traces"] == 6
    rg = client.get("/runs/b/regress?before=a").json()
    assert rg["n_tasks_common"] == 6 and rg["before_label"] == "a"
    assert client.get("/runs/b/regress?before=zzz").status_code == 404


def test_errors_and_misc(client, tmp_path):
    assert client.post("/runs", json={}).status_code == 400
    assert client.post("/runs", json={"config_path": "missing.toml"}).status_code == 400
    assert client.post("/runs", json={"demo": True, "stages": ["BOGUS"]}).status_code == 400
    assert client.get("/runs/none").status_code == 404
    assert client.get("/runs/none/card").status_code == 404
    assert client.get("/runs/none/events.json").status_code == 404
    h = client.get("/health").json()
    assert h["ok"] and h["version"]
    idx = client.get("/")
    assert idx.status_code == 200 and "SparkJury" in idx.text and "EventSource" in idx.text
    d = client.get("/dgx").json()
    assert "available" in d and "gpus" in d and d["endpoints"] == []


def test_dgx_probe_reports_down_endpoints(monkeypatch):
    eps = dgx_mod.probe_endpoints([{"name": "x", "url": "http://127.0.0.1:9/v1"}], timeout_s=0.3)
    assert eps[0]["ok"] is False and eps[0]["error"] and eps[0]["latency_ms"] >= 0
    monkeypatch.setattr(dgx_mod.shutil, "which", lambda _: None)
    g = dgx_mod.sample_gpus()
    assert g["available"] is False and "nvidia-smi" in g["reason"]


def test_dgx_sample_handles_unified_memory_na(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr(dgx_mod.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(dgx_mod.subprocess, "run", lambda *a, **k: sp.CompletedProcess(a, 0, stdout="0, NVIDIA GB10, [N/A], [N/A], 3, [N/A]\n", stderr=""))
    monkeypatch.setattr(dgx_mod, "_system_memory_mb", lambda: (98000.0, 121000.0))
    g = dgx_mod.sample_gpus()
    assert g["available"] and g["gpus"][0]["unified_memory"] and g["gpus"][0]["mem_total_mb"] == 121000.0
    assert g["gpus"][0]["util_pct"] == 3.0 and g["gpus"][0]["temp_c"] is None
