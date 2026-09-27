import json

from typer.testing import CliRunner

from sparkjury.adapters.otel import load_otel
from sparkjury.adapters.tau2 import load_tau2
from sparkjury.cli import app
from sparkjury.models.precheck import PrecheckKind
from sparkjury.models.trace import Outcome, Role, Step, ToolCall, ToolResult, Trace, TraceSource
from sparkjury.precheck import PrecheckConfig, run, run_many
from sparkjury.store import TraceStore

runner = CliRunner(env={"COLUMNS": "200"})


def _trace(tid: str, steps: list[Step], termination: str = "user_stop") -> Trace:
    t = Trace(trace_id=tid, source=TraceSource.CUSTOM, task_id="t", steps=steps, outcome=Outcome(success=False, termination_reason=termination))
    t.compute_metrics()
    return t


def _u(i, c="hello"):
    return Step(idx=i, role=Role.USER, content=c)


def _a(i, c="ok", calls=None):
    return Step(idx=i, role=Role.ASSISTANT, content=c, tool_calls=calls or [])


def _call(cid, name="get_order_details"):
    return ToolCall(call_id=cid, name=name, arguments={"order_id": "#W1"})


def _t(i, cid, content, err=True):
    return Step(idx=i, role=Role.TOOL, tool_result=ToolResult(call_id=cid, content=content, is_error=err))


NORMAL = [_u(0, "cancel my order"), _a(1, None, [_call("c1")]), _t(2, "c1", '{"status":"pending"}', err=False), _a(3, "Confirm?"), _u(4, "yes"), _a(5, "done")]


# ---- one trace per rule -------------------------------------------------------

def test_infra_error():
    r = run(_trace("x", NORMAL, termination="infrastructure_error"))
    assert r.kinds == [PrecheckKind.INFRA_ERROR]


def test_timeout_by_termination_and_by_step_latency():
    assert run(_trace("x", NORMAL, termination="timeout")).kinds == [PrecheckKind.TIMEOUT]
    slow = [s.model_copy() for s in NORMAL]
    slow[1] = slow[1].model_copy(update={"latency_ms": 200_000.0})
    r = run(_trace("y", slow))
    assert r.kinds == [PrecheckKind.TIMEOUT] and r.flags[0].evidence_step_idx == 1
    # threshold disabled -> no flag
    assert run(_trace("y", slow), PrecheckConfig(step_latency_ms=None)).flags == []


def test_context_overflow():
    assert run(_trace("x", NORMAL, termination="context_window_exceeded")).kinds == [PrecheckKind.CONTEXT_OVERFLOW]


def test_tool_unavailable_needs_consecutive_same_tool():
    steps = [
        _u(0), _a(1, None, [_call("c1")]), _t(2, "c1", "Error: backend service unavailable (503)"),
        _a(3, "retry", [_call("c2")]), _t(4, "c2", "Error: connection reset"),
    ]
    r = run(_trace("x", steps, termination="too_many_errors"))
    assert r.kinds == [PrecheckKind.TOOL_UNAVAILABLE] and r.flags[0].evidence_step_idx == 2
    # a single 503 is not enough
    assert run(_trace("y", steps[:3])).flags == []
    # two errors on different tools do not count as one outage
    steps2 = [_u(0), _a(1, None, [_call("c1", "a")]), _t(2, "c1", "503"), _a(3, None, [_call("c2", "b")]), _t(4, "c2", "503")]
    assert run(_trace("z", steps2)).flags == []
    # errors that look like validation problems are the agent's fault, not an outage
    steps3 = [_u(0), _a(1, None, [_call("c1")]), _t(2, "c1", "Error: order not found"), _a(3, None, [_call("c2")]), _t(4, "c2", "Error: order not found")]
    assert run(_trace("w", steps3)).flags == []


