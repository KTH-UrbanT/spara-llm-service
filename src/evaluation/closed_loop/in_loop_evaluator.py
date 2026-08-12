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

# On o3/o4 deployments reasoning tokens count against this budget, so a value sized
# for the JSON alone returns an empty completion — which the fail-open then converts
# into a silent pass. Sized for reasoning + the verdict.
_MAX_COMPLETION_TOKENS = 6000

_AXIS_ORDER = ["faithfulness", "answer_relevance", "question_coverage", "calibration"]
_AXIS_TO_STAGE = {"faithfulness": "summarizer", "answer_relevance": "router",
                  "question_coverage": "specialists", "calibration": "summarizer"}


@dataclass
class RouteVerdict:
    verdict: Literal["plausible", "implausible", "ambiguous"]
    axes: dict
    corrective_hint: str | None
    raw_json: dict = field(default_factory=dict)


@dataclass
class AnswerVerdict:
    verdict: Literal["pass", "fail"]
    axes: dict
    composite: float
    stage_attribution_judge: str   # the judge's own guess (recorded for analysis)
    stage_attribution_rule: str    # deterministic rule (drives the controller)
    corrective_hint: str | None
    # False when the judge was handed no evidence. Recorded on every verdict so a
    # dead upstream retrieval channel shows up as a flag instead of disappearing
    # into a low quality score (§1.3).
    evidence_present: bool = True
    raw_json: dict = field(default_factory=dict)


def _parse(content: str) -> dict | None:
    c = re.sub(r"^```(?:json)?\s*", "", content.strip())
    c = re.sub(r"\s*```$", "", c)
    try:
        return json.loads(c)
    except (json.JSONDecodeError, ValueError):
        return None


def _applicable(axes: dict) -> dict:
    """The axes the pass rule and the attribution rule apply over.

    An axis explicitly set to None is *not applicable* — today only
    `faithfulness`, when there was no evidence to check the answer against
    (see `_score`) — and is dropped rather than read as a zero. An axis the
    judge simply *omitted* still counts as 0, so a missing score is attributed
    to its stage rather than masked.
    """
    return {a: (axes[a] if a in axes else 0)
            for a in _AXIS_ORDER if not (a in axes and axes[a] is None)}


def _score(raw_axes: dict, evidence_present: bool) -> tuple[dict, float, str]:
    """Parse the judge's axes and apply the pre-registered pass rule.

    `faithfulness` asks whether the answer's claims are supported by the
    retrieved evidence. With no evidence there is nothing to support them
    against, so the axis has no input: it is recorded as None ("not measured")
    rather than scored 0. A 0 would both drag the composite down and trip the
    floor, failing correct answers because of an upstream retrieval outage
    (§1.2). TAU_SEMANTIC and TAU_AXIS_FLOOR are unchanged — only the set of
    axes they are applied over changes.

    Returns (axes, composite, verdict).
    """
    # None is the judge saying "not applicable" — keep it, never int() it.
    axes = {k: (None if v is None else int(v)) for k, v in (raw_axes or {}).items()}
    if not evidence_present:
        axes["faithfulness"] = None

    vals = _applicable(axes)
    composite = sum(vals.values()) / len(vals) if vals else 0.0
    floor_fail = any(v < TAU_AXIS_FLOOR for v in vals.values())
    verdict = "fail" if (floor_fail or composite < TAU_SEMANTIC) else "pass"
    return axes, composite, verdict


def _stage_rule(verdict: str, axes: dict) -> str:
    """Attribution rule, over the applicable axes only (see `_applicable`).

    When a fail is due purely to the composite (every applicable axis at or
    above the floor), the failing stage is indeterminate -> 'unknown'."""
    vals = _applicable(axes)
    if not vals:
        return "unknown"
    if verdict == "fail" and all(v >= TAU_AXIS_FLOOR for v in vals.values()):
        return "unknown"
    return _AXIS_TO_STAGE.get(min(vals, key=lambda a: vals[a]), "unknown")


class InLoopEvaluator:
    def __init__(self) -> None:
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
        self._rp = (d / os.environ.get("ROUTE_PLAUSIBILITY_PROMPT_VERSION", "route_plausibility_v1.txt")).read_text()
        self._ap = (d / os.environ.get("ANSWER_QUALITY_PROMPT_VERSION", "answer_quality_v1.txt")).read_text()
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

    def answer_quality(
        self,
        question: str,
        retrieved_evidence: dict,
        final_answer: str,
    ) -> AnswerVerdict:
        # A dict of empty containers holds no evidence: {"generic_sql": []} is
        # truthy but there is nothing in it for faithfulness to check against.
        evidence_present = any(bool(v) for v in (retrieved_evidence or {}).values())
        try:
            raw = self._call(self._ap, json.dumps(
                {"question": question, "retrieved_evidence": retrieved_evidence,
                 "final_answer": final_answer}, ensure_ascii=False, default=str))
            p = _parse(raw)
            if p is None:
                raise ValueError(f"JSON failure: {raw[:200]!r}")
            axes, composite, verdict = _score(p.get("axes") or {}, evidence_present)
            return AnswerVerdict(
                verdict=verdict, axes=axes, composite=composite,  # type: ignore
                evidence_present=evidence_present,
                stage_attribution_judge=str(p.get("stage_attribution") or "unknown"),
                stage_attribution_rule=_stage_rule(verdict, axes),
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
        """o3/o4 deployments reject `temperature`, `top_p`, `frequency_penalty`,
        `presence_penalty`, and `max_tokens` — they require `max_completion_tokens`
        instead, and the API-version check fires on certain param combinations."""
        name = (self._dep or "").lower()
        return ("o4" in name) or ("o3" in name)

    def _call(self, system: str, user: str) -> str:
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
