import json

import pytest
from typer.testing import CliRunner

from sparkjury.adapters.otel import load_otel
from sparkjury.adapters.tau2 import load_tau2
from sparkjury.cli import app
from sparkjury.judges import MockJudge, OpenAICompatJudge, Panel, PanelConfig, decide_agreement
from sparkjury.judges.prompts import build_messages, parse_verdict_json
from sparkjury.models.verdict import ALL_DIMENSIONS, Dimension, Verdict
from sparkjury.precheck import run_many
from sparkjury.store import TraceStore

runner = CliRunner(env={"COLUMNS": "220"})


@pytest.fixture
def traces(tau2_path):
    return {t.trace_id: t for t in load_tau2(tau2_path)}


# ---- prompts & parsing --------------------------------------------------------

def test_prompt_contains_rubric_gold_and_transcript(traces):
    msgs = build_messages(traces["retail_task_001-t2"], Dimension.SAFETY)
    assert msgs[0]["role"] == "system" and "SAFETY" in msgs[0]["content"] and '"score"' in msgs[0]["content"]
    u = msgs[1]["content"]
    assert "Gold outcome: SUCCESS" in u and "cancel_pending_order" in u and "[1] USER" in u


def test_parse_verdict_json_tolerates_fences_and_chatter():
    d = parse_verdict_json('Sure! ```json\n{"score": 3, "label": "pass", "confidence": 0.8, "evidence_steps": [4, 2, 4], "rationale": "ok"}\n```')
    assert d == {"score": 3, "label": "pass", "confidence": 0.8, "evidence_steps": [2, 4], "rationale": "ok"}
    d = parse_verdict_json('Here is my answer: {"score": 1.6, "rationale": "x"} thanks')
    assert d["score"] == 2 and d["label"] is None
    with pytest.raises(ValueError):
        parse_verdict_json('{"score": 9}')
    with pytest.raises(ValueError):
        parse_verdict_json("no json here")
    with pytest.raises(ValueError):
        parse_verdict_json('{"score": 2, "label": "maybe"}')


# ---- heuristic judge on the samples ------------------------------------------------

def test_mock_judge_scores_good_trace_high(traces):
    j = MockJudge("m")
    vs = {d: j.score(traces["retail_task_001-t0"], d) for d in ALL_DIMENSIONS}
    assert vs[Dimension.OUTCOME].label == "pass" and vs[Dimension.OUTCOME].score == 4
    assert vs[Dimension.TOOL_USE].score == 4 and vs[Dimension.EFFICIENCY].score == 4 and vs[Dimension.SAFETY].score == 4
    assert vs[Dimension.SAFETY].label is None


def test_mock_judge_flags_cancel_without_confirmation(traces):
    v = MockJudge("m").score(traces["retail_task_001-t2"], Dimension.SAFETY)
    assert v.score == 2 and v.evidence_steps == [6] and "confirmation" in v.rationale


def test_mock_judge_flags_wrong_tool_loop(traces):
    j = MockJudge("m")
    t = traces["retail_task_002-t1"]
    tool = j.score(t, Dimension.TOOL_USE)
    eff = j.score(t, Dimension.EFFICIENCY)
    saf = j.score(t, Dimension.SAFETY)
    assert tool.score <= 1 and 10 in tool.evidence_steps
    assert eff.score <= 2
    assert saf.score == 0 and "against the user's stated wishes" in saf.rationale
    assert j.score(traces["retail_task_002-t2"], Dimension.EFFICIENCY).score == 0  # max_steps


def test_mock_judge_flags_hallucinated_status(traces):
    j = MockJudge("m")
    t = traces["retail_task_004-t0"]
    assert j.score(t, Dimension.OUTCOME).label == "fail"
    tool = j.score(t, Dimension.TOOL_USE)
    assert tool.score <= 2 and 6 in tool.evidence_steps and "without reading" in tool.rationale
    assert j.score(t, Dimension.SAFETY).score == 1


def test_mock_judge_jitter_is_deterministic(traces):
    a = MockJudge("b", jitter=0.5)
    t = traces["retail_task_003-t0"]
    s1 = [a.score(t, d).score for d in ALL_DIMENSIONS]
    s2 = [a.score(t, d).score for d in ALL_DIMENSIONS]
    assert s1 == s2


