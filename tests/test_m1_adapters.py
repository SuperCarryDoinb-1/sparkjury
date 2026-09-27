from sparkjury.adapters import load
from sparkjury.adapters.tau2 import load_tau2, parse_tau2
from sparkjury.adapters.otel import load_otel
from sparkjury.models.trace import Role, TraceSource


def test_tau2_loads_all_simulations(tau2_path):
    traces = load_tau2(tau2_path)
    assert len(traces) == 12
    assert {t.task_id for t in traces} == {f"retail_task_00{i}" for i in range(1, 5)}
    assert all(t.source == TraceSource.TAU2 for t in traces)
    assert all(t.domain == "retail" for t in traces)
    assert all(t.agent_model == "openai/qwen3-8b" for t in traces)


def test_tau2_steps_and_tool_calls(tau2_path):
    by_id = {t.trace_id: t for t in load_tau2(tau2_path)}
    good = by_id["retail_task_001-t0"]
    assert good.outcome.success is True and good.outcome.reward == 1.0
    assert good.outcome.termination_reason == "user_stop"
    names = [tc.name for _, tc in good.iter_tool_calls()]
    assert names == ["find_user_id_by_email", "get_order_details", "cancel_pending_order"]
    # tool result is linked by call id
    res = good.tool_result_for("c1")
    assert res is not None and res.content == "yusuf_rossi_9620" and not res.is_error
    # metrics
    m = good.metrics
    assert m.n_steps == len(good.steps)
    assert m.n_tool_calls == 3 and m.n_tool_errors == 0
    assert m.n_assistant_turns == 6 and m.n_user_turns == 4
    assert m.duration_s == 41.2
    assert m.tokens_in and m.tokens_out


def test_tau2_tool_errors_counted(tau2_path):
    by_id = {t.trace_id: t for t in load_tau2(tau2_path)}
    bad = by_id["retail_task_003-t1"]
    assert bad.outcome.success is False
    assert bad.outcome.termination_reason == "too_many_errors"
    assert bad.metrics.n_tool_errors == 3
    errs = [s for s in bad.steps if s.role == Role.TOOL and s.tool_result.is_error]
    assert "503" in errs[0].tool_result.content


def test_tau2_carries_the_task_requirement_to_the_judge():
    data = {
        "info": {"agent_info": {"llm": "m"}, "environment_info": {"domain_name": "retail"}},
        "tasks": [{
            "id": "t1",
            "user_scenario": {"instructions": {"reason_for_call": "Swap the keyboard, but only if a clicky one exists."}},
            "evaluation_criteria": {"actions": [{"name": "exchange_delivered_order_items",
                                                 "arguments": {"item_ids": ["should-never-reach-a-judge"]}}]},
        }, {"id": "t2", "description": {"purpose": None}}],
        "simulations": [
            {"id": "a", "task_id": "t1", "trial": 0, "messages": [], "reward_info": {"reward": 1.0}},
            {"id": "b", "task_id": "t2", "trial": 0, "messages": [], "reward_info": {"reward": 0.0}},
        ],
    }
    by_id = {t.task_id: t for t in parse_tau2(data)}
    assert by_id["t1"].task_requirement == "Swap the keyboard, but only if a clicky one exists."
    assert by_id["t2"].task_requirement is None      # a task without user_scenario stays empty
    # the reference call list is the answer key and is never copied onto the trace
    assert "should-never-reach-a-judge" not in by_id["t1"].model_dump_json()


def test_tau2_ticks_fallback():
    data = {
        "info": {"agent_info": {"llm": "m"}, "environment_info": {"domain_name": "retail"}},
        "simulations": [{
            "id": "x", "task_id": "t", "trial": 0, "duration": 1.0, "termination_reason": "agent_stop",
            "reward_info": {"reward": 0.0},
            "ticks": [
                {"tick_id": 0, "timestamp": "0", "user_chunk": {"role": "user", "content": "hi"}},
                {"tick_id": 1, "timestamp": "1", "agent_chunk": {"role": "assistant", "content": None},
                 "agent_tool_calls": [{"id": "k1", "name": "get_order_details", "arguments": {"order_id": "#W"}}],
                 "agent_tool_results": [{"id": "k1", "role": "tool", "content": "{}", "error": False}]},
            ],
        }],
    }
    [t] = parse_tau2(data)
    roles = [s.role for s in t.steps]
    assert roles == [Role.USER, Role.ASSISTANT, Role.ASSISTANT, Role.TOOL]
    assert t.metrics.n_tool_calls == 1 and t.outcome.success is False


def test_transcript_is_readable(tau2_path):
    t = load_tau2(tau2_path)[0]
    txt = t.transcript()
    assert "USER:" in txt and "-> find_user_id_by_email(" in txt and "TOOL(c1)" in txt
    assert "SYSTEM" not in txt  # system hidden by default


def test_otel_builds_one_trace_per_invocation(otel_path):
    traces = load_otel(otel_path)
    assert len(traces) == 2
    a = next(t for t in traces if t.task_id == "otel_task_A")
    b = next(t for t in traces if t.task_id == "otel_task_B")
    assert a.source == TraceSource.OTEL and a.domain == "retail" and a.agent_model == "qwen3-8b"
    assert a.outcome.success is True and b.outcome.success is False
    assert b.outcome.termination_reason == "ToolError"
    # A: system, user, assistant(tool_call), tool, assistant(text)
    assert [s.role for s in a.steps] == [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    assert a.steps[2].tool_calls[0].name == "get_order_details"
    assert a.tool_result_for("call_1").content.startswith("{")
    assert a.metrics.n_tool_calls == 1 and a.metrics.n_tool_errors == 0
    assert a.metrics.duration_s == 6.0
    assert a.steps[2].tokens_in == 812
    # B: permission-denied tool error is flagged
    assert b.metrics.n_tool_errors == 1
    assert "permission denied" in b.tool_result_for("call_9").content


def test_dispatch_by_source(tau2_path, otel_path):
    assert len(load(tau2_path, "tau2")) == 12
    assert len(load(otel_path, TraceSource.OTEL)) == 2


def test_tau2_keeps_the_benchmark_s_own_failure_reason():
    """90 条真批里丢了 26 条，卡片只说得出「empty_trace=26」这个规则名，读的人分不清是评测链路
    自己死的还是被测 Agent 崩的——这两种要做的处置正好相反。原因就写在 tau2 自己的 info 里。"""
    data = {
        "info": {"agent_info": {"llm": "m"}},
        "tasks": [{"id": "t1"}],
        "simulations": [
            {"id": "lost", "task_id": "t1", "trial": 0, "messages": [], "duration": 0.0,
             "termination_reason": "infrastructure_error",
             "info": {"error_type": "InternalServerError", "failed_after_attempts": 4}},
            {"id": "clean", "task_id": "t1", "trial": 1, "messages": [{"role": "user", "content": "hi"}],
             "termination_reason": "user_stop"},
        ],
    }
    by_id = {t.trace_id: t for t in parse_tau2(data)}
    assert by_id["lost"].failure_cause == "InternalServerError after 4 attempts"
    assert by_id["clean"].failure_cause is None       # 没记录原因就不编一个
