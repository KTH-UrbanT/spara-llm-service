"""The in-loop LLM judge: one method per checkpoint.

INVARIANT: no public method accepts a gold-bearing parameter. This is enforced
structurally (the signatures simply don't declare one) and verified by a test
that inspects every public method's parameter names.
"""
from __future__ import annotations
import json, logging, os, re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from openai import AzureOpenAI, BadRequestError

logger = logging.getLogger(__name__)

# Pre-registered thresholds — committed before the experiment; disclose any post-hoc change.
TAU_ROUTE = 5.0          # early checkpoint: both axes below this can trigger "implausible"
TAU_HINT_MIN_CHARS = 20  # minimum hint length for "implausible" to fire
TAU_SEMANTIC = 7.0       # late checkpoint: composite pass threshold
TAU_AXIS_FLOOR = 4.0     # late checkpoint: any axis below this forces a fail

# k=3 self-consistency on the early judge: a false fire destroys a case permanently while
# a true fire only probably gains one, so the asymmetry is worth 3x the calls (v21 §2.1).
# A module constant rather than CLOSED_LOOP_EARLY_VOTE_K: the early checkpoint runs only
# under EVALUATOR_MODE=full, and every frozen run_record.json agrees A_full always used 3.
EARLY_VOTE_K = 3

# Rubric files, pinned as module constants rather than read from the environment.
# The v1 files stay on disk so the frozen pre-v24 runs remain verifiable, but nothing
# loads them any more: v1's `question_coverage` axis was degenerate (see the _AXIS_ORDER
# note below) and an env-driven default was how a bare `InLoopEvaluator()` could silently
# load v1 and invalidate a new run against the v24/v25 evidence.
ROUTE_PLAUSIBILITY_PROMPT = "route_plausibility_v2.txt"
ANSWER_QUALITY_PROMPT = "answer_quality_v2.txt"

# On o3/o4 deployments reasoning tokens count against this budget, so a value sized
# for the JSON alone returns an empty completion — which the fail-open then converts
# into a silent pass. Sized for reasoning + the verdict.
_MAX_COMPLETION_TOKENS = 6000

# v24: `question_coverage` scored 10 on 855 of 855 attempts (its own rubric gave single-clause
# questions full marks, and the dataset is all single-clause) and agreed with humans at k = 0.0.
# `entity_consistency` replaces it. With several records under one address string, quoting the
# wrong building's figures IS faithful — both numbers are in the evidence — so no other axis
# can see the dominant real failure (v24 §1.1).
_AXIS_ORDER = ["faithfulness", "answer_relevance", "entity_consistency", "calibration"]
# The v1 rubric, kept because `replay_rescore` runs `score_axes` over the frozen v20/v21
# traces: those rows carry `question_coverage`, and scoring them under the v2 order would read
# the absent `entity_consistency` as 0 and report drift that never happened.
_AXIS_ORDER_V1 = ["faithfulness", "answer_relevance", "question_coverage", "calibration"]
# `entity_consistency` -> summarizer, not specialists: the right record is already in the
# evidence, so only the re-draft can fix the citation. That leaves no axis mapped to
# `specialists`, i.e. `rewind_to_specialists` is now structurally dead (v24 §C3).
_AXIS_TO_STAGE = {"faithfulness": "summarizer", "answer_relevance": "router",
                  "entity_consistency": "summarizer", "calibration": "summarizer",
                  "question_coverage": "specialists"}   # retired axis; frozen traces only

# The only identity fields the judge may see. Projected here rather than at each call site
# so no caller can widen it by handing over the whole identity block.
_IDENTITY_KEYS = ("matched_building_id", "matched_address", "candidate_building_ids",
                  "multiple_records_same_address")


def _axis_order(axes: dict) -> list[str]:
    """Which rubric produced these axes — v1 traces carry `question_coverage`."""
    return _AXIS_ORDER_V1 if ("question_coverage" in axes
                              and "entity_consistency" not in axes) else _AXIS_ORDER


