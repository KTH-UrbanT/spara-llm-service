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
    """Per-case loop budget, carried across checkpoints in the graph state."""

    # Rewinds still allowed on this case. Decremented by whichever checkpoint spends one.
    retry_budget_remaining: int = 2
    # (node_name, "rewind") pairs already spent — the anti-thrash memory.
    attempt_history: list = field(default_factory=list)
    # Which experiment arm's rules apply. Derived from EVALUATOR_MODE, never read back
    # out of graph state; see `_MODE_TO_ARM` in building_flow_graph.py.
    arm: Literal["A_open", "A_late_only", "A_full"] = "A_open"


@dataclass
class ControllerDecision:
    """What the graph should do next: fall through, rewind upstream, or stop."""

    action: Literal["continue", "rewind", "terminate"]
    # Graph node to rewind to; None unless action == "rewind".
    target_stage: str | None = None
    # Note injected into that node's prompt on rewind (see `_fmt`).
    corrective_hint: str | None = None
    # Merged into `controller_flags` in the graph state; analysis reads them, nothing branches.
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
    """Turns a judge verdict into a continue / rewind / terminate decision.

    Pure and stateless: all mutable state lives in the `ControllerState` passed in,
    which the caller has rebuilt from the graph state for this attempt.
    """

    def handle_early_checkpoint(self, verdict, ctrl: ControllerState) -> ControllerDecision:
        """Decide whether an implausible route is worth rewinding the router for."""
        # Only "implausible" is actionable — "ambiguous" is the downgraded verdict the
        # judge's own guard produces when it is not confident enough to fire.
        if verdict.verdict in ("plausible", "ambiguous"):
            return ControllerDecision(action="continue")
        # The early loop is the full arm's defining feature; the other arms observe only.
        if ctrl.arm != "A_full":
            return ControllerDecision(action="continue")
        # Out of budget, or the router was already rewound once on this case (anti-thrash).
        if ctrl.retry_budget_remaining <= 0 or ("understand_context", "rewind") in ctrl.attempt_history:
            return ControllerDecision(action="continue", flags={"early_block_exhausted": True})
        # Name the weakest axis so the hint tells the router what specifically was wrong.
        axes = _rankable(verdict.axes)
        lowest = min(axes, key=axes.get, default="intent_consistency")
        n = 2 - ctrl.retry_budget_remaining + 1   # 1-based attempt number, for the hint text
        hint = _fmt(n, "router", "axes: " + ", ".join(f"{k}={v}" for k, v in axes.items()),
                    lowest, verdict.corrective_hint or "Reconsider the route.")
        # Spend the budget before returning — the caller writes both fields back to the state.
        ctrl.retry_budget_remaining -= 1
        ctrl.attempt_history.append(("understand_context", "rewind"))
        return ControllerDecision(action="rewind", target_stage="understand_context", corrective_hint=hint)

    def handle_late_checkpoint(self, verdict, ctrl: ControllerState) -> ControllerDecision:
        """Route a failed answer back to the stage the attribution rule blames."""
        if verdict.verdict == "pass":
            return ControllerDecision(action="terminate", flags={"case_pass": True})
        attr = verdict.stage_attribution_rule
        target_for = {"summarizer": "llm_summarizer", "specialists": "maintain_history",
                      "router": "understand_context"}
        # Every branch below terminates without a pass; the flags say which reason applied.
        if ctrl.retry_budget_remaining <= 0:
            return ControllerDecision(action="terminate",
                                      flags={"closed_loop_terminated_without_pass": True})
        # No stage to blame (composite-only failure) — a rewind would be a guess.
        if attr == "unknown":
            return ControllerDecision(action="terminate",
                                      flags={"closed_loop_terminated_without_pass": True})
        # The late-only arm may fix the answer but never re-decide the route: that
        # restriction is what makes full-vs-late-only measure early routing alone.
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
        n = 2 - ctrl.retry_budget_remaining + 1   # 1-based attempt number, for the hint text
        hint = _fmt(n, attr, f"composite={verdict.composite:.1f}", lowest,
                    verdict.corrective_hint or f"Improve {attr}.")
        ctrl.retry_budget_remaining -= 1
        ctrl.attempt_history.append((target, "rewind"))
        return ControllerDecision(action="rewind", target_stage=target, corrective_hint=hint)