def test_permission_denied_but_not_agent_auth_failure():
    denied = [_u(0), _a(1, None, [_call("c1")]), _t(2, "c1", "HTTP 403 Forbidden: api key lacks scope orders:write")]
    r = run(_trace("x", denied))
    assert r.kinds == [PrecheckKind.PERMISSION_DENIED] and r.flags[0].evidence_step_idx == 2
    # the agent skipped authentication: that IS the agent's fault, keep it scorable
    agent_fault = [_u(0), _a(1, None, [_call("c1", "cancel_pending_order")]), _t(2, "c1", "permission denied: user not authenticated")]
    assert run(_trace("y", agent_fault)).flags == []


def test_user_sim_broken_empty_and_repeated():
    empty = [_u(0, ""), _a(1), _u(2, None), _a(3)]
    r = run(_trace("x", empty))
    assert r.kinds == [PrecheckKind.USER_SIM_BROKEN] and "empty" in r.flags[0].note
    rep = [_u(0, "hi"), _a(1), _u(2, "hi"), _a(3), _u(4, "hi"), _a(5)]
    r = run(_trace("y", rep))
    assert r.kinds == [PrecheckKind.USER_SIM_BROKEN] and "repeated 3x" in r.flags[0].note
    assert run(_trace("z", rep), PrecheckConfig(user_repeat_min=4)).flags == []


def test_empty_trace():
    assert run(Trace(trace_id="e", source=TraceSource.CUSTOM, task_id="t")).kinds == [PrecheckKind.EMPTY_TRACE]
    assert run(_trace("u", [_u(0, "hello?")])).kinds == [PrecheckKind.EMPTY_TRACE]


def test_normal_trace_has_no_flags():
    assert run(_trace("ok", NORMAL)).flags == []
    assert run(_trace("ok2", NORMAL, termination="max_steps")).flags == []  # agent looping is the agent's fault


# ---- against the sample data -----------------------------------------------------

def test_samples_only_expected_traces_flagged(tau2_path, otel_path):
    traces = load_tau2(tau2_path) + load_otel(otel_path)
    results = {r.trace_id: r for r in run_many(traces)}
    flagged = {tid for tid, r in results.items() if r.is_env_failure}
    # task_003 trial 1: find_user_id_by_email returned 503 / connection reset three times in a row
    assert flagged == {"retail_task_003-t1"}
    assert results["retail_task_003-t1"].kinds == [PrecheckKind.TOOL_UNAVAILABLE]
    assert "failed 3x" in results["retail_task_003-t1"].flags[0].note
    # OTel B ("permission denied: user not authenticated") stays scorable: the agent skipped auth
    assert results["b000000000000001"].flags == []


def test_store_and_cli(tmp_path, tau2_path, otel_path):
    db = tmp_path / "p.db"
    with TraceStore(db) as store:
        store.upsert_traces(load_tau2(tau2_path) + load_otel(otel_path))
        results = run_many(store.list())
        assert store.put_precheck(results) == 14
        assert store.get_precheck("retail_task_003-t1").kinds == [PrecheckKind.TOOL_UNAVAILABLE]
        assert store.get_precheck("retail_task_001-t0").flags == []
        assert [r.trace_id for r in store.list_precheck(env_failure=True)] == ["retail_task_003-t1"]
        scorable = store.scorable_traces()
        assert len(scorable) == 13 and all(t.trace_id != "retail_task_003-t1" for t in scorable)
        s = store.precheck_summary()
        assert s == {"n_checked": 14, "n_env_failures": 1, "n_scorable": 13, "kinds": {"tool_unavailable": 1}}

    r = runner.invoke(app, ["precheck", "--db", str(db), "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["summary"]["n_env_failures"] == 1
    assert data["flagged"][0]["trace_id"] == "retail_task_003-t1"

    r = runner.invoke(app, ["precheck", "--db", str(db)])
    assert r.exit_code == 0, r.output
    assert "1 environment failures, 13 go to judges" in r.output
    assert "tool_unavailable" in r.output