@dataclass
class RouteVerdict:
    """The early judge's reading of the chosen route.

    `ambiguous` is not a third opinion the rubric emits — it is the downgrade
    `route_plausible` applies when an `implausible` verdict fails its evidence guard.
    Only `implausible` is actionable by the controller.
    """

    verdict: Literal["plausible", "implausible", "ambiguous"]
    axes: dict
    # Free text shown to the router on rewind; must clear TAU_HINT_MIN_CHARS to fire.
    corrective_hint: str | None
    # The judge's unparsed reply, kept so a verdict can be re-derived offline.
    raw_json: dict = field(default_factory=dict)


@dataclass
class AnswerVerdict:
    """The late judge's reading of the final answer, plus where to blame a failure."""

    verdict: Literal["pass", "fail"]
    axes: dict
    composite: float
    stage_attribution_judge: str   # the judge's own guess (recorded for analysis)
    stage_attribution_rule: str    # deterministic rule (drives the controller)
    corrective_hint: str | None
    # Diagnostic only — nothing branches on it. `stage_attribution_rule` says "summarizer"
    # for every evidence-absent failure because faithfulness and calibration share a stage
    # (v21 §1.6); this column separates "the summariser wrote a bad answer" from "there was
    # nothing for it to write from".
    attribution_diagnostic: str = "unknown"
    # False when the judge was handed no evidence. Recorded on every verdict so a
    # dead upstream retrieval channel shows up as a flag instead of disappearing
    # into a low quality score (§1.3).
    evidence_present: bool = True
    raw_json: dict = field(default_factory=dict)


def _parse(content: str) -> dict | None:
    """Strip a ```json fence if present and parse; None on anything unparseable.

    Returning None rather than raising lets each caller decide — both turn it into an
    explicit fail-open with the raw text attached, instead of a bare JSONDecodeError.
    """
    c = re.sub(r"^```(?:json)?\s*", "", content.strip())
    c = re.sub(r"\s*```$", "", c)
    try:
        return json.loads(c)
    except (json.JSONDecodeError, ValueError):
        return None


def _applicable(axes: dict) -> dict:
    """The axes the pass rule and the attribution rule apply over.

    An axis explicitly set to None is *not applicable* — `faithfulness` when
    there was no evidence to check the answer against, `entity_consistency`
    when the system identified no building (see `_score`) — and is dropped
    rather than read as a zero. An axis the judge simply *omitted* still counts
    as 0, so a missing score is attributed to its stage rather than masked.
    """
    return {a: (axes[a] if a in axes else 0)
            for a in _axis_order(axes) if not (a in axes and axes[a] is None)}


def score_axes(raw_axes: dict, evidence_present: bool,
               entity_scorable: bool = True) -> tuple[dict, float, str]:
    """Parse the judge's axes and apply the pre-registered pass rule.

    `faithfulness` asks whether the answer's claims are supported by the
    retrieved evidence. With no evidence there is nothing to support them
    against, so the axis has no input: it is recorded as None ("not measured")
    rather than scored 0. A 0 would both drag the composite down and trip the
    floor, failing correct answers because of an upstream retrieval outage
    (§1.2). TAU_SEMANTIC and TAU_AXIS_FLOOR are unchanged — only the set of
    axes they are applied over changes.

    `entity_consistency` has the same shape of missing input: with no evidence,
    or with no building resolved to check the answer's figures against, there is
    nothing to be consistent *with*. `entity_scorable` is False in exactly those
    cases and the axis is recorded as None. Enforced here and not left to the
    prompt, for the same reason faithfulness is: a 0 on the 61 evidence-absent
    cases would re-create the v20 false-negative epidemic.

    Returns (axes, composite, verdict).
    """
    # None is the judge saying "not applicable" — keep it, never int() it.
    axes = {k: (None if v is None else int(v)) for k, v in (raw_axes or {}).items()}
    if not evidence_present:
        axes["faithfulness"] = None
    if not entity_scorable and "entity_consistency" in _axis_order(axes):
        axes["entity_consistency"] = None

    vals = _applicable(axes)
    composite = sum(vals.values()) / len(vals) if vals else 0.0
    floor_fail = any(v < TAU_AXIS_FLOOR for v in vals.values())
    verdict = "fail" if (floor_fail or composite < TAU_SEMANTIC) else "pass"
    return axes, composite, verdict


