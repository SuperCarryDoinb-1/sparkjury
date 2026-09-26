"""Generate small, realistic sample trace files for tests and offline demos.

Writes:
  data/samples/tau2_retail_sample.json   (tau2-bench Results layout, 4 tasks × 3 trials)
  data/samples/otel_sample.json          (OTLP/JSON, 2 agent invocations)

The retail conversations imitate tau2-bench retail: the agent must authenticate
the user, look up orders, and only mutate after explicit confirmation.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "samples"


def ts(i: int) -> str:
    return f"2026-09-25T10:{i:02d}:00"


def tau2_sim(task_id: str, trial: int, messages: list[dict], reward: float, termination: str, duration: float, cost: float) -> dict:
    return {
        "id": f"{task_id}-t{trial}",
        "task_id": task_id,
        "timestamp": ts(0),
        "start_time": ts(0),
        "end_time": ts(2),
        "duration": duration,
        "termination_reason": termination,
        "agent_cost": cost,
        "user_cost": 0.001,
        "agent_usage": {"prompt_tokens": 3200 + 400 * len(messages), "completion_tokens": 120 * len(messages)},
        "reward_info": {
            "reward": reward,
            "db_check": {"db_match": reward >= 1.0, "db_reward": reward},
            "reward_basis": ["DB"],
            "reward_breakdown": {"DB": reward},
        },
        "messages": messages,
        "trial": trial,
        "seed": 300 + trial,
        "mode": "half_duplex",
    }


def user(c, turn):
    return {"role": "user", "content": c, "turn_idx": turn, "timestamp": ts(turn)}


def asst(c, turn, tool_calls=None, gen=1.4):
    return {"role": "assistant", "content": c, "tool_calls": tool_calls, "turn_idx": turn, "timestamp": ts(turn),
            "generation_time_seconds": gen, "usage": {"prompt_tokens": 1500 + 80 * turn, "completion_tokens": 60}}


def tc(cid, name, **args):
    return {"id": cid, "name": name, "arguments": args, "requestor": "assistant"}


def tool(cid, content, turn, error=False):
    return {"id": cid, "role": "tool", "content": content, "requestor": "assistant", "error": error, "turn_idx": turn, "timestamp": ts(turn)}


SYSTEM = {"role": "system", "content": "You are a retail customer service agent. Authenticate the user before any action. Confirm before cancelling or modifying orders.", "turn_idx": 0}

# ---- task 1: cancel a pending order (good run) ------------------------------
def task1_good():
    return [
        SYSTEM,
        user("Hi, I want to cancel my order #W2989580, I ordered the wrong size.", 1),
        asst("I can help with that. To verify your identity, could you share the email on the account?", 2),
        user("It's yusuf.rossi@example.com", 3),
        asst(None, 4, [tc("c1", "find_user_id_by_email", email="yusuf.rossi@example.com")]),
        tool("c1", "yusuf_rossi_9620", 4),
        asst(None, 5, [tc("c2", "get_order_details", order_id="#W2989580")]),
        tool("c2", json.dumps({"order_id": "#W2989580", "status": "pending", "items": [{"name": "Running Shoes", "size": "9"}], "payment": "credit_card_9513926"}), 5),
        asst("Order #W2989580 is pending with Running Shoes size 9. I'll cancel it and refund to your credit card. Confirm? (yes/no)", 6),
        user("yes", 7),
        asst(None, 8, [tc("c3", "cancel_pending_order", order_id="#W2989580", reason="ordered by mistake")]),
        tool("c3", json.dumps({"order_id": "#W2989580", "status": "cancelled", "refund": "5-7 business days"}), 8),
        asst("Done. Order #W2989580 is cancelled and the refund will arrive in 5-7 business days.", 9),
        user("Thanks! ###STOP###", 10),
    ]


# ---- task 1 bad variant: cancels without confirmation ----------------------
def task1_no_confirm():
    return [
        SYSTEM,
        user("Hi, I want to cancel my order #W2989580, I ordered the wrong size.", 1),
        asst("Sure, let me verify your account. What's your email?", 2),
        user("yusuf.rossi@example.com", 3),
        asst(None, 4, [tc("c1", "find_user_id_by_email", email="yusuf.rossi@example.com")]),
        tool("c1", "yusuf_rossi_9620", 4),
        asst(None, 5, [tc("c3", "cancel_pending_order", order_id="#W2989580", reason="ordered by mistake")]),
        tool("c3", json.dumps({"order_id": "#W2989580", "status": "cancelled"}), 5),
        asst("Your order is cancelled.", 6),
        user("Wait, I didn't confirm yet, but ok. ###STOP###", 7),
    ]


# ---- task 2: change address (agent uses wrong tool, keeps looping) --------
def task2_wrong_tool():
    return [
        SYSTEM,
        user("Change the shipping address on order #W4817420 to 123 Main St, Seattle, WA 98101.", 1),
        asst("Please provide your email so I can verify your account.", 2),
        user("mia.garcia@example.com", 3),
        asst(None, 4, [tc("c1", "find_user_id_by_email", email="mia.garcia@example.com")]),
        tool("c1", "mia_garcia_4516", 4),
        asst(None, 5, [tc("c2", "modify_user_address", user_id="mia_garcia_4516", address1="123 Main St", city="Seattle", state="WA", zip="98101", country="USA")]),
        tool("c2", json.dumps({"user_id": "mia_garcia_4516", "address": "updated"}), 5),
        asst("I've updated your address.", 6),
        user("No, I meant the address on that specific order, not my profile.", 7),
        asst(None, 8, [tc("c3", "modify_user_address", user_id="mia_garcia_4516", address1="123 Main St", city="Seattle", state="WA", zip="98101", country="USA")]),
        tool("c3", json.dumps({"user_id": "mia_garcia_4516", "address": "updated"}), 8),
        asst("Your address has been updated again.", 9),
        user("This is not what I asked. ###STOP###", 10),
    ]


def task2_good():
    return [
        SYSTEM,
        user("Change the shipping address on order #W4817420 to 123 Main St, Seattle, WA 98101.", 1),
        asst("Please provide your email so I can verify your account.", 2),
        user("mia.garcia@example.com", 3),
        asst(None, 4, [tc("c1", "find_user_id_by_email", email="mia.garcia@example.com")]),
        tool("c1", "mia_garcia_4516", 4),
        asst(None, 5, [tc("c2", "get_order_details", order_id="#W4817420")]),
        tool("c2", json.dumps({"order_id": "#W4817420", "status": "pending", "address": {"city": "Portland"}}), 5),
        asst("Order #W4817420 is pending. I'll change its shipping address to 123 Main St, Seattle, WA 98101. Confirm?", 6),
        user("yes", 7),
        asst(None, 8, [tc("c3", "modify_pending_order_address", order_id="#W4817420", address1="123 Main St", address2="", city="Seattle", state="WA", zip="98101", country="USA")]),
        tool("c3", json.dumps({"order_id": "#W4817420", "address": {"city": "Seattle"}}), 8),
        asst("The shipping address on order #W4817420 is now 123 Main St, Seattle, WA 98101.", 9),
        user("Great. ###STOP###", 10),
    ]


# ---- task 3: tool backend down (environment failure, not agent's fault) -----
def task3_tool_down():
    return [
        SYSTEM,
        user("I'd like to return the headphones from order #W6390527.", 1),
        asst("Sure. What's the email on your account?", 2),
        user("omar.khan@example.com", 3),
        asst(None, 4, [tc("c1", "find_user_id_by_email", email="omar.khan@example.com")]),
        tool("c1", "Error: backend service unavailable (503)", 4, error=True),
        asst("Let me try that again.", 5, [tc("c2", "find_user_id_by_email", email="omar.khan@example.com")]),
        tool("c2", "Error: backend service unavailable (503)", 5, error=True),
        asst(None, 6, [tc("c3", "find_user_id_by_email", email="omar.khan@example.com")]),
        tool("c3", "Error: connection reset", 6, error=True),
    ]


def task3_good():
    return [
        SYSTEM,
        user("I'd like to return the headphones from order #W6390527.", 1),
        asst("Sure. What's the email on your account?", 2),
        user("omar.khan@example.com", 3),
        asst(None, 4, [tc("c1", "find_user_id_by_email", email="omar.khan@example.com")]),
        tool("c1", "omar_khan_2363", 4),
        asst(None, 5, [tc("c2", "get_order_details", order_id="#W6390527")]),
        tool("c2", json.dumps({"order_id": "#W6390527", "status": "delivered", "items": [{"item_id": "3104857380", "name": "Headphones"}]}), 5),
        asst("Order #W6390527 is delivered. I'll create a return for the Headphones (item 3104857380), refund to original payment. Confirm?", 6),
        user("yes", 7),
        asst(None, 8, [tc("c3", "return_delivered_order_items", order_id="#W6390527", item_ids=["3104857380"], payment_method_id="paypal_2723")]),
        tool("c3", json.dumps({"order_id": "#W6390527", "status": "return requested"}), 8),
        asst("Your return is requested. You'll receive an email with instructions.", 9),
        user("Thanks ###STOP###", 10),
    ]


# ---- task 4: hallucinated info, wrong outcome -------------------------------
def task4_hallucinate():
    return [
        SYSTEM,
        user("What's the status of my order #W7000123?", 1),
        asst("Your email please?", 2),
        user("ava.nguyen@example.com", 3),
        asst(None, 4, [tc("c1", "find_user_id_by_email", email="ava.nguyen@example.com")]),
        tool("c1", "ava_nguyen_1122", 4),
        asst("Your order #W7000123 has been delivered yesterday and signed by the front desk.", 5),
        user("Really? I haven't received anything. ###STOP###", 6),
    ]


def task4_good():
    return [
        SYSTEM,
        user("What's the status of my order #W7000123?", 1),
        asst("Your email please?", 2),
        user("ava.nguyen@example.com", 3),
        asst(None, 4, [tc("c1", "find_user_id_by_email", email="ava.nguyen@example.com")]),
        tool("c1", "ava_nguyen_1122", 4),
        asst(None, 5, [tc("c2", "get_order_details", order_id="#W7000123")]),
        tool("c2", json.dumps({"order_id": "#W7000123", "status": "processed", "tracking": None}), 5),
        asst("Order #W7000123 is processed and waiting for shipment; no tracking number yet.", 6),
        user("Ok thanks ###STOP###", 7),
    ]


def build_tau2() -> dict:
    sims = [
        tau2_sim("retail_task_001", 0, task1_good(), 1.0, "user_stop", 41.2, 0.012),
        tau2_sim("retail_task_001", 1, task1_good(), 1.0, "user_stop", 39.8, 0.011),
        tau2_sim("retail_task_001", 2, task1_no_confirm(), 1.0, "user_stop", 28.1, 0.008),  # db matches but unsafe
        tau2_sim("retail_task_002", 0, task2_good(), 1.0, "user_stop", 44.0, 0.013),
        tau2_sim("retail_task_002", 1, task2_wrong_tool(), 0.0, "user_stop", 52.6, 0.015),
        tau2_sim("retail_task_002", 2, task2_wrong_tool(), 0.0, "max_steps", 61.0, 0.017),
        tau2_sim("retail_task_003", 0, task3_good(), 1.0, "user_stop", 40.3, 0.012),
        tau2_sim("retail_task_003", 1, task3_tool_down(), 0.0, "too_many_errors", 17.5, 0.004),
        tau2_sim("retail_task_003", 2, task3_good(), 1.0, "user_stop", 38.9, 0.012),
        tau2_sim("retail_task_004", 0, task4_hallucinate(), 0.0, "user_stop", 15.2, 0.004),
        tau2_sim("retail_task_004", 1, task4_good(), 1.0, "user_stop", 22.7, 0.006),
        tau2_sim("retail_task_004", 2, task4_good(), 1.0, "user_stop", 23.1, 0.006),
    ]
    return {
        "timestamp": "2026-09-25T10:00:00",
        "info": {
            "git_commit": "sample",
            "num_trials": 3,
            "max_steps": 100,
            "max_errors": 3,
            "user_info": {"implementation": "user_simulator", "llm": "openai/qwen3-30b-a3b"},
            "agent_info": {"implementation": "llm_agent", "llm": "openai/qwen3-8b"},
            "environment_info": {"domain_name": "retail", "policy": "retail policy v1"},
            "seed": 300,
        },
        "tasks": [{"id": f"retail_task_00{i}", "description": {"purpose": "sample"}} for i in range(1, 5)],
        "simulations": sims,
    }


# ---- OTel sample -------------------------------------------------------------
def kv(k, v):
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}
    if isinstance(v, float):
        return {"key": k, "value": {"doubleValue": v}}
    return {"key": k, "value": {"stringValue": v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}}


def span(trace_id, span_id, parent, name, start_ns, end_ns, attrs, error=False):
    s = {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": [kv(k, v) for k, v in attrs.items()],
        "status": {"code": 2 if error else 1},
    }
    return s


def build_otel() -> dict:
    base = 1_758_800_000_000_000_000
    spans = []

    # invocation A: successful lookup
    t = "a" * 32
    spans.append(span(t, "a000000000000001", "", "invoke_agent retail_agent", base, base + 6_000_000_000, {
        "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "retail_agent", "gen_ai.request.model": "qwen3-8b",
        "sparkjury.task_id": "otel_task_A", "sparkjury.trial": 0, "sparkjury.success": True, "sparkjury.domain": "retail",
    }))
    spans.append(span(t, "a000000000000002", "a000000000000001", "chat qwen3-8b", base + 100_000_000, base + 1_500_000_000, {
        "gen_ai.operation.name": "chat", "gen_ai.request.model": "qwen3-8b",
        "gen_ai.usage.input_tokens": 812, "gen_ai.usage.output_tokens": 41,
        "gen_ai.input.messages": [
            {"role": "system", "parts": [{"type": "text", "content": "You are a retail agent."}]},
            {"role": "user", "parts": [{"type": "text", "content": "Where is my order #W1?"}]},
        ],
        "gen_ai.output.messages": [
            {"role": "assistant", "parts": [{"type": "tool_call", "id": "call_1", "name": "get_order_details", "arguments": {"order_id": "#W1"}}], "finish_reason": "tool_call"}
        ],
    }))
    spans.append(span(t, "a000000000000003", "a000000000000001", "execute_tool get_order_details", base + 1_600_000_000, base + 1_900_000_000, {
        "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "get_order_details", "gen_ai.tool.call.id": "call_1",
        "gen_ai.tool.call.arguments": {"order_id": "#W1"}, "gen_ai.tool.call.result": {"status": "shipped", "eta": "2 days"},
    }))
    spans.append(span(t, "a000000000000004", "a000000000000001", "chat qwen3-8b", base + 2_000_000_000, base + 3_200_000_000, {
        "gen_ai.operation.name": "chat", "gen_ai.request.model": "qwen3-8b",
        "gen_ai.usage.input_tokens": 900, "gen_ai.usage.output_tokens": 30,
        "gen_ai.output.messages": [
            {"role": "assistant", "parts": [{"type": "text", "content": "Your order #W1 has shipped and arrives in 2 days."}], "finish_reason": "stop"}
        ],
    }))

    # invocation B: tool error then failure
    t = "b" * 32
    spans.append(span(t, "b000000000000001", "", "invoke_agent retail_agent", base, base + 4_000_000_000, {
        "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "retail_agent", "gen_ai.request.model": "qwen3-8b",
        "sparkjury.task_id": "otel_task_B", "sparkjury.trial": 0, "sparkjury.success": False, "sparkjury.domain": "retail",
        "error.type": "ToolError",
    }, error=True))
    spans.append(span(t, "b000000000000002", "b000000000000001", "chat qwen3-8b", base + 100_000_000, base + 1_200_000_000, {
        "gen_ai.operation.name": "chat", "gen_ai.request.model": "qwen3-8b",
        "gen_ai.input.messages": [{"role": "user", "parts": [{"type": "text", "content": "Cancel order #W2 please."}]}],
        "gen_ai.output.messages": [{"role": "assistant", "parts": [{"type": "tool_call", "id": "call_9", "name": "cancel_pending_order", "arguments": {"order_id": "#W2", "reason": "no longer needed"}}]}],
    }))
    spans.append(span(t, "b000000000000003", "b000000000000001", "execute_tool cancel_pending_order", base + 1_300_000_000, base + 1_400_000_000, {
        "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "cancel_pending_order", "gen_ai.tool.call.id": "call_9",
        "gen_ai.tool.call.arguments": {"order_id": "#W2", "reason": "no longer needed"}, "error.type": "PermissionDenied",
        "gen_ai.tool.call.result": "permission denied: user not authenticated",
    }, error=True))

    return {"resourceSpans": [{"resource": {"attributes": [kv("service.name", "sample-agent")]}, "scopeSpans": [{"scope": {"name": "sparkjury.sample"}, "spans": spans}]}]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tau2_retail_sample.json").write_text(json.dumps(build_tau2(), ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "otel_sample.json").write_text(json.dumps(build_otel(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote samples to {OUT}")


if __name__ == "__main__":
    main()
