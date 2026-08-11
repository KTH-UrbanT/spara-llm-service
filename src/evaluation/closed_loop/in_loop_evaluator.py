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
    raw_json: dict = field(default_factory=dict)


def _parse(content: str) -> dict | None:
    c = re.sub(r"^```(?:json)?\s*", "", content.strip())
    c = re.sub(r"\s*```$", "", c)
    try:
        return json.loads(c)
    except (json.JSONDecodeError, ValueError):
        return None


def _stage_rule(verdict: str, axes: dict) -> str:
    """Attribution rule. Missing axes are treated as 0 — consistent with the
    floor check in answer_quality — so an omitted axis is attributed to its
    stage rather than masked. When a fail is due purely to the composite (every
    axis at or above the floor), the failing stage is indeterminate -> 'unknown'."""
    vals = {a: axes.get(a, 0) for a in _AXIS_ORDER}
    if verdict == "fail" and all(v >= TAU_AXIS_FLOOR for v in vals.values()):
        return "unknown"
    lowest = min(_AXIS_ORDER, key=lambda a: vals[a])
    return _AXIS_TO_STAGE.get(lowest, "unknown")


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
    ) -> RouteVerdict:
        try:
            raw = self._call(self._rp, json.dumps(
                {"question": question, "chosen_route": chosen_route,
                 "has_address_flag": has_address_flag}, ensure_ascii=False))
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
        try:
            raw = self._call(self._ap, json.dumps(
                {"question": question, "retrieved_evidence": retrieved_evidence,
                 "final_answer": final_answer}, ensure_ascii=False, default=str))
            p = _parse(raw)
            if p is None:
                raise ValueError(f"JSON failure: {raw[:200]!r}")
            axes = {k: int(val) for k, val in (p.get("axes") or {}).items()}
            vals = [axes.get(a, 0) for a in _AXIS_ORDER]
            composite = sum(vals) / len(vals) if vals else 0.0
            floor_fail = any(axes.get(a, 0) < TAU_AXIS_FLOOR for a in _AXIS_ORDER)
            verdict = "fail" if (floor_fail or composite < TAU_SEMANTIC) else "pass"
            return AnswerVerdict(
                verdict=verdict, axes=axes, composite=composite,  # type: ignore
                stage_attribution_judge=str(p.get("stage_attribution") or "unknown"),
                stage_attribution_rule=_stage_rule(verdict, axes),
                corrective_hint=p.get("corrective_hint") or None, raw_json=p,
            )
        except Exception as e:
            logger.warning("answer_quality fail-open: %s", str(e)[:200])
            return AnswerVerdict(verdict="pass",
                                 axes={"_fail_open": True, "_error": str(e)[:200]},
                                 composite=0.0, stage_attribution_judge="unknown",
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
            params["max_completion_tokens"] = 2000
        else:
            params["temperature"] = 0
            params["max_tokens"] = 2000
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
