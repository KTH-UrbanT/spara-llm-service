"""Deterministic re-routing state machine (no LLM of its own).

Arm-aware: the late-only arm fixes in-route errors (re-summarise, re-select
specialists) but is forbidden from rewinding the top-level route, so that the
full-vs-late-only contrast isolates the value of early routing correction.
Stage names map to live graph nodes: router -> understand_context,
specialists -> maintain_history (re-runs decision_router), summarizer -> llm_summarizer.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ControllerState:
    retry_budget_remaining: int = 2
    attempt_history: list = field(default_factory=list)
    arm: Literal["A_open", "A_late_only", "A_full"] = "A_open"


@dataclass
class ControllerDecision:
    action: Literal["continue", "rewind", "terminate"]
    target_stage: str | None = None
    corrective_hint: str | None = None
    flags: dict = field(default_factory=dict)


def _rankable(axes: dict | None) -> dict:
    """Axes that can be ordered to pick the worst one.

    Drops the `_`-prefixed bookkeeping keys (`_fail_open`, `_short_circuit`, ...) and
    any axis scored None. None means "not applicable" — the judge had no input for it
    (today: faithfulness with no evidence). Leaving it in makes `min()` compare None
    against an int, which raises TypeError inside the checkpoint node's try/except and
    silently turns the rewind into a no-op on exactly the cases it is meant to correct.
    """
    return {k: v for k, v in (axes or {}).items()
            if not str(k).startswith("_") and v is not None}


def _fmt(n: int, stage: str, prior: str, axis: str, hint: str) -> str:
    """The note injected verbatim into the upstream stage's prompt on rewind."""
    return (f"[previous_attempt_note]\nattempt: {n}\nstage: {stage}\n"
            f"prior_decision: {prior}\nrejection_reason_axis: {axis}\n"
            f"corrective_hint: {hint}\n[/previous_attempt_note]")


class EvaluationController:
    def handle_early_checkpoint(self, verdict, ctrl: ControllerState) -> ControllerDecision:
        if verdict.verdict in ("plausible", "ambiguous"):
            return ControllerDecision(action="continue")
        if ctrl.arm != "A_full":
            return ControllerDecision(action="continue")
        if ctrl.retry_budget_remaining <= 0 or ("understand_context", "rewind") in ctrl.attempt_history:
            return ControllerDecision(action="continue", flags={"early_block_exhausted": True})
        axes = _rankable(verdict.axes)
        lowest = min(axes, key=axes.get, default="intent_consistency")
        n = 2 - ctrl.retry_budget_remaining + 1
        hint = _fmt(n, "router", "axes: " + ", ".join(f"{k}={v}" for k, v in axes.items()),
                    lowest, verdict.corrective_hint or "Reconsider the route.")
        ctrl.retry_budget_remaining -= 1
        ctrl.attempt_history.append(("understand_context", "rewind"))
        return ControllerDecision(action="rewind", target_stage="understand_context", corrective_hint=hint)

    def handle_late_checkpoint(self, verdict, ctrl: ControllerState) -> ControllerDecision:
        if verdict.verdict == "pass":
            return ControllerDecision(action="terminate", flags={"case_pass": True})
        attr = verdict.stage_attribution_rule
        target_for = {"summarizer": "llm_summarizer", "specialists": "maintain_history",
                      "router": "understand_context"}
        if ctrl.retry_budget_remaining <= 0:
            return ControllerDecision(action="terminate",
                                      flags={"closed_loop_terminated_without_pass": True})
        if attr == "unknown":
            return ControllerDecision(action="terminate",
                                      flags={"closed_loop_terminated_without_pass": True})
        if attr == "router" and ctrl.arm == "A_late_only":
            return ControllerDecision(action="terminate",
                                      flags={"closed_loop_terminated_without_pass": True,
                                             "terminated_router_disallowed": True})
        target = target_for.get(attr)
        if not target:
            return ControllerDecision(action="terminate",
                                      flags={"closed_loop_terminated_without_pass": True})
        if (target, "rewind") in ctrl.attempt_history:  # anti-thrash
            return ControllerDecision(action="terminate",
                                      flags={"closed_loop_terminated_without_pass": True})
        axes = _rankable(verdict.axes)
        lowest = min(axes, key=axes.get, default=attr)
        n = 2 - ctrl.retry_budget_remaining + 1
        hint = _fmt(n, attr, f"composite={verdict.composite:.1f}", lowest,
                    verdict.corrective_hint or f"Improve {attr}.")
        ctrl.retry_budget_remaining -= 1
        ctrl.attempt_history.append((target, "rewind"))
        return ControllerDecision(action="rewind", target_stage=target, corrective_hint=hint)
