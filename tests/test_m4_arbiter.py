import json

import httpx
import pytest
from typer.testing import CliRunner

from sparkjury.adapters.otel import load_otel
from sparkjury.adapters.tau2 import load_tau2
from sparkjury.arbiter import Arbiter, JevClient, JevError
from sparkjury.cli import app
from sparkjury.judges import MockJudge, Panel, PanelConfig
from sparkjury.judges.prompts import rubric_levels
from sparkjury.models.arbitration import DecisionSource
from sparkjury.models.verdict import ALL_DIMENSIONS, Dimension, DimensionAgreement, PanelResult, Verdict
from sparkjury.precheck import run_many
from sparkjury.store import TraceStore

runner = CliRunner(env={"COLUMNS": "220"})


# ---- rubric levels feed Jev score questions -------------------------------------------

def test_rubric_levels_cover_0_to_4():
    for d in ALL_DIMENSIONS:
        lv = rubric_levels(d)
        assert len(lv) == 5 and lv[0].startswith("0:") and lv[4].startswith("4:")


# ---- Jev client against a fake HTTP transport --------------------------------------------

def _fake_jev(handler):
    return JevClient(api_key="test-key", transport=httpx.MockTransport(handler), timeout_s=1.0)


def test_jev_request_and_response_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "jev-1.13.0",
            "answers": {
                "score": {"type": "score", "score": 3.4, "legend": {"1": "0: a", "2": "1: b", "3": "2: c", "4": "3: d", "5": "4: e"},
                          "probabilities": {}, "confidence": 0.77},
                "pass": {"type": "noul", "noul": 0.9},
                "kind": {"type": "choice", "choice": "wrong_tool", "confidence": 0.6, "probabilities": {"wrong_tool": 0.6, "loop": 0.4}},
            },
            "usage": {"input_tokens": 10, "output_tokens": 1},
        })

    jev = _fake_jev(handler)
    answers = jev.ask("state text", {
        "score": JevClient.q_score("rate", ["0: a", "1: b", "2: c", "3: d", "4: e"]),
        "pass": JevClient.q_noul("ok?"),
        "kind": JevClient.q_choice("which", {"wrong_tool": "x", "loop": "y"}),
    })
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "jev-latest" and seen["body"]["state"] == "state text"
    assert seen["body"]["questions"]["score"] == {"type": "score", "instructions": "rate", "criteria": ["0: a", "1: b", "2: c", "3: d", "4: e"]}
    assert seen["body"]["questions"]["pass"] == {"type": "noul", "instructions": "ok?"}
    # 1-based legend is normalised to a 0-based level index
    raw, idx, conf = JevClient.parse_score(answers["score"], 5)
    assert abs(raw - 2.4) < 1e-9 and idx == 2 and conf == 0.77
    assert JevClient.parse_noul(answers["pass"]) == 0.9
    assert JevClient.parse_choice(answers["kind"]) == ("wrong_tool", 0.6)
    assert jev.last_usage == {"input_tokens": 10, "output_tokens": 1} and jev.last_latency_ms is not None


def test_jev_errors():
    jev = _fake_jev(lambda r: httpx.Response(503, text="down"))
    with pytest.raises(JevError, match="HTTP 503"):
        jev.ask("s", {"q": JevClient.q_noul("?")})

    def boom(r):
        raise httpx.ConnectTimeout("timeout")
    with pytest.raises(JevError, match="transport"):
        _fake_jev(boom).ask("s", {"q": JevClient.q_noul("?")})
    with pytest.raises(JevError, match="TYPESAFE_API_KEY"):
        JevClient(api_key=None, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))).ask("s", {})
    assert not JevClient(api_key=None).configured


# ---- arbiter decision paths -----------------------------------------------------------------

def _v(judge, dim, score, label=None):
    return Verdict(trace_id="t", judge=judge, model="m", dimension=dim, score=score, label=label)


def _panel(dim, scores, labels=None, agreed=False):
    names = ["a", "b", "c"]
    labels = labels or [None] * 3
    vs = [_v(n, dim, s, l) for n, s, l in zip(names, scores, labels)]
    ag = DimensionAgreement(dimension=dim, scores=dict(zip(names, scores)), labels=dict(zip(names, labels)), agreed=agreed, reason="test")
    return PanelResult(trace_id="t", verdicts=vs, agreement=[ag])


@pytest.fixture
def trace(tau2_path):
    return next(t for t in load_tau2(tau2_path) if t.trace_id == "retail_task_001-t2")