# Back-compat: `replay_rescore` and older callers imported the private name.
_score = score_axes


# Rewind risk per stage, for tie-breaking only (v24 C5). A summariser re-draft keeps the
# route and the evidence, so a wrong one costs an attempt. A router rewind re-decides the
# route: on the v24 smoke, `TULEGATAN_5A_002` scored answer_relevance and entity_consistency
# both 2, the tie resolved to `router` in axis order, the route went building -> generic and
# a passing case failed on `route_match` while its answer had got BETTER. Ties now resolve to
# the recoverable stage; a strictly lower axis still wins outright.
_STAGE_RISK = {"summarizer": 0, "specialists": 1, "router": 2}


def _worst_axis(vals: dict) -> str:
    """The axis a failure is attributed to — lowest score, ties to the safest stage."""
    lo = min(vals.values())
    tied = sorted(a for a, v in vals.items() if v == lo)
    return min(tied, key=lambda a: _STAGE_RISK.get(_AXIS_TO_STAGE.get(a, ""), 3))


def _stage_rule(verdict: str, axes: dict) -> str:
    """Attribution rule, over the applicable axes only (see `_applicable`).

    When a fail is due purely to the composite (every applicable axis at or
    above the floor), the failing stage is indeterminate -> 'unknown'."""
    vals = _applicable(axes)
    if not vals:
        return "unknown"
    if verdict == "fail" and all(v >= TAU_AXIS_FLOOR for v in vals.values()):
        return "unknown"
    return _AXIS_TO_STAGE.get(_worst_axis(vals), "unknown")


def _attribution_diagnostic(verdict: str, axes: dict, evidence_present: bool) -> str:
    """Diagnostic-only refinement of `_stage_rule` — nothing branches on the result.

    With no evidence retrieved, `faithfulness` is dropped (see `score_axes`) and the answer
    can only hedge, so `calibration` / `answer_relevance` become the lowest axis and the rule
    blames the summariser for an upstream retrieval outage. Naming that case
    `retrieval_starved` is what makes the column carry information (v21 §2.3).
    """
    vals = _applicable(axes)
    if vals and not evidence_present \
            and _worst_axis(vals) in ("calibration", "answer_relevance"):
        return "retrieval_starved"
    return _stage_rule(verdict, axes)


def is_o_family(deployment: str | None) -> bool:
    """o3/o4 deployments reject `temperature`, `top_p`, `frequency_penalty`,
    `presence_penalty`, and `max_tokens` — they require `max_completion_tokens`
    instead, and the API-version check fires on certain param combinations.

    Module-level so the run record can derive the judge's sampling config from the same
    predicate `_call` builds the request with, instead of a hand-written claim about it.
    """
    name = (deployment or "").lower()
    return ("o4" in name) or ("o3" in name)


