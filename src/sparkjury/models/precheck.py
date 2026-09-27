"""Precheck results: environment-caused failures that must not be blamed on the agent."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class PrecheckKind(StrEnum):
    INFRA_ERROR = "infra_error"              # runner / infrastructure / simulator crashed
    TIMEOUT = "timeout"                      # run or single step exceeded time budget
    CONTEXT_OVERFLOW = "context_overflow"    # model context window exceeded
    TOOL_UNAVAILABLE = "tool_unavailable"    # backend 5xx / connection errors, repeated
    PERMISSION_DENIED = "permission_denied"  # credentials / ACL problem on the tool side
    USER_SIM_BROKEN = "user_sim_broken"      # simulated user produced empty / looping messages
    EMPTY_TRACE = "empty_trace"              # nothing recorded, cannot be judged


class PrecheckFlag(BaseModel):
    kind: PrecheckKind
    evidence_step_idx: int | None = None
    note: str = ""
    # blocking=True（默认）＝环境把这条 trace 弄坏了：数据本身不能用来判分，排除出评分集。
    # blocking=False＝这条 trace 能判，只是有值得看的信号（例如某一步特别慢），照实写进
    # 报告但照样送裁判。拆开的原因是真批数据逼出来的，见 precheck/rules.py::rule_timeout。
    blocking: bool = True


class PrecheckResult(BaseModel):
    trace_id: str
    flags: list[PrecheckFlag] = Field(default_factory=list)

    @property
    def is_env_failure(self) -> bool:
        """Any *blocking* flag. A trace with only advisory flags still goes to the judges."""
        return any(f.blocking for f in self.flags)

    @property
    def advisories(self) -> list[PrecheckFlag]:
        """Flags worth reporting that must not drop the trace out of the eval."""
        return [f for f in self.flags if not f.blocking]

    @property
    def kinds(self) -> list[PrecheckKind]:
        return [f.kind for f in self.flags]

    @property
    def blocking_kinds(self) -> list[PrecheckKind]:
        return [f.kind for f in self.flags if f.blocking]
