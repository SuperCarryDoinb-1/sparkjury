import json

import pytest
from typer.testing import CliRunner

from sparkjury.adapters.otel import load_otel
from sparkjury.adapters.tau2 import load_tau2
from sparkjury.cli import app
from sparkjury.judges import MockJudge, OpenAICompatJudge, Panel, PanelConfig, decide_agreement
from sparkjury.judges import JudgeSpec
from sparkjury.judges.prompts import build_messages, parse_verdict_json
from sparkjury.models.trace import Role
from sparkjury.models.verdict import ALL_DIMENSIONS, Dimension, Verdict
from sparkjury.precheck import run_many
from sparkjury.store import TraceStore

runner = CliRunner(env={"COLUMNS": "220"})


@pytest.fixture
def traces(tau2_path):
    return {t.trace_id: t for t in load_tau2(tau2_path)}


# ---- prompts & parsing --------------------------------------------------------

def test_prompt_contains_rubric_and_transcript_but_not_the_gold_answer(traces):
    msgs = build_messages(traces["retail_task_001-t2"], Dimension.SAFETY)
    assert msgs[0]["role"] == "system" and "SAFETY" in msgs[0]["content"] and '"score"' in msgs[0]["content"]
    u = msgs[1]["content"]
    assert "cancel_pending_order" in u and "[1] USER" in u
    # 金标默认不进 prompt：把基准算出来的答案交给裁判，outcome 维度就退化成复述，
    # 三家必然一致，这一维也就不再携带任何信息（node-real-samples 上实测 13/13 全一致）。
    assert "Gold outcome" not in u


def test_prompt_includes_gold_only_when_explicitly_asked(traces):
    u = build_messages(traces["retail_task_001-t2"], Dimension.SAFETY, include_gold=True)[1]["content"]
    assert "Gold outcome: SUCCESS" in u


def test_prompt_carries_the_task_requirement_but_never_the_reference_calls(traces):
    t = traces["retail_task_001-t2"]
    # 真实批次的 trace 带任务原始要求；样例文件里没有，先照真实形状补上
    t.task_requirement = "Exchange the keyboard only if a clicky full-size one exists."
    t.outcome.gold = {"reward": 1.0, "db_check": {"db_match": True},
                      "action_checks": [{"action": {"name": "cancel_pending_order",
                                                     "arguments": {"order_id": "#NEVER-LEAK"}}}]}
    u = build_messages(t, Dimension.OUTCOME)[1]["content"]
    assert "Task requirement" in u and "only if a clicky full-size one exists" in u
    # 要求是"用户来干什么"，不是答案：参考调用清单与奖励判定都不能进 prompt
    assert "NEVER-LEAK" not in u and "Gold outcome" not in u and "db_match" not in u


def test_task_requirement_is_absent_from_the_prompt_when_the_source_has_none(traces):
    t = traces["retail_task_001-t2"]
    assert t.task_requirement is None
    assert "Task requirement" not in build_messages(t, Dimension.OUTCOME)[1]["content"]


def test_task_requirement_counts_against_the_transcript_budget(traces):
    t = traces["retail_task_001-t2"]
    t.task_requirement = "R" * 4000
    u = build_messages(t, Dimension.OUTCOME, transcript_max_chars=6000)[1]["content"]
    assert "R" * 4000 in u
    # 预算里要扣掉要求本身的长度，否则整条 prompt 会超过给 16k 上下文裁判留的余量
    assert len(u) <= 4000 + 6000 + 400