# ---- LLM judge with a stubbed backend ------------------------------------------------

class _Stub(OpenAICompatJudge):
    def __init__(self, replies):
        self.name, self.model, self.base_url = "stub", "stub-model", "http://x"
        self.temperature, self.max_tokens, self.extra_body = 0.0, 100, {}
        self._replies = list(replies)
        self.calls = 0

    def _chat(self, messages):
        self.calls += 1
        r = self._replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_openai_judge_parses_and_nudges(traces):
    t = traces["retail_task_001-t0"]
    j = _Stub(['{"score": 4, "label": "pass", "confidence": 0.9, "evidence_steps": [8], "rationale": "done"}'])
    v = j.score(t, Dimension.OUTCOME)
    assert v.ok and v.score == 4 and v.label == "pass" and v.evidence_steps == [8] and v.latency_ms is not None
    # malformed first reply -> nudge once
    j = _Stub(["I think it is fine.", '{"score": 3, "confidence": 0.5, "evidence_steps": [], "rationale": "r"}'])
    v = j.score(t, Dimension.TOOL_USE)
    assert v.ok and v.score == 3 and v.label is None and j.calls == 2
    # outcome without label gets one derived from the score
    j = _Stub(['{"score": 1, "rationale": "bad"}'])
    assert j.score(t, Dimension.OUTCOME).label == "fail"
    # backend failure -> errored verdict, never an exception
    j = _Stub([RuntimeError("connection refused")])
    v = j.score(t, Dimension.SAFETY)
    assert not v.ok and "connection refused" in v.error and v.score is None


# ---- agreement -----------------------------------------------------------------------

def _v(judge, dim, score, label=None, error=None):
    return Verdict(trace_id="t", judge=judge, model="m", dimension=dim, score=score, label=label, error=error)


def test_decide_agreement_rules():
    o = Dimension.OUTCOME
    assert decide_agreement([_v("a", o, 4, "pass"), _v("b", o, 3, "pass"), _v("c", o, 4, "pass")], o).agreed
    a = decide_agreement([_v("a", o, 4, "pass"), _v("b", o, 0, "fail"), _v("c", o, 4, "pass")], o)
    assert not a.agreed and "split" in a.reason
    s = Dimension.SAFETY
    assert decide_agreement([_v("a", s, 4), _v("b", s, 3), _v("c", s, 4)], s).agreed
    assert not decide_agreement([_v("a", s, 4), _v("b", s, 2), _v("c", s, 4)], s).agreed
    assert decide_agreement([_v("a", s, 4), _v("b", s, 2), _v("c", s, 4)], s, tolerance=2).agreed
    # a failed judge means no agreement (needs arbitration or retry)
    r = decide_agreement([_v("a", s, 4), _v("b", s, None, error="boom"), _v("c", s, 4)], s)
    assert not r.agreed
    r = decide_agreement([_v("a", s, 4), _v("b", s, None, error="x"), _v("c", s, None, error="y")], s)
    assert not r.agreed and "usable" in r.reason


# ---- panel end to end ----------------------------------------------------------------

def test_mock_panel_scores_all_dimensions(traces):
    panel = Panel.from_config(PanelConfig.mock())
    results = panel.score_many(traces.values())
    assert len(results) == 12
    for r in results:
        assert len(r.verdicts) == 12 and all(v.ok for v in r.verdicts)
        assert [a.dimension for a in r.agreement] == ALL_DIMENSIONS
    # gold-backed outcome is unanimous for every trace (jitter never flips a gold label)
    assert all(next(a for a in r.agreement if a.dimension == Dimension.OUTCOME).agreed for r in results)
    # jitter produces at least one disagreement somewhere so the arbitration path gets exercised
    assert any(r.needs_arbitration for r in results)


def test_panel_requires_two_judges():
    with pytest.raises(ValueError):
        Panel([MockJudge("only")])


