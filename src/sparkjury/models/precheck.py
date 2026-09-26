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


class PrecheckResult(BaseModel):
    trace_id: str
    flags: list[PrecheckFlag] = Field(default_factory=list)

    @property
    def is_env_failure(self) -> bool:
        return bool(self.flags)

    @property
    def kinds(self) -> list[PrecheckKind]:
        return [f.kind for f in self.flags]
