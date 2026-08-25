"""Controller decisions: which failures earn a rewind, and which arm is allowed one."""
import pytest
from src.evaluation.closed_loop.controller import ControllerState, ControllerDecision, EvaluationController
from src.evaluation.closed_loop.in_loop_evaluator import RouteVerdict, AnswerVerdict

def _ctrl(arm="A_full", budget=2, history=None):
    """A controller state with the given arm, budget and rewind history."""
    return ControllerState(retry_budget_remaining=budget,
                           attempt_history=list(history or []), arm=arm)
def _rv(v="implausible", h="Route to generic instead of building."):
    """A route verdict with both axes below TAU_ROUTE."""
    return RouteVerdict(verdict=v, axes={"intent_consistency":3,"precondition_satisfied":2},
                        corrective_hint=h)
def _av(v="fail", a="summarizer", h="Fix numbers."):
    """An answer verdict attributed to stage `a`."""
    return AnswerVerdict(verdict=v,
                         axes={"faithfulness":3,"answer_relevance":8,"question_coverage":7,"calibration":8},
                         composite=6.5, stage_attribution_judge=a, stage_attribution_rule=a,
                         corrective_hint=h)

c = EvaluationController()

def test_late_rewinds_when_faithfulness_is_not_applicable():
    """Fix 4 puts None in axes when there was no evidence. The controller ranks axes with
    min(); a None there raises TypeError inside the checkpoint node's try, which fails open
    and silently cancels the rewind on exactly the empty-evidence cases Fix 4 is about."""
    v = AnswerVerdict(verdict="fail",
                      axes={"faithfulness": None, "answer_relevance": 8,
                            "question_coverage": 8, "calibration": 2},
                      composite=6.0, evidence_present=False,
                      stage_attribution_judge="summarizer", stage_attribution_rule="summarizer",
                      corrective_hint="Hedge where evidence is missing.")
    s = _ctrl(arm="A_full", budget=2)
    d = c.handle_late_checkpoint(v, s)
    assert d.action == "rewind" and d.target_stage == "llm_summarizer"
    assert "calibration" in d.corrective_hint      # the worst APPLICABLE axis
    assert "faithfulness=None" not in d.corrective_hint

def test_late_rewinds_when_entity_consistency_is_not_applicable():
    """v24 adds a second axis that can be None (no building resolved / no evidence). The
    same min() that Fix 4 defused must survive it, or the rewind silently becomes a no-op."""
    v = AnswerVerdict(verdict="fail",
                      axes={"faithfulness": 8, "answer_relevance": 8,
                            "entity_consistency": None, "calibration": 3},
                      composite=6.33, evidence_present=True,
                      stage_attribution_judge="summarizer", stage_attribution_rule="summarizer",
                      corrective_hint="Hedge where evidence is missing.")
    d = c.handle_late_checkpoint(v, _ctrl(arm="A_full", budget=2))
    assert d.action == "rewind" and d.target_stage == "llm_summarizer"
    assert "calibration" in d.corrective_hint and "entity_consistency=None" not in d.corrective_hint

def test_late_entity_consistency_rewinds_to_the_summarizer():
    """C3: the fixable class is a citation error with the right record already retrieved."""
    v = AnswerVerdict(verdict="fail",
                      axes={"faithfulness": 8, "answer_relevance": 8,
                            "entity_consistency": 2, "calibration": 8},
                      composite=6.5, evidence_present=True,
                      stage_attribution_judge="summarizer", stage_attribution_rule="summarizer",
                      corrective_hint="Quote byggnadsid 19-84-LEOPARDEN5-1.")
    d = c.handle_late_checkpoint(v, _ctrl(arm="A_full", budget=2))
    assert d.action == "rewind" and d.target_stage == "llm_summarizer"
    assert "entity_consistency" in d.corrective_hint

def test_early_rewinds_with_a_none_axis():
    """A None axis on the EARLY verdict must not break min() either."""
    v = RouteVerdict(verdict="implausible",
                     axes={"intent_consistency": 3, "precondition_satisfied": None},
                     corrective_hint="Route to generic instead of building.")
    assert c.handle_early_checkpoint(v, _ctrl(arm="A_full")).action == "rewind"

def test_early_plausible_continues():
    """A plausible route is left alone."""
    assert c.handle_early_checkpoint(_rv("plausible"), _ctrl()).action == "continue"
def test_early_ambiguous_continues():
    """`ambiguous` is the guard's downgrade of a weak `implausible` — never actionable."""
    assert c.handle_early_checkpoint(_rv("ambiguous"), _ctrl()).action == "continue"
