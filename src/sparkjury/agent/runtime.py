"""装配层：把模型、工具、会话树、事件流和用量账本拼成一次能跑的 agent run。

对应 pi 的 `pi-durable` 那一小块里最省事的版本。这里刻意只做三件事：

1. 开一个 run 目录 `runs/agent-<时间戳>/`，和流水线那些 run 摆在一起，用同一套 EventBus
   写 `events.jsonl`，M8 的看板不用改就能看到 agent 在干什么。
2. 会话写 `session.jsonl`（只追加、带 parent），中断后能原样回放；用量写 `usage.jsonl`。
3. 收尾写 `manifest.json`，字段口径跟流水线的 manifest 对齐（status / models / duration_s /
   degradations），看的人和工具不用学第二套。

没做的是操作状态机那一层（operation = run | compaction | navigation）：这一版不压缩历史、
不跨进程恢复在跑的操作，中断了就中断了，人重新起一次。
"""

from __future__ import annotations

import json
import platform
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from sparkjury import __version__
from sparkjury.agent.ai import Provider, Turn
from sparkjury.agent.loop import AgentLoop, LoopResult, default_system_prompt
from sparkjury.agent.session import SessionTree
from sparkjury.agent.tools import SkillInfo, ToolRegistry, load_skill_tools
from sparkjury.harness.events import EventBus


def new_run_id(prefix: str = "agent") -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


@dataclass
class AgentRun:
    """一次 agent run 的把手：跑之前的东西、跑完的产物都在这里。"""

    run_id: str
    run_dir: Path
    session: SessionTree
    bus: EventBus
    provider: Provider
    registry: ToolRegistry
    skills: list[SkillInfo] = field(default_factory=list)
    ledger_path: Path | None = None
    loop: AgentLoop | None = None
    started_at: str = ""
    _t0: float = 0.0

    @property
    def session_path(self) -> Path:
        return self.run_dir / "session.jsonl"

    @property
    def events_path(self) -> Path:
        return self.run_dir / "events.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "manifest.json"


class AgentRuntime:
    """把上面那堆零件装成一次 run，并负责落盘。"""

    def __init__(self, provider: Provider, *, runs_dir: str | Path = "runs", run_id: str | None = None,
                 workdir: str | Path | None = None, registry: ToolRegistry | None = None,
                 skills: Sequence[SkillInfo] = (), executor: Any | None = None,
                 skills_root: str | Path | None = None, max_turns: int = 12,
                 system_prompt: str | None = None, session_path: Path | None = None):
        self.workdir = Path(workdir or Path.cwd()).resolve()
        self.run_id = run_id or new_run_id()
        self.run_dir = Path(runs_dir) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if registry is None:
            registry, skills = load_skill_tools(skills_root, executor=executor, workdir=self.workdir)
        self.registry = registry
        self.skills = list(skills)
        self.bus = EventBus(self.run_id, self.run_dir / "events.jsonl")
        self.session = SessionTree.load(session_path or (self.run_dir / "session.jsonl"))
        self.provider = provider
        self.max_turns = max_turns
        self.system_prompt = system_prompt or default_system_prompt(registry, self.skills, workdir=self.workdir)
        self.run = AgentRun(run_id=self.run_id, run_dir=self.run_dir, session=self.session, bus=self.bus,
                            provider=provider, registry=registry, skills=self.skills,
                            ledger_path=self.run_dir / "usage.jsonl",
                            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.turns: list[dict[str, Any]] = []

    # ------------------------------------------------------------ 跑
    def start(self, prompt: str | None, *, max_turns: int | None = None) -> LoopResult:
        """起一次 run。返回的 LoopResult 里 stopped / turns / usage 就是要贴进报告的东西。"""
        self.run._t0 = time.perf_counter()
        loop = AgentLoop(self.provider, self.registry, self.session, bus=self.bus,
                         max_turns=max_turns or self.max_turns, system_prompt=self.system_prompt,
                         skills=self.skills, on_turn=self._on_turn)
        self.run.loop = loop
        result = loop.run(prompt)
        self.write_manifest(result)
        return result

    def _on_turn(self, turn: Turn, index: int) -> None:
        """每一轮往用量账本追加一行。模型端点不回 usage 时照样记延迟和工具数。"""
        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "turn": index,
               "model": turn.model or self.provider.spec.model, "latency_ms": round(turn.latency_ms, 1),
               "tool_calls": len(turn.tool_calls), "error": turn.error, **(turn.usage or {})}
        self.turns.append(row)
        if self.run.ledger_path:
            with self.run.ledger_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------ 收尾
    def write_manifest(self, result: LoopResult) -> Path:
        """写 manifest.json。降级项照实记：模型调用失败、工具失败都进 degradations。"""
        degradations = [f"tool {name} failed" for name in _failed_tools(self.run.registry)]
        if result.error:
            degradations.append(f"model call failed: {result.error}")
        if result.stopped == "aborted":
            degradations.append("run aborted by caller")
        manifest = {
            "run_id": self.run_id,
            "kind": "agent",
            "status": "ok" if result.ok else result.stopped,
            "sparkjury_version": __version__,
            "python": sys.version.split()[0],
            "host": platform.node(),
            "started_at": self.run.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "duration_s": round(time.perf_counter() - self.run._t0, 2),
            "session": {"id": self.session.session_id, "path": str(self.run.session_path),
                        "entries": len(self.session), "leaf": self.session.leaf_id},
            "models": {"agent": {"name": self.provider.spec.name, "model": self.provider.spec.model,
                                 "base_url": self.provider.spec.base_url}},
            "tools": self.registry.names(),
            "skills": [s.name for s in self.skills],
            "turns": result.turns,
            "tool_calls": result.tool_calls,
            "stopped": result.stopped,
            "usage": result.usage,
            "degradations": degradations,
        }
        self.run.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
        # 这里刻意不发事件：run_end 必须是事件流里最后一条。收尾之后还能再冒一条出来，
        # 消费 SSE 的看板就没法用「看到 run_end 就收工」判断一次 run 结束了。
        return self.run.manifest_path


def _failed_tools(registry: ToolRegistry) -> list[str]:
    """注册表调用记录里失败过的工具名（同一工具只算一次，按首次出现排序）。"""
    failed: list[str] = []
    for record in registry.calls:
        if not record.ok and record.name not in failed:
            failed.append(record.name)
    return failed