def test_jev_state_text_carries_the_task_requirement_but_not_the_gold(trace):
    seen = {}

    def handler(request):
        seen["state"] = json.loads(request.content)["state"]
        return httpx.Response(200, json={"answers": {
            "score": {"type": "score", "score": 0.0, "legend": {"0": "x"}, "confidence": 0.5},
            "pass": {"type": "noul", "noul": 0.9},
        }})

    trace.task_requirement = "Only exchange the thermostat; leave the keyboard alone."
    Arbiter(jev=_fake_jev(handler), local_judge=MockJudge("local"), audit_rate=0).decide(
        trace, _panel(Dimension.OUTCOME, [4, 0, 4], ["pass", "fail", "pass"]))
    # 分歧交给仲裁时也要带上任务的原始要求，否则仲裁只看对话里被用户模拟器改写过的说法
    assert "Only exchange the thermostat; leave the keyboard alone." in seen["state"]
    assert "Gold final-state outcome" not in seen["state"]     # 金标默认不进仲裁


def test_agreed_dimension_takes_panel_median(trace):
    arb = Arbiter(jev=None, local_judge=MockJudge("local"), audit_rate=0)
    d = arb.decide(trace, _panel(Dimension.SAFETY, [4, 3, 4], agreed=True))
    a = d.arbitrations[0]
    assert a.source == DecisionSource.PANEL and a.final_score == 4 and not a.degraded
    d = arb.decide(trace, _panel(Dimension.OUTCOME, [4, 4, 3], ["pass", "pass", "pass"], agreed=True))
    assert d.outcome_label == "pass" and d.arbitrations[0].final_score == 4


def test_disagreement_goes_to_jev(trace):
    calls = {}

    def handler(request):
        body = json.loads(request.content)
        calls["questions"] = body["questions"]
        calls["state"] = body["state"]
        return httpx.Response(200, json={"answers": {
            "score": {"type": "score", "score": 1.2, "legend": {"0": "x"}, "confidence": 0.8},
            "pass": {"type": "noul", "noul": 0.1},
        }})

    arb = Arbiter(jev=_fake_jev(handler), local_judge=MockJudge("local"), audit_rate=0)
    d = arb.decide(trace, _panel(Dimension.OUTCOME, [4, 0, 4], ["pass", "fail", "pass"]))
    a = d.arbitrations[0]
    assert a.source == DecisionSource.JEV and not a.degraded
    assert a.final_label == "fail" and a.final_score == 1 and a.jev_confidence == 0.8
    assert "pass" in calls["questions"] and calls["questions"]["score"]["type"] == "score"
    assert len(calls["questions"]["score"]["criteria"]) == 5
    assert "Three independent judges disagreed" in calls["state"] and "[1] USER" in calls["state"]
    # score dimension: no noul question
    d = arb.decide(trace, _panel(Dimension.SAFETY, [4, 2, 4]))
    assert "pass" not in calls["questions"] and d.arbitrations[0].final_label is None


def test_jev_failure_falls_back_to_local_degraded(trace):
    arb = Arbiter(jev=_fake_jev(lambda r: httpx.Response(500, text="oops")), local_judge=MockJudge("local"), audit_rate=0)
    a = arb.decide(trace, _panel(Dimension.SAFETY, [4, 2, 4])).arbitrations[0]
    assert a.source == DecisionSource.LOCAL and a.degraded
    assert a.final_score == 2  # local heuristic: cancel without confirmation
    assert "degraded" in a.rationale and "HTTP 500" in a.rationale


def test_no_jev_configured_uses_local(trace):
    arb = Arbiter(jev=JevClient(api_key=None), local_judge=MockJudge("local"), audit_rate=0)
    a = arb.decide(trace, _panel(Dimension.TOOL_USE, [4, 1, 3])).arbitrations[0]
    assert a.source == DecisionSource.LOCAL and a.degraded and "not configured" in a.rationale


def test_everything_failing_uses_panel_median_marked_degraded(trace):
    class Broken:
        name, model = "broken", "b"

        def score(self, t, d):
            return Verdict(trace_id=t.trace_id, judge="broken", model="b", dimension=d, error="down")

    arb = Arbiter(jev=None, local_judge=Broken(), audit_rate=0)
    a = arb.decide(trace, _panel(Dimension.EFFICIENCY, [4, 1, 3])).arbitrations[0]
    assert a.source == DecisionSource.PANEL_FALLBACK and a.degraded and a.final_score == 3
    assert a.error and "local judge error: down" in a.error


