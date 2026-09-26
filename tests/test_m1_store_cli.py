import json

from typer.testing import CliRunner

from sparkjury.adapters.tau2 import load_tau2
from sparkjury.adapters.otel import load_otel
from sparkjury.cli import app
from sparkjury.store import TraceStore

runner = CliRunner(env={"COLUMNS": "200"})


def test_store_roundtrip_and_stats(tmp_path, tau2_path, otel_path):
    db = tmp_path / "t.db"
    with TraceStore(db) as store:
        assert store.upsert_traces(load_tau2(tau2_path)) == 12
        assert store.upsert_traces(load_otel(otel_path)) == 2
        assert store.count() == 14
        # idempotent upsert
        store.upsert_traces(load_tau2(tau2_path))
        assert store.count() == 14

        t = store.get("retail_task_001-t0")
        assert t is not None and t.metrics.n_tool_calls == 3
        assert store.get("nope") is None

        failed = store.list(success=False)
        assert {x.trace_id for x in failed} == {
            "retail_task_002-t1", "retail_task_002-t2", "retail_task_003-t1", "retail_task_004-t0",
            "b000000000000001",
        }
        assert len(store.list(task_id="retail_task_002")) == 3

        st = store.stats()
        assert st.n_traces == 14 and st.n_tasks == 6
        assert st.trials_per_task == {3: 4, 1: 2}
        assert st.n_with_gold == 14
        assert abs(st.pass_rate - 9 / 14) < 1e-9
        # pass^k over tasks with >= k trials: k=1 uses all 6 tasks; k=3 uses the 4 tau2 tasks
        assert abs(st.pass_k[1] - 4 / 6) < 1e-9   # first trial passed: t1,t2,t3,A
        assert abs(st.pass_k[3] - 1 / 4) < 1e-9   # only task_001 passes all 3
        assert st.termination["too_many_errors"] == 1 and st.termination["max_steps"] == 1
        assert st.sources == {"tau2": 12, "otel": 2}
        assert st.avg_steps and st.avg_tool_calls is not None

        out = tmp_path / "x.jsonl"
        assert store.export_jsonl(out) == 14
        first = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        assert "trace_id" in first and "steps" in first

        store.put_run("r1", "INGEST", {"n": 14})
        assert store.get_run("r1")["manifest"] == {"n": 14}


def test_cli_ingest_stats_show(tmp_path, tau2_path):
    db = tmp_path / "cli.db"
    r = runner.invoke(app, ["ingest", "--path", str(tau2_path), "--source", "tau2", "--db", str(db)])
    assert r.exit_code == 0, r.output
    assert "ingested 12 traces" in r.output

    r = runner.invoke(app, ["stats", "--db", str(db), "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["n_traces"] == 12 and data["n_tasks"] == 4
    assert abs(data["pass_k"]["3"] - 0.25) < 1e-9

    r = runner.invoke(app, ["show", "retail_task_002-t1", "--db", str(db)])
    assert r.exit_code == 0, r.output
    assert "modify_user_address" in r.output and "success=False" in r.output

    r = runner.invoke(app, ["list", "--db", str(db), "--failed"])
    assert r.exit_code == 0, r.output
    assert "retail_task_004-t0" in r.output

    r = runner.invoke(app, ["show", "missing", "--db", str(db)])
    assert r.exit_code == 1
