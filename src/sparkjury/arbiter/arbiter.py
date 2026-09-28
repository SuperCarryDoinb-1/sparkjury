"""Arbiter (M4): turn three verdicts into one decision per dimension.

Agreed dimensions take the panel median (majority label for outcome).
Disagreements go to Jev; if Jev is unconfigured or fails within its timeout,
a local judge arbitrates and the decision is marked degraded. A deterministic
5% sample of traces is additionally re-scored by an audit judge so systematic
blind spots of the small local judges become visible.

一切走本地仲裁的决策（`degraded=True`）默认全部送审计，不参与 5% 抽样。理由是
真批上量出来的：63 条 trace 里 38 条本地仲裁决策有 31 条被审计裁判判成另一种结果，
而被抽到的 18 条面板决策一条不一致。

这个 31/38 的读法要知道：审计裁判就是面板里的另一位真裁判，它重问的正是当初吵起来
的那一票，所以这个数说明的是「这些维度没有独立裁决」——面板只有两个真裁判时，
本地仲裁人无论选谁都是当事人——而不是「仲裁判错了」。审计在这里的作用是把缺失
独立裁决的规模变成可数的，不是去纠正它。审计只记录不改判。
"""

from __future__ import annotations

import hashlib
import statistics
import time
from typing import Sequence

from sparkjury.arbiter.jev import JevClient, JevError
from sparkjury.judges.client import Judge
from sparkjury.judges.prompts import rubric_levels
from sparkjury.models.arbitration import Arbitration, DecisionSource, TraceDecision
from sparkjury.models.trace import Trace
from sparkjury.models.verdict import Dimension, DimensionAgreement, PanelResult, Verdict