def test_audit_sampling_is_deterministic_and_rate_bound(trace):
    arb = Arbiter(jev=None, local_judge=MockJudge("local"), audit_judge=MockJudge("audit"), audit_rate=1.0)
    a = arb.decide(trace, _panel(Dimension.SAFETY, [4, 3, 4], agreed=True)).arbitrations[0]
    assert a.audit_sampled and a.audit_score == 2 and a.audit_disagrees is True  # panel said 4, audit says 2
    arb0 = Arbiter(jev=None, local_judge=MockJudge("local"), audit_judge=MockJudge("audit"), audit_rate=0.0)
    assert not arb0.decide(trace, _panel(Dimension.SAFETY, [4, 3, 4], agreed=True)).arbitrations[0].audit_sampled
    # ~5% of 1000 ids, deterministic across instances
    arb5 = Arbiter(jev=None, audit_rate=0.05)
    picked = [i for i in range(1000) if arb5._is_audit_sample(f"trace-{i}")]
    assert 25 <= len(picked) <= 80
    assert picked == [i for i in range(1000) if Arbiter(jev=None, audit_rate=0.05)._is_audit_sample(f"trace-{i}")]


def test_degraded_decisions_are_always_audited(trace):
    """本地仲裁的决策必须全查，不靠 5% 抽样。

    真批上的证据：judge-loop-real2 与 real6 两次跑批，被抽中的本地仲裁决策 4 条、
    4 条全被审计裁判推翻；同批被抽中的 18 条面板决策 0 条有分歧。只抽 5% 等于
    明知这条路径可疑还基本不查。
    """
    arb = Arbiter(jev=None, local_judge=MockJudge("local"), audit_judge=MockJudge("audit"), audit_rate=0.0)
    a = arb.decide(trace, _panel(Dimension.SAFETY, [4, 2, 4])).arbitrations[0]
    assert a.source == DecisionSource.LOCAL and a.degraded
    assert a.audit_sampled and a.audit_score is not None        # 抽样率为 0 也照查
    # 面板决策仍然只按抽样率查
    b = arb.decide(trace, _panel(Dimension.SAFETY, [4, 3, 4], agreed=True)).arbitrations[0]
    assert not b.degraded and not b.audit_sampled
    # 关掉这个行为就退回老的纯抽样
    off = Arbiter(jev=None, local_judge=MockJudge("local"), audit_judge=MockJudge("audit"),
                  audit_rate=0.0, audit_degraded=False)
    assert not off.decide(trace, _panel(Dimension.SAFETY, [4, 2, 4])).arbitrations[0].audit_sampled
    # 没有真裁判可审计时不该硬造一条审计记录
    none = Arbiter(jev=None, local_judge=MockJudge("local"), audit_judge=None, audit_rate=0.0)
    assert not none.decide(trace, _panel(Dimension.SAFETY, [4, 2, 4])).arbitrations[0].audit_sampled


# ---- store + CLI ----------------------------------------------------------------------

def test_store_and_cli(tmp_path, tau2_path, otel_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    db = tmp_path / "a.db"
    with TraceStore(db) as store:
        store.upsert_traces(load_tau2(tau2_path) + load_otel(otel_path))
        store.put_precheck(run_many(store.list()))
        panel = Panel.from_config(PanelConfig.mock())
        store.put_panel_results(panel.score_many(store.scorable_traces()))
        arb = Arbiter(jev=None, local_judge=panel.judges[0], audit_judge=panel.judges[-1], audit_rate=0.2)
        decisions = arb.decide_many([(store.get(p.trace_id), p) for p in store.list_panel_results()])
        assert store.put_decisions(decisions) == 13
        d = store.get_decision("retail_task_002-t1")
        assert d is not None and len(d.arbitrations) == 4 and d.outcome_label == "fail"
        assert store.get_decision("retail_task_003-t1") is None
        s = store.arbitration_summary()
        assert s["n_traces"] == 13 and s["n_dimensions"] == 52
        assert s["by_source"].get("local", 0) == s["n_degraded"] > 0   # no Jev key -> every disagreement is local+degraded
        assert s["n_outcome_fail"] == 4
        assert len(store.list_decisions()) == 13

    r = runner.invoke(app, ["arbitrate", "--db", str(db), "--jev", "off", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["summary"]["n_traces"] == 13 and data["summary"]["n_degraded"] > 0

    r = runner.invoke(app, ["arbitrate", "--db", str(db)])
    assert r.exit_code == 0, r.output
    assert "decided 13 traces x 52 dimensions" in r.output and "jev: not configured" in r.output

    r = runner.invoke(app, ["arbitrate", "--db", str(tmp_path / "empty.db")])
    assert r.exit_code == 1