def test_transcript_shrinks_to_fit_the_budget_without_losing_step_indices(traces):
    t = max(traces.values(), key=lambda x: len(x.transcript(width=4000)))
    # 样例里的工具返回只有几十字符，"收缩"和"丢弃"分不出来，先造一条远超预算的 trace
    step = next(s for s in reversed(t.steps) if s.role not in (Role.SYSTEM, Role.TOOL))
    step.content = "x" * 9000
    full = t.transcript(width=4000)
    tight = t.transcript(width=4000, max_chars=len(full) // 2, min_width=20)
    assert len(tight) <= len(full) // 2 < len(full)
    # 预算收紧只该让每行变短：步骤编号骨架还在，裁判引用的 evidence_steps 才站得住
    assert len(tight.splitlines()) == sum(1 for s in t.steps if s.role != Role.SYSTEM)
    assert "[1]" in tight


def test_transcript_drops_middle_steps_only_when_the_minimum_width_does_not_fit(traces):
    t = max(traces.values(), key=lambda x: len(x.transcript(width=4000)))
    tiny = t.transcript(width=4000, max_chars=60, min_width=40)
    assert len(tiny) <= 60
    # 丢步必须留痕，否则缺了一截的 transcript 读起来和完整的一模一样
    assert "omitted to fit the prompt budget" in tiny or "transcript omitted" in tiny


def test_arbiter_judges_never_hand_audit_or_arbitration_to_a_mock():
    cfg = PanelConfig(judges=[
        JudgeSpec(name="judge_a", kind="mock", model="mock-qwen"),
        JudgeSpec(name="judge_b", kind="openai", model="nemotron", base_url="http://127.0.0.1:8002/v1"),
        JudgeSpec(name="judge_c", kind="openai", model="step", base_url="https://api.stepfun.com/v1"),
    ])
    local, audit = Panel.from_config(cfg).arbiter_judges()
    assert (local.name, audit.name) == ("judge_b", "judge_c")   # 真裁判优先，mock 不参与仲裁与审计

    # 面板里一个真裁判都没有时，审计应当直接跳过，而不是让 mock 去"审" mock
    local2, audit2 = Panel.from_config(PanelConfig.mock()).arbiter_judges()
    assert local2 is not None and audit2 is None


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
    def __init__(self, replies, finish_reasons=None):
        # 走一遍真构造函数，别手抄默认值：新增字段（prompt 预算等）抄漏了就会在打分时炸
        super().__init__("stub", "stub-model", "http://x", max_tokens=100)
        self._replies = list(replies)
        self._finish_reasons = list(finish_reasons or [])
        self.calls = 0
        self.budgets = []        # 每次调用实际用的 max_tokens，用来断言重问时预算真的变大了

    def _chat(self, messages, max_tokens=None):
        self.calls += 1
        self.budgets.append(max_tokens if max_tokens is not None else self.max_tokens)
        r = self._replies.pop(0)
        # 没说就当作自然写完；真实现里这两个值由后端给（stop / length）
        self._last_finish_reason = self._finish_reasons.pop(0) if self._finish_reasons else "stop"
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
    # 自报 label 与分数矛盾时以分数为准：错误的 label 会盖住真实分歧（真批上出现过）
    j = _Stub(['{"score": 1, "label": "pass", "rationale": "自相矛盾"}'])
    assert j.score(t, Dimension.OUTCOME).label == "fail"
    j = _Stub(['{"score": 4, "label": "fail", "rationale": "自相矛盾"}'])
    assert j.score(t, Dimension.OUTCOME).label == "pass"
    # backend failure -> errored verdict, never an exception
    j = _Stub([RuntimeError("connection refused")])
    v = j.score(t, Dimension.SAFETY)
    assert not v.ok and "connection refused" in v.error and v.score is None


def test_empty_answer_is_retried_with_a_bigger_budget(traces):
    """思维模型把输出预算全花在推理上时，content 是空串（真批实测 32 个维度废掉 17 个）。
    空答案拿同样的预算再问一遍没用，得加大预算 —— 这条守着那次重问和它用的预算。"""
    t = traces["retail_task_001-t0"]
    j = _Stub(["", '{"score": 4, "label": "pass", "confidence": 1.0, "evidence_steps": [3], "rationale": "ok"}'])
    v = j.score(t, Dimension.OUTCOME)
    assert v.ok and v.score == 4
    assert j.budgets == [100, 400]              # 重问时预算放大 4 倍（上限 8000）
    assert v.raw.startswith('{"score"')         # raw 留的是这次真拿到的输出


def test_last_words_of_the_model_survive_a_parse_failure(traces):
    """解析失败时 verdict.raw 要留着模型最后说的话。上次这类失败查不出来，就是因为库里
    只剩一句 no JSON object in judge output，看不到模型到底回了什么。"""
    t = traces["retail_task_001-t0"]
    j = _Stub(["I cannot judge this.", "Still cannot, sorry."])
    v = j.score(t, Dimension.SAFETY)
    assert not v.ok and "no JSON object" in v.error
    assert v.raw == "Still cannot, sorry."      # 不是第一次的那句


def test_a_cut_off_answer_is_retried_with_a_bigger_budget(traces):
    """模型被 token 上限截断时（finish_reason=length），追问不能再用原预算：原预算只会截在
    同一个地方。第一句是没写完的话，进不了 JSON 解析这条路。"""
    t = traces["retail_task_001-t0"]
    j = _Stub(["Let me walk through the transcript step by step. The user first asked",
               '{"score": 3, "label": "pass", "confidence": 0.8, "evidence_steps": [2], "rationale": "ok"}'],
              finish_reasons=["length"])
    v = j.score(t, Dimension.OUTCOME)
    assert v.ok and v.score == 3
    assert j.budgets == [100, 400]              # 追问那一问用的是放大后的预算


def test_an_answer_that_was_not_cut_off_is_nudged_at_the_same_budget(traces):
    """没被截断就是模型自己没照格式写，原预算再问一遍是对的——别白白放大预算拖长判定时间。"""
    t = traces["retail_task_001-t0"]
    j = _Stub(["I think it is fine.",
               '{"score": 3, "confidence": 0.5, "evidence_steps": [], "rationale": "r"}'],
              finish_reasons=["stop"])
    v = j.score(t, Dimension.TOOL_USE)
    assert v.ok and v.score == 3
    assert j.budgets == [100, 100]


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


class _CountingJudge:
    """记录自己同时在飞几个请求的假裁判。"""

    def __init__(self, name: str, delay: float = 0.05):
        self.name, self.model, self.delay = name, f"{name}-model", delay
        self.inflight = self.peak = 0

    def score(self, trace, dimension):
        import time
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            time.sleep(self.delay)
        finally:
            self.inflight -= 1
        return Verdict(trace_id=trace.trace_id, judge=self.name, dimension=dimension,
                       model=self.model, score=4, label="pass")


def test_each_judge_gets_its_own_concurrency(traces):
    """并发按裁判定，不是一个池子跑全面板。

    真批实测：全局 6 路时 judge_a 单条中位 18.9s（它从 1 路加到 4 路解码只从 24 涨到
    29 tok/s），judge_b 10.7s（NVFP4 能到 150 tok/s）；全局 1 路时 3.7s / 3.0s，而整段
    SCORE 只慢 1.31 倍。所以 judge_a 该留在 1 路，judge_b 才值得开高。
    """
    a, b = _CountingJudge("judge_a"), _CountingJudge("judge_b")
    panel = Panel([a, b], workers=1, concurrency={"judge_b": 4})
    r = panel.score(traces["retail_task_001-t2"])
    assert a.peak == 1 and b.peak == 4          # 高并发只给了点名的那个
    assert panel.concurrency == {"judge_a": 1, "judge_b": 4}
    # 判定顺序仍是「按维度、按裁判」，和单池时代一致，下游不该看出区别
    assert [v.dimension for v in r.verdicts] == [d for d in panel.dimensions for _ in panel.judges]
    assert [v.judge for v in r.verdicts[:2]] == ["judge_a", "judge_b"]


def test_panel_config_lets_one_judge_override_the_default(tmp_path):
    p = tmp_path / "judges.toml"
    p.write_text('[panel]\nworkers = 1\n'
                 '[panel.judges]\nname = "judge_a"\nkind = "mock"\n'
                 '[[panel.judges]]\nname = "judge_b"\nkind = "mock"\nconcurrency = 3\n'.replace(
                     '[panel.judges]\nname = "judge_a"\nkind = "mock"\n',
                     '[[panel.judges]]\nname = "judge_a"\nkind = "mock"\n'), encoding="utf-8")
    cfg = PanelConfig.from_toml(p)
    assert [s.concurrency for s in cfg.judges] == [0, 3]
    assert Panel.from_config(cfg).concurrency == {"judge_a": 1, "judge_b": 3}
    # 没写 concurrency 的裁判跟面板默认走
    cfg2 = PanelConfig.model_validate({"workers": 2, "judges": [{"name": "x"}, {"name": "y"}]})
    assert Panel.from_config(cfg2).concurrency == {"x": 2, "y": 2}


def _long_trace(trace, n_steps: int = 40, chars: int = 900):
    """一条够长的 trace：transcript 一开始就顶到 45000 字符的预算上限。"""
    from sparkjury.models.trace import Step, Trace

    steps = [Step(idx=i, role=Role.USER if i % 2 == 0 else Role.ASSISTANT, content="x" * chars)
             for i in range(1, n_steps + 1)]
    return Trace(trace_id="long", source=trace.source, domain=trace.domain, task_id="1", steps=steps)


def test_a_prompt_that_overflows_the_judge_context_is_retried_shorter(traces):
    """真批实测：transcript 预算 45000 字符时，最长的那条 trace 让 16k 上下文的 Nemotron 回 400
    「This model's maximum context length is 16384 tokens」，三个维度各废掉一次判定。

    400 不是空答案，JSON 追问救不了；事先算准预算又要后端的 tokenizer。所以拿错误本身当信号：
    把 transcript 预算减半重问。这里断言提示词真的变短了，而且最终拿到了判定。
    """
    seen: list[int] = []

    class _Overflow(_Stub):
        def _chat(self, messages):
            seen.append(len(messages[-1]["content"]))
            if len(messages[-1]["content"]) > 12000:
                self.calls += 1
                raise RuntimeError("Error code: 400 - This model's maximum context length is 16384 tokens. "
                                   "However, you requested 1200 output tokens and your prompt contains at "
                                   "least 15185 input tokens.")
            return super()._chat(messages)

    j = _Overflow(['{"score": 4, "label": "pass", "rationale": "ok"}'])
    v = j.score(_long_trace(traces["retail_task_001-t0"]), Dimension.OUTCOME)
    assert v.ok and v.score == 4 and v.error is None
    assert len(seen) >= 2 and seen[-1] < seen[0]
    assert seen[-1] <= 12000


def test_a_plain_backend_failure_is_not_retried_as_if_it_were_an_overflow(traces):
    """只有「上下文撑爆」才缩小重问；连不上端点重问几次都是白等，一次就报 errored。"""
    class _Down(_Stub):
        def _chat(self, messages):
            self.calls += 1
            raise RuntimeError("Connection error.")

    j = _Down([])
    v = j.score(traces["retail_task_001-t0"], Dimension.SAFETY)
    assert not v.ok and j.calls == 1 and "Connection error" in v.error


# 下面两条用的是真批上抓到的原始坏例子（judge-loop-real16 里 judge_b 的两条 error），
# 不是编出来的形状：一条多写了一个 `]`，一条在结尾又写了个没值的键。
_STRAY_BRACKET = ('{"score": 4, "label": "pass", "confidence": 0.95, '
                  '"evidence_steps": [["19", "20", "21", "22"]], '
                  '"rationale": "The agent exchanged only the desk lamp."], "label": "pass"}')
_TRAILING_KEY = ('{"score": 3, "label": "pass", "confidence": 0.85, "evidence_steps": [21, 33], '
                 '"rationale": "The agent wasted one step re-verifying the order.", '
                 '"two or three sentences"}')


def test_a_judge_that_emits_a_stray_bracket_still_yields_its_score():
    """`[` 两个而 `]` 三个，整条判定被记成 error，分数其实好好地在里面。只删多余的那个。"""
    assert _STRAY_BRACKET.count("[") == 2 and _STRAY_BRACKET.count("]") == 3
    d = parse_verdict_json(_STRAY_BRACKET)
    assert d["score"] == 4 and d["label"] == "pass" and d["_salvaged"] is True
    assert "desk lamp" in d["rationale"]


def test_a_judge_that_appends_a_valueless_key_still_yields_its_score():
    """`..., "rationale": "...", "two or three sentences"}`：模型把模板里的占位词一起写了出来。
    这种按逗号从后往前截断并闭合，保住最后一个完整字段之前的内容。"""
    d = parse_verdict_json(_TRAILING_KEY)
    assert d["score"] == 3 and d["_salvaged"] is True and "wasted one step" in d["rationale"]


def test_salvaged_verdicts_say_they_were_salvaged(traces):
    """补救来的判定要在 rationale 里说清楚，别让它看起来像原样输出。"""
    j = _Stub([_STRAY_BRACKET])
    v = j.score(traces["retail_task_001-t0"], Dimension.OUTCOME)
    assert v.ok and v.score == 4 and "[salvaged from malformed judge output]" in v.rationale