def test_early_implausible_full_rewinds():
    """The full arm rewinds the router and spends exactly one unit of budget."""
    s = _ctrl(arm="A_full", budget=2)
    d = c.handle_early_checkpoint(_rv("implausible"), s)
    assert d.action == "rewind" and d.target_stage == "understand_context"
    assert s.retry_budget_remaining == 1 and ("understand_context","rewind") in s.attempt_history
def test_early_implausible_open_continues():
    """The open arm observes only — it must never act on a verdict."""
    assert c.handle_early_checkpoint(_rv("implausible"), _ctrl(arm="A_open")).action == "continue"
def test_early_implausible_late_only_continues():
    """The early loop is the full arm's defining feature; late-only must not have it."""
    assert c.handle_early_checkpoint(_rv("implausible"), _ctrl(arm="A_late_only")).action == "continue"
def test_early_budget_zero():
    """An exhausted budget continues and is flagged, rather than rewinding anyway."""
    d = c.handle_early_checkpoint(_rv("implausible"), _ctrl(arm="A_full", budget=0))
    assert d.action == "continue" and d.flags.get("early_block_exhausted")
def test_early_antithrash():
    """The router is rewound at most once per case, even with budget left."""
    s = _ctrl(arm="A_full", budget=1, history=[("understand_context","rewind")])
    d = c.handle_early_checkpoint(_rv("implausible"), s)
    assert d.action == "continue" and d.flags.get("early_block_exhausted")

def test_late_pass_terminates():
    """A passing answer ends the loop and records the pass."""
    d = c.handle_late_checkpoint(_av("pass"), _ctrl())
    assert d.action == "terminate" and d.flags.get("case_pass")
def test_late_summarizer_rewinds():
    """A summarizer-attributed failure re-drafts the answer and spends budget."""
    s = _ctrl(budget=2)
    d = c.handle_late_checkpoint(_av("fail","summarizer"), s)
    assert d.action == "rewind" and d.target_stage == "llm_summarizer" and s.retry_budget_remaining == 1
def test_late_specialists_rewinds():
    """A specialists-attributed failure targets maintain_history.

    Structurally unreachable since v24 retired `question_coverage`, but the mapping is
    still asserted so re-arming an axis to `specialists` cannot silently misroute.
    """
    d = c.handle_late_checkpoint(_av("fail","specialists"), _ctrl(budget=2))
    assert d.action == "rewind" and d.target_stage == "maintain_history"
def test_late_router_full_rewinds():
    """The full arm may re-decide the route from the late checkpoint."""
    d = c.handle_late_checkpoint(_av("fail","router"), _ctrl(arm="A_full",budget=2))
    assert d.action == "rewind" and d.target_stage == "understand_context"
def test_late_router_late_only_terminates():
    """The late-only arm disables router-rewinds, to isolate early-routing-correction value."""
    d = c.handle_late_checkpoint(_av("fail","router"), _ctrl(arm="A_late_only",budget=2))
    assert d.action == "terminate"
    assert d.flags.get("terminated_router_disallowed") and d.flags.get("closed_loop_terminated_without_pass")
def test_late_unknown_terminates():
    """With no stage to blame, a rewind would be a guess — terminate instead."""
    d = c.handle_late_checkpoint(_av("fail","unknown"), _ctrl())
    assert d.action == "terminate" and d.flags.get("closed_loop_terminated_without_pass")
def test_late_budget_zero_terminates():
    """An exhausted budget terminates without a pass."""
    d = c.handle_late_checkpoint(_av("fail","summarizer"), _ctrl(budget=0))
    assert d.action == "terminate" and d.flags.get("closed_loop_terminated_without_pass")
def test_late_antithrash():
    """Each stage is rewound at most once per case, even with budget left."""
    s = _ctrl(budget=1, history=[("llm_summarizer","rewind")])
    d = c.handle_late_checkpoint(_av("fail","summarizer"), s)
    assert d.action == "terminate" and d.flags.get("closed_loop_terminated_without_pass")
def test_hint_format():
    """The hint keeps its delimited shape — upstream prompts parse it verbatim."""
    d = c.handle_late_checkpoint(_av("fail","summarizer"), _ctrl(budget=2))
    assert "[previous_attempt_note]" in (d.corrective_hint or "")
    assert "stage:" in (d.corrective_hint or "") and "corrective_hint:" in (d.corrective_hint or "")
def test_budget_decrements():
    """The controller mutates the state it is handed; the node writes it back."""
    s = _ctrl(budget=2)
    c.handle_late_checkpoint(_av("fail","summarizer"), s)
    assert s.retry_budget_remaining == 1