class Arbiter:
    def __init__(
        self,
        *,
        jev: JevClient | None = None,
        local_judge: Judge | None = None,
        audit_judge: Judge | None = None,
        audit_rate: float = 0.05,
        audit_degraded: bool = True,
        transcript_width: int = 4000,
        transcript_max_chars: int | None = 45000,
        include_gold: bool = False,
    ):
        self.jev = jev if (jev is not None and jev.configured) else None
        self.jev_unavailable_reason = None if self.jev else ("jev not configured" if jev is None or not jev.configured else None)
        self.local_judge = local_judge
        self.audit_judge = audit_judge
        self.audit_rate = audit_rate
        self.audit_degraded = audit_degraded
        self.transcript_width = transcript_width
        self.transcript_max_chars = transcript_max_chars
        self.include_gold = include_gold

    # ---- public --------------------------------------------------------------

    def decide(self, trace: Trace, panel: PanelResult) -> TraceDecision:
        audit = self._is_audit_sample(trace.trace_id)
        out: list[Arbitration] = []
        for agreement in panel.agreement:
            verdicts = panel.verdicts_for(agreement.dimension)
            if agreement.agreed:
                arb = self._from_panel(trace, agreement, verdicts, DecisionSource.PANEL, degraded=False)
            else:
                arb = self._arbitrate(trace, agreement, verdicts)
            if self.audit_judge is not None and (audit or (self.audit_degraded and arb.degraded)):
                self._audit(trace, arb)
            out.append(arb)
        return TraceDecision(trace_id=trace.trace_id, arbitrations=out)

    def decide_many(self, pairs: Sequence[tuple[Trace, PanelResult]], on_result=None) -> list[TraceDecision]:
        res = []
        for trace, panel in pairs:
            d = self.decide(trace, panel)
            res.append(d)
            if on_result:
                on_result(d)
        return res

    # ---- decision paths ---------------------------------------------------------

    def _arbitrate(self, trace: Trace, agreement: DimensionAgreement, verdicts: list[Verdict]) -> Arbitration:
        if self.jev is not None:
            try:
                return self._via_jev(trace, agreement, verdicts)
            except JevError as e:
                jev_err = str(e)
        else:
            jev_err = self.jev_unavailable_reason or "jev unavailable"
        if self.local_judge is not None:
            t0 = time.perf_counter()
            v = self.local_judge.score(trace, agreement.dimension)
            if v.ok:
                return Arbitration(
                    trace_id=trace.trace_id, dimension=agreement.dimension,
                    final_score=v.score, final_label=v.label if agreement.dimension == Dimension.OUTCOME else None,
                    source=DecisionSource.LOCAL, degraded=True,
                    panel_scores=agreement.scores, panel_labels=agreement.labels,
                    rationale=f"[degraded: {jev_err}] local {self.local_judge.name}: {v.rationale}",
                    latency_ms=(time.perf_counter() - t0) * 1000,
                )
            jev_err += f"; local judge error: {v.error}"
        arb = self._from_panel(trace, agreement, verdicts, DecisionSource.PANEL_FALLBACK, degraded=True)
        arb.error = jev_err
        arb.rationale = f"[degraded: {jev_err}] panel median used"
        return arb

    def _via_jev(self, trace: Trace, agreement: DimensionAgreement, verdicts: list[Verdict]) -> Arbitration:
        assert self.jev is not None
        dim = agreement.dimension
        state = self._state_text(trace, dim, verdicts)
        levels = rubric_levels(dim)
        questions = {"score": JevClient.q_score(f"Rate the agent on the {dim.value} rubric.", levels)}
        if dim == Dimension.OUTCOME:
            questions["pass"] = JevClient.q_noul("Did the agent achieve what the user asked for, as shown by the final state?")
        answers = self.jev.ask(state, questions)
        raw, idx, conf = JevClient.parse_score(answers["score"], len(levels))
        label = None
        if dim == Dimension.OUTCOME:
            p = JevClient.parse_noul(answers["pass"]) if "pass" in answers else (1.0 if idx >= 3 else 0.0)
            label = "pass" if p >= 0.5 else "fail"
            # keep score and label consistent
            if label == "pass" and idx < 3:
                idx = 3
            if label == "fail" and idx > 2:
                idx = 2
        return Arbitration(
            trace_id=trace.trace_id, dimension=dim, final_score=idx, final_label=label,
            source=DecisionSource.JEV, degraded=False,
            panel_scores=agreement.scores, panel_labels=agreement.labels,
            jev_confidence=conf, jev_raw_score=raw,
            rationale=f"Jev {self.jev.model}: position {raw:.2f} on {len(levels)} levels" + (f", confidence {conf:.2f}" if conf is not None else ""),
            latency_ms=self.jev.last_latency_ms,
        )

    def _from_panel(self, trace: Trace, agreement: DimensionAgreement, verdicts: list[Verdict],
                    source: DecisionSource, *, degraded: bool) -> Arbitration:
        ok = [v for v in verdicts if v.ok]
        scores = [v.score for v in ok if v.score is not None]
        score = int(round(statistics.median(scores))) if scores else None
        label = None
        if agreement.dimension == Dimension.OUTCOME:
            labels = [v.label for v in ok if v.label]
            if labels:
                label = max(set(labels), key=labels.count)
            elif score is not None:
                label = "pass" if score >= 3 else "fail"
        return Arbitration(
            trace_id=trace.trace_id, dimension=agreement.dimension, final_score=score, final_label=label,
            source=source, degraded=degraded, panel_scores=agreement.scores, panel_labels=agreement.labels,
            rationale=("panel agreed: " if source == DecisionSource.PANEL else "") + agreement.reason,
        )

    # ---- audit ---------------------------------------------------------------------

    def _is_audit_sample(self, trace_id: str) -> bool:
        if self.audit_rate <= 0:
            return False
        h = int(hashlib.sha1(f"audit|{trace_id}".encode()).hexdigest(), 16)
        return (h % 10_000) / 10_000.0 < self.audit_rate

    def _audit(self, trace: Trace, arb: Arbitration) -> None:
        assert self.audit_judge is not None
        v = self.audit_judge.score(trace, arb.dimension)
        arb.audit_sampled = True
        if not v.ok:
            arb.audit_disagrees = None
            return
        arb.audit_score = v.score
        arb.audit_label = v.label if arb.dimension == Dimension.OUTCOME else None
        if arb.dimension == Dimension.OUTCOME:
            arb.audit_disagrees = (v.label != arb.final_label)
        else:
            arb.audit_disagrees = (arb.final_score is not None and v.score is not None and abs(v.score - arb.final_score) > 1)

    # ---- state text for Jev ----------------------------------------------------------

    def _state_text(self, trace: Trace, dim: Dimension, verdicts: list[Verdict]) -> str:
        o = trace.outcome
        head = f"Task {trace.task_id} trial {trace.trial}, domain {trace.domain}."
        if self.include_gold:
            gold = "not available" if o.success is None else ("SUCCESS" if o.success else "FAIL")
            head += f" Gold final-state outcome: {gold}."
        lines = [
            head,
            f"Dimension under arbitration: {dim.value}.",
        ]
        requirement = (trace.task_requirement or "").strip()
        if requirement:
            lines.append(f"Task requirement (what the user came for, as the task defines it): {requirement}")
        lines.append("Three independent judges disagreed:")
        for v in verdicts:
            if v.ok:
                lines.append(f"- {v.judge} ({v.model}): score {v.score}" + (f", {v.label}" if v.label else "") +
                             f"; evidence steps {v.evidence_steps or '-'}; {v.rationale}")
            else:
                lines.append(f"- {v.judge} ({v.model}): failed ({v.error})")
        lines.append("Transcript ([n] = step index):")
        lines.append(trace.transcript(width=self.transcript_width, max_chars=self.transcript_max_chars))
        return "\n".join(lines)
