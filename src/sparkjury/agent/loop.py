"""agent loop：一次 run 就是一串 turn，直到模型不再要求调工具。

对应 pi 的 agent loop：消息进当前分支 → 组装请求（system prompt + 活动分支 + 工具表）
→ 模型回文本和工具调用 → 执行工具、把结果写回会话 = 一个 turn → 还有要求就再来一轮，
否则这个 run 结束。

三种打断方式，语义跟 pi 对齐：
- `steer(text)`：在当前 assistant turn 之后插入，下一个请求就能看到（工具跑着的时候插话）。
- `followup(text)`：等这一轮活干完、模型自己说完了，再作为新的一轮发过去。
- `abort()`：停掉当前 run，已经写进会话的记录一条不删。

这一层只管推进，不碰网络也不碰文件：模型从 Provider 来，工具从 ToolRegistry 来，
落到哪儿由 runtime 决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from sparkjury.agent.ai import Provider, ToolCall, Turn
from sparkjury.agent.session import SessionTree
from sparkjury.agent.tools import SkillInfo, ToolRegistry, describe_skills
from sparkjury.harness.events import EventBus, EventKind

STOPPED_END_TURN = "end_turn"
STOPPED_ABORTED = "aborted"
STOPPED_MAX_TURNS = "max_turns"
STOPPED_ERROR = "error"

DEFAULT_MAX_TURNS = 12


def default_system_prompt(registry: ToolRegistry, skills: Sequence[SkillInfo] = (), *,
                          workdir: str | Path | None = None, extra: str = "") -> str:
    """system prompt 里只放技能的一句话描述；正文让模型自己用 load_skill 去取。"""
    lines = [
        "你是 SparkJury 的评测 Agent，跑在团队的 DGX Spark 节点上。你的活是给别人的 Agent 做体检：",
        "把轨迹灌进库里、用裁判面板打分、分歧交给仲裁、把坏例子聚类、最后出证据卡片。",
        "",
        "你可以调用这些工具：",
        registry.describe(),
    ]
    if skills:
        lines += ["", "仓库里有这些技能（要看说明书就调 load_skill）：", describe_skills(skills)]
    if workdir:
        lines += ["", f"工作目录是 {workdir}，list_dir 和 read_file 只能在这个目录里活动。"]
    lines += [
        "",
        "干活规矩：",
        "- 不确定某个技能怎么用，先 load_skill 读它的说明书，不要凭猜编参数。",
        "- 按 SparkJury 流水线的顺序推进：清洗导入 → 定评测集 → 打分 → 出卡片。",
        "- 真跑命令用 run_skill，参数原样透传给命令行。",
        "- 工具报错就照实说清楚是哪一步失败了，不要绕过它假装成功。",
        "- 最后一次回答不要再调工具，直接用中文讲清楚：你做了什么、看到什么、结论是什么。",
    ]
    if extra:
        lines += ["", extra]
    return "\n".join(lines)


@dataclass
class LoopResult:
    """一次 run 的结果。`stopped` 说明它是怎么停的，回放时先看这个。"""

    stopped: str
    turns: int = 0
    tool_calls: int = 0
    text: str = ""
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    session_id: str = ""
    session_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.stopped in (STOPPED_END_TURN, STOPPED_MAX_TURNS) and not self.error


class AgentLoop:
    """把「模型说要干什么」和「工具真的干了什么」串起来的那只循环。"""

    def __init__(self, provider: Provider, registry: ToolRegistry, session: SessionTree, *,
                 bus: EventBus | None = None, max_turns: int = DEFAULT_MAX_TURNS,
                 system_prompt: str | None = None, skills: Sequence[SkillInfo] = (),
                 on_turn: Callable[[Turn, int], None] | None = None):
        self.provider = provider
        self.registry = registry
        self.session = session
        self.bus = bus
        self.max_turns = max(1, int(max_turns))
        self.skills = list(skills)
        self.on_turn = on_turn
        self._system_prompt = system_prompt
        self._steering: list[str] = []
        self._followups: list[str] = []
        self._aborted = False
        self.usage: dict[str, int] = {}

    # ------------------------------------------------------------ 三个打断口
    def steer(self, text: str) -> None:
        self._steering.append(text)

    def followup(self, text: str) -> None:
        self._followups.append(text)

    def abort(self) -> None:
        self._aborted = True

    @property
    def aborted(self) -> bool:
        return self._aborted

    # ------------------------------------------------------------ 主循环
    def run(self, prompt: str | None = None) -> LoopResult:
        self._aborted = False
        result = LoopResult(stopped=STOPPED_END_TURN, session_id=self.session.session_id,
                            session_path=str(self.session.path) if self.session.path else None)
        self._ensure_system()
        if prompt:
            self.session.append("message", role="user", data={"text": prompt})
        self._publish(EventKind.RUN_START, "agent run 开始", model=self.provider.spec.model,
                      base_url=self.provider.spec.base_url, max_turns=self.max_turns,
                      session_id=self.session.session_id)
        while True:
            if self._aborted:
                result.stopped = STOPPED_ABORTED
                self.session.append("note", data={"text": "run 被中断，已完成的记录保留", "level": "warn"})
                self._publish(EventKind.WARNING, "run 被中断")
                break
            if result.turns >= self.max_turns:
                result.stopped = STOPPED_MAX_TURNS
                self.session.append("note", data={"text": f"达到轮数上限 {self.max_turns}", "level": "warn"})
                self._publish(EventKind.WARNING, f"达到轮数上限 {self.max_turns}")
                break
            self._flush_steering()
            try:
                turn = self.provider.complete(self.session.messages(), self.registry.payload())
            except KeyboardInterrupt:
                # Ctrl-C 不算异常退出：按中断处理，已写下的记录一条不动
                self._aborted = True
                continue
            result.turns += 1
            self._accumulate_usage(turn, result)
            if self.on_turn:
                self.on_turn(turn, result.turns)
            if turn.error:
                result.stopped = STOPPED_ERROR
                result.error = turn.error
                self.session.append("note", data={"text": f"模型调用失败：{turn.error}", "level": "error"})
                self._publish(EventKind.ERROR, f"模型调用失败：{turn.error}",
                              model=self.provider.spec.model, turn=result.turns)
                break
            self._publish(EventKind.PROGRESS, f"第 {result.turns} 轮：模型回复",
                          phase="assistant", turn=result.turns, tool_calls=len(turn.tool_calls),
                          latency_ms=round(turn.latency_ms, 1))
            if turn.tool_calls:
                result.tool_calls += len(turn.tool_calls)
                self._record_tool_calls(turn)
                self._run_tools(turn.tool_calls)
                continue
            result.text = turn.text
            self.session.append("message", role="assistant", data={"text": turn.text})
            if self._followups:
                text = self._followups.pop(0)
                self.session.append("message", role="user", data={"text": text, "followup": True})
                self._publish(EventKind.PROGRESS, "收到 follow-up，继续跑", phase="followup")
                continue
            result.stopped = STOPPED_END_TURN
            break
        self._publish(EventKind.RUN_END, f"agent run 结束：{result.stopped}", stopped=result.stopped,
                      turns=result.turns, tool_calls=result.tool_calls, usage=dict(self.usage),
                      error=result.error)
        return result

    # ------------------------------------------------------------ 内部
    def _ensure_system(self) -> None:
        if len(self.session) and self.session.entries[0].role == "system":
            return
        text = self._system_prompt or default_system_prompt(self.registry, self.skills)
        if len(self.session):
            # 会话已经开了头（比如从文件恢复），补一条 system 到当前分支尾部不如直接记个说明
            self.session.append("note", data={"text": "会话已有内容，沿用文件里的记录；system prompt 未重新写入"})
            return
        self.session.append("message", role="system", data={"text": text})

    def _flush_steering(self) -> None:
        while self._steering:
            text = self._steering.pop(0)
            self.session.append("message", role="user", data={"text": text, "steering": True})
            self._publish(EventKind.PROGRESS, "插入 steering 消息", phase="steering")

    def _record_tool_calls(self, turn: Turn) -> None:
        self.session.append("tool_call", data={
            "text": turn.text,
            "calls": [{"id": c.id, "name": c.name, "arguments": c.raw_arguments or _dumps(c.arguments)}
                      for c in turn.tool_calls],
        })

    def _run_tools(self, calls: Sequence[ToolCall]) -> None:
        for call in calls:
            if self._aborted:
                self.session.append("tool_result", data={"call_id": call.id, "name": call.name,
                                                         "output": "（run 已中断，未执行）", "ok": False})
                continue
            output, ok = self.registry.call(call.name, call.arguments)
            self.session.append("tool_result", data={"call_id": call.id, "name": call.name,
                                                     "output": output, "ok": ok})
            self._publish(EventKind.PROGRESS if ok else EventKind.WARNING,
                          f"工具 {call.name} {'完成' if ok else '失败'}", phase="tool", tool=call.name,
                          ok=ok, output_chars=len(output))

    def _accumulate_usage(self, turn: Turn, result: LoopResult) -> None:
        for key, value in (turn.usage or {}).items():
            self.usage[key] = self.usage.get(key, 0) + int(value)
        result.usage = dict(self.usage)

    def _publish(self, kind: EventKind, message: str, **data: Any) -> None:
        if self.bus is not None:
            self.bus.publish(kind, message, stage="AGENT", **data)


def _dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