class InLoopEvaluator:
    """Azure OpenAI client wrapping the two rubrics, one public method per checkpoint.

    Every public method fails open — a judge crash returns a permissive verdict tagged
    `_fail_open` rather than raising, so an outage degrades the loop to the open arm
    instead of killing the run. Analysis must exclude those rows (`analysis_filters`).
    """

    def __init__(self) -> None:
        """Build the client and read both rubric files off disk once per instance."""
        # o3/o4 deployments require api-version 2024-12-01-preview or later. The
        # repo's global OPENAI_API_VERSION may be older (2023-05-15), so prefer
        # a judge-specific override and fall back to a version that supports o4.
        api_version = (
            os.environ.get("CLOSED_LOOP_JUDGE_API_VERSION")
            or os.environ.get("OPENAI_RESPONSE_MODEL_API_VERSION")
            or "2024-12-01-preview"
        )
        self._client = AzureOpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            api_version=api_version,
            azure_endpoint=os.environ["AZURE_ENDPOINT"],
        )
        self._api_version = api_version
        self._dep = os.environ["OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME"]
        d = Path(__file__).parent / "prompts"
        self._rp = (d / ROUTE_PLAUSIBILITY_PROMPT).read_text()
        self._ap = (d / ANSWER_QUALITY_PROMPT).read_text()
        self.last_usage: dict = {"total_tokens": 0, "completion_tokens": 0}

    def route_plausible(
        self,
        question: str,
        chosen_route: str,
        has_address_flag: bool,
        dialogue_summary: str | None,  # reserved for future multi-turn work; always None today
        about_to_request_address: bool = False,
    ) -> RouteVerdict:
        """`about_to_request_address` is the graph's pending next hop, not gold: it says the
        router is about to bounce this question back to the user for an address. Without it
        the judge cannot see the failure mode it is best placed to catch — a general-advice
        question routed to `building` and answered with "what is your address?"."""
        try:
            raw = self._call(self._rp, json.dumps(
                {"question": question, "chosen_route": chosen_route,
                 "has_address_flag": has_address_flag,
                 "about_to_request_address": about_to_request_address}, ensure_ascii=False))
            p = _parse(raw)
            if p is None:
                raise ValueError(f"JSON failure: {raw[:200]!r}")
            v = str(p.get("verdict", "plausible")).lower()
            if v not in ("plausible", "implausible", "ambiguous"):
                v = "plausible"
            axes = {k: int(val) for k, val in (p.get("axes") or {}).items()}
            hint = p.get("corrective_hint") or None
            if v == "implausible":
                # `axes and` guards against a vacuous all() on an empty dict —
                # implausible must be backed by actual low-axis evidence.
                if not (axes and all(x < TAU_ROUTE for x in axes.values())
                        and hint is not None and len(hint) >= TAU_HINT_MIN_CHARS):
                    v = "ambiguous"
            return RouteVerdict(verdict=v, axes=axes, corrective_hint=hint, raw_json=p)  # type: ignore
        except Exception as e:
            logger.warning("route_plausible fail-open: %s", str(e)[:200])
            return RouteVerdict(verdict="plausible",
                                axes={"_fail_open": True, "_error": str(e)[:200]},
                                corrective_hint=None)

    def route_plausible_voted(
        self,
        question: str,
        chosen_route: str,
        has_address_flag: bool,
        dialogue_summary: str | None,
        about_to_request_address: bool = False,
        k: int = 1,
    ) -> RouteVerdict:
        """Majority vote over `k` independent `route_plausible` calls (self-consistency).

        The early judge changes its mind on identical input — it fired on one guard case in
        1 of 3 replicates and on `EKR_GEN_030` in 1 of 3 (v21 §1.4, §1.5) — and a false fire
        destroys a case permanently while a true fire only probably gains one. So the fire
        condition is a strict majority of *post-guard* `implausible` verdicts: each vote has
        already passed the implausible->ambiguous downgrade guard inside `route_plausible`.

        The deployment is o-family and rejects `temperature`, so the votes differ only by the
        model's native sampling noise — which is exactly the noise being averaged out.

        `k=1` is the single-call path unchanged. `last_usage` is the sum over the k calls.
        """
        k = max(1, int(k))
        votes: list[RouteVerdict] = []
        total = {"total_tokens": 0, "completion_tokens": 0}
        for _ in range(k):
            # Reset first: a fail-open raises before `_call` records usage, and the stale
            # value from the previous vote would otherwise be counted twice.
            self.last_usage = {"total_tokens": 0, "completion_tokens": 0}
            votes.append(self.route_plausible(question, chosen_route, has_address_flag,
                                              dialogue_summary, about_to_request_address))
            for key in total:
                total[key] += int((self.last_usage or {}).get(key, 0) or 0)
        self.last_usage = total

        implausible = [v for v in votes if v.verdict == "implausible"]
        if len(implausible) * 2 > k:
            return implausible[0]   # hint comes from the first implausible vote
        survivors = [v for v in votes if v.verdict != "implausible"]
        return survivors[0] if survivors else votes[0]

    def answer_quality(
        self,
        question: str,
        retrieved_evidence: dict,
        final_answer: str,
        identified_building: dict | None = None,
    ) -> AnswerVerdict:
        """`identified_building` is the system's OWN identity resolution
        (`state.metadata.building_identity_check`), not gold — the same class of input as
        `route_plausible`'s `about_to_request_address`. Without it the judge cannot see the
        dominant real failure: with several records under one address string, an answer that
        quotes the wrong building's figures is faithful to the evidence and passes every
        axis (v24 §1.1). Only the four fields in `_IDENTITY_KEYS` are forwarded."""
        # A dict of empty containers holds no evidence: {"generic_sql": []} is
        # truthy but there is nothing in it for faithfulness to check against.
        evidence_present = any(bool(v) for v in (retrieved_evidence or {}).values())
        ident = ({k: (identified_building or {}).get(k) for k in _IDENTITY_KEYS}
                 if (identified_building or {}).get("matched_building_id") else None)
        try:
            raw = self._call(self._ap, json.dumps(
                {"question": question, "retrieved_evidence": retrieved_evidence,
                 "identified_building": ident,
                 "final_answer": final_answer}, ensure_ascii=False, default=str))
            p = _parse(raw)
            if p is None:
                raise ValueError(f"JSON failure: {raw[:200]!r}")
            axes, composite, verdict = score_axes(
                p.get("axes") or {}, evidence_present,
                entity_scorable=bool(ident) and evidence_present)
            return AnswerVerdict(
                verdict=verdict, axes=axes, composite=composite,  # type: ignore
                evidence_present=evidence_present,
                stage_attribution_judge=str(p.get("stage_attribution") or "unknown"),
                stage_attribution_rule=_stage_rule(verdict, axes),
                attribution_diagnostic=_attribution_diagnostic(verdict, axes, evidence_present),
                corrective_hint=p.get("corrective_hint") or None, raw_json=p,
            )
        except Exception as e:
            logger.warning("answer_quality fail-open: %s", str(e)[:200])
            return AnswerVerdict(verdict="pass",
                                 axes={"_fail_open": True, "_error": str(e)[:200]},
                                 composite=0.0, evidence_present=evidence_present,
                                 stage_attribution_judge="unknown",
                                 stage_attribution_rule="unknown", corrective_hint=None)

    def _is_o4_family(self) -> bool:
        """Whether this deployment needs the o-family parameter set (see `is_o_family`)."""
        return is_o_family(self._dep)

    def _call(self, system: str, user: str) -> str:
        """One chat completion, returning raw text and recording usage in `last_usage`."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        params: dict = {"model": self._dep, "messages": messages}
        if self._is_o4_family():
            # o4/o3: no temperature, max_completion_tokens (not max_tokens), no top_p.
            params["max_completion_tokens"] = _MAX_COMPLETION_TOKENS
        else:
            params["temperature"] = 0
            params["max_tokens"] = _MAX_COMPLETION_TOKENS
        try:
            r = self._client.chat.completions.create(**params)
        except BadRequestError as e:
            # Surface the underlying API error instead of swallowing it silently.
            # Common case: an older Azure api_version rejects a param mix on o4 deployments.
            msg = str(e)
            # If a non-o4 model still rejects temperature, retry without it.
            if "temperature" in msg and "temperature" in params:
                params.pop("temperature", None)
                logger.warning("InLoopEvaluator: retrying without temperature after 400: %s", msg[:160])
                r = self._client.chat.completions.create(**params)
            else:
                logger.error("InLoopEvaluator BadRequestError: %s", msg[:300])
                raise
        usage = getattr(r, "usage", None)
        try:
            self.last_usage = {
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            }
        except (TypeError, ValueError):
            # Non-numeric usage (e.g. a mock in tests) -> zeroed; never trigger fail-open.
            self.last_usage = {"total_tokens": 0, "completion_tokens": 0}
        return r.choices[0].message.content or ""