def test_panel_config_from_toml(tmp_path, monkeypatch):
    cfg_path = tmp_path / "j.toml"
    cfg_path.write_text(
        '[panel]\nworkers = 2\n[[panel.judges]]\nname = "judge_a"\nkind = "mock"\njitter = 0.1\n'
        '[[panel.judges]]\nname = "judge_c"\nkind = "openai"\nmodel = "step-3.7-flash"\nbase_url = "http://127.0.0.1:9/v1"\napi_key_env = "STEPFUN_API_KEY"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("STEPFUN_API_KEY", "k")
    cfg = PanelConfig.from_toml(cfg_path)
    assert cfg.workers == 2 and [j.kind for j in cfg.judges] == ["mock", "openai"]
    panel = Panel.from_config(cfg)
    assert isinstance(panel.judges[1], OpenAICompatJudge) and panel.judges[1].model == "step-3.7-flash"


# ---- store + CLI ----------------------------------------------------------------------

def test_store_and_cli(tmp_path, tau2_path, otel_path):
    db = tmp_path / "s.db"
    with TraceStore(db) as store:
        store.upsert_traces(load_tau2(tau2_path) + load_otel(otel_path))
        store.put_precheck(run_many(store.list()))
        panel = Panel.from_config(PanelConfig.mock())
        results = panel.score_many(store.scorable_traces())
        assert store.put_panel_results(results) == 13
        pr = store.get_panel_result("retail_task_001-t2")
        assert pr is not None and len(pr.verdicts) == 12
        assert store.get_panel_result("retail_task_003-t1") is None  # excluded by precheck
        s = store.verdict_summary()
        assert s["n_traces_scored"] == 13 and s["n_verdicts"] == 13 * 12
        assert set(s["judges"]) == {"judge_a", "judge_b", "judge_c"}
        assert s["judges"]["judge_a"]["n_errors"] == 0
        need = store.list_panel_results(needs_arbitration=True)
        assert len(need) == s["n_needing_arbitration"] and all(r.needs_arbitration for r in need)
        # idempotent
        store.put_panel_results(results)
        assert store.verdict_summary()["n_verdicts"] == 13 * 12

    r = runner.invoke(app, ["score", "--db", str(db), "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["summary"]["n_traces_scored"] == 13

    r = runner.invoke(app, ["score", "--db", str(db)])
    assert r.exit_code == 0, r.output
    assert "scored 13 traces, 156 verdicts" in r.output

    r = runner.invoke(app, ["score", "--db", str(db), "--trace", "retail_task_004-t0", "--dims", "safety"])
    assert r.exit_code == 0, r.output
    assert "retail_task_004-t0" in r.output and "safety" in r.output

    r = runner.invoke(app, ["verdicts", "retail_task_002-t1", "--db", str(db)])
    assert r.exit_code == 0, r.output
    assert "judge_a [mock-qwen]" in r.output and "against the user's stated wishes" in r.output

    r = runner.invoke(app, ["verdicts", "nope", "--db", str(db)])
    assert r.exit_code == 1


def test_parse_verdict_json_handles_reasoning_models():
    # thinking block + prose + the object restated at the end
    text = ('<think>Let me look at step 4... the agent cancelled without asking.</think>\n'
            'Here is my assessment.\n{"score": 2, "label": "fail", "confidence": 0.7, "evidence_steps": [4], "rationale": "no confirmation"}\n'
            'Final answer:\n{"score": 2, "label": "fail", "confidence": 0.7, "evidence_steps": [4, 6], "rationale": "no confirmation before cancel"}\n')
    d = parse_verdict_json(text)
    assert d["score"] == 2 and d["evidence_steps"] == [4, 6]
    # braces inside strings and a trailing non-verdict object
    text2 = '{"score": 3, "rationale": "used {get_order_details} correctly"} {"note": "done"}'
    assert parse_verdict_json(text2)["score"] == 3
    # unterminated reasoning tag, object buried in prose
    text3 = "<reasoning>blah\n{\"score\": 4, \"label\": \"pass\", \"evidence_steps\": [], \"rationale\": \"ok\"}"
    assert parse_verdict_json(text3)["label"] == "pass"
    with pytest.raises(ValueError):
        parse_verdict_json("<think>only thoughts</think> nothing else")


def test_parse_verdict_json_repairs_common_slips():
    assert parse_verdict_json('{"score": 2, "label": "fail", "evidence_steps": [6, 10,], "rationale": "trailing comma",}')["score"] == 2
    d = parse_verdict_json('{"score": 3, "rationale": "line one\nline two", "evidence_steps": [1]}')
    assert d["score"] == 3 and "line one" in d["rationale"]
    assert parse_verdict_json('{“score”: 4, “rationale”: “smart quotes”}')["score"] == 4
