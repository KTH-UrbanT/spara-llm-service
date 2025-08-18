
import os
import re
import json
import time
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Union , Sequence
import random

import pandas as pd
from dotenv import load_dotenv
from openai import AzureOpenAI, APIConnectionError, RateLimitError, APIStatusError

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Try to import a default query executor (your DB connector).
# You can also inject a callable in __init__(query_executor=...).
_DEFAULT_EXECUTOR = None
try:
    # Adjust these imports to match your actual connector module path
    from src.database.hammarby_data import query_executor as _DEFAULT_EXECUTOR  # type: ignore
except Exception:
    _DEFAULT_EXECUTOR = None


def _df_to_records(df: Optional[pd.DataFrame]) -> Optional[List[Dict[str, Any]]]:
    if df is None:
        return None
    try:
        # Ensure native Python types for JSON serializability
        return json.loads(df.to_json(orient="records"))
    except Exception:
        return df.to_dict(orient="records")


def _sanitize_literal(value: str, pattern: str) -> str:
    r"""
    Return a safely quoted SQL literal if it matches the allowed pattern,
    otherwise raise ValueError.

    pattern examples:
      - r'^[A-Za-z0-9_-]{1,64}$' for building_id
      - r'^\d{4}-\d{2}-\d{2}$' for YYYY-MM-DD
    """
    if value is None:
        raise ValueError("Missing required value.")
    if not re.match(pattern, str(value)):
        raise ValueError(f"Value '{value}' failed safety check.")
    return f"'{value}'"


def _only_selects(sql: str) -> bool:
    """
    Enforce the final SQL is a single SELECT (no mutations, no multi-statement).
    Also forbid referencing system catalogs (except INFORMATION_SCHEMA in discovery stage,
    which we don't run in the final execution anyway).
    """
    s = sql.strip().strip(";")
    # Must start with SELECT
    if not re.match(r"(?is)^\s*select\b", s):
        return False
    # Disallow dangerous keywords
    forbidden = r"(?is)\b(insert|update|delete|merge|drop|alter|create|exec|execute|;|--|/\*)"
    if re.search(forbidden, s):
        return False
    # Disallow system tables in final execution
    if re.search(r"(?is)\b(sys\.|msdb\.|master\.|tempdb\.|xp_)\b", s):
        return False
    return True


class SpecializedSQLLayer:
    """
    Builds SAFE read-only T-SQL using an LLM (gpt-35-turbo via Azure OpenAI),
    executes it via the provided `query_executor(query: str) -> Optional[pd.DataFrame]`,
    and returns normalized results.

    Public API (kept close to your generic layer style):
        - route(state) -> Tuple[str, Dict[str, Any]]
        - execute(op, kwargs) -> Dict[str, Any]

    Typical usage in your agent node:
        op, kwargs = specialized_layer.route(state)
        result = specialized_layer.execute(op, kwargs)
        if result["ok"]:
            # use result["data"]
    """

    DEFAULT_PROMPT_RELATIVE_PATH = os.path.join("..", "prompts", "specialized_sql_prompt.txt")
    

    def __init__(
        self,
        prompt_path: Optional[str] = None,
        query_executor: Optional[Callable[[str], Optional[pd.DataFrame]]] = None,
    ) -> None:
        # Load prompt
        self.prompt_template = self._load_prompt(prompt_path)

        # Configure DB executor
        self.query_executor = query_executor or _DEFAULT_EXECUTOR
        if not callable(self.query_executor):
            raise ValueError(
                "No valid query_executor supplied and no default could be imported. "
                "Pass query_executor=... that accepts a SQL string and returns a pandas DataFrame (for SELECT)."
            )

        # Initialize Azure OpenAI
        self._initialize_openai_client()

        # Known/allowed schema context (kept minimal and safe)
        # You can expand with more domain hints as needed.
        self.allowed_tables = [
            "dbo.buildings",
            "dbo.users",
            "dbo.electricity_enduses",
            "dbo.hvac_systems",
            "dbo.normalization_values",
            "dbo.meterings",
            "dbo.results",
        ]
        self.fk_notes = [
            "All child tables reference buildings.building_id.",
            "Primary building key: buildings.building_id.",
        ]
        self.max_retries = int(os.getenv("AZURE_OAI_MAX_RETRIES", "5"))

    # -------------------------
    # Initialization utilities
    # -------------------------
    def _load_prompt(self, path: Optional[str]) -> str:
        if path is None:
            script_dir = os.path.dirname(__file__)
            full_path = os.path.join(script_dir, self.DEFAULT_PROMPT_RELATIVE_PATH)
        else:
            full_path = path

        if not os.path.exists(full_path):
            raise ValueError(f"Prompt file not found at: {full_path}")

        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()
    def _chat_with_retries(self, messages: Sequence[Dict[str, Any]], **kwargs):
        for attempt in range(self.max_retries):
            try:
                return self.client.chat.completions.create(
                    model=self.deployment,
                    messages=messages,
                    **kwargs,
                )
            except (RateLimitError, APIStatusError) as e:
                # Only backoff on 429/5xx
                status = getattr(e, "status_code", None) or getattr(e, "status", None)
                if isinstance(e, RateLimitError) or status in (429, 500, 502, 503, 504):
                    retry_after = 0
                    resp = getattr(e, "response", None)
                    if resp:
                        # Azure usually sets Retry-After (seconds)
                        retry_after = int(resp.headers.get("retry-after", "0") or "0")
                    # exponential backoff with jitter, while respecting Retry-After
                    backoff = max(retry_after, min(2 ** attempt, 30)) + random.random()
                    time.sleep(backoff)
                    continue
                raise  # non-rate-limit error: bubble up
    def _initialize_openai_client(self) -> None:
        """
        Reads env vars and creates AzureOpenAI client.
        Falls back to GENERIC_* envs if SPECIALIZED_* are not set.
        """
        endpoint = os.getenv("AZURE_ENDPOINT")
        api_key = os.getenv("OPENAI_API_KEY")

        deployment = (
            os.getenv("SPECIALIZED_SQL_DEPLOYMENT_NAME")
            or os.getenv("GENERIC_MODEL_DEPLOYMENT_NAME")
        )
        api_version = (
            os.getenv("SPECIALIZED_SQL_API_VERSION")
            or os.getenv("GENERIC_MODEL_API_VERSION")
        )

        missing = []
        if not endpoint:
            missing.append("AZURE_ENDPOINT")
        if not api_key:
            missing.append("OPENAI_API_KEY")
        if not deployment:
            missing.append("SPECIALIZED_SQL_DEPLOYMENT_NAME (or GENERIC_MODEL_DEPLOYMENT_NAME)")
        if not api_version:
            missing.append("SPECIALIZED_SQL_API_VERSION (or GENERIC_MODEL_API_VERSION)")
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        self.deployment = deployment
        self.client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        logger.info(f"Specialized SQL model: {self.deployment} (API {api_version})")

    # -------------------------
    # Public interface
    # -------------------------
    def route(self, state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        Prepare an 'operation' and kwargs. The op is a single verb since the LLM
        will decide the exact SQL under constraints.
        """
        last_message: str = state.get("last_message", "") or ""
        ctx = state.get("context", {}) or {}
        ents = ctx.get("entities") or []
        md = state.get("metadata", {}) or {}

        return "llm_sql", {
            "question": last_message,
            "entities": ents,
            "address": md.get("address"),
            "building_id": md.get("building_id"),
        }

    def execute(self, op: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the requested op. Currently supports only 'llm_sql'.
        Returns a normalized dict: { ok, data, message }.
        """
        if op != "llm_sql":
            return {"ok": False, "data": None, "message": f"Unsupported op '{op}'."}

        try:
            start = time.time()
            plan = self._synthesize_sql_plan(
                question=str(kwargs.get("question") or ""),
                entities=kwargs.get("entities") or [],
                building_id=kwargs.get("building_id"),
                address=kwargs.get("address"),
            )
            logger.debug(f"LLM plan: {json.dumps(plan, ensure_ascii=False)}")

            # Basic validation
            sql_template: str = (plan.get("sql") or "").strip()
            expects_single_row: bool = bool(plan.get("expects_single_row", False))

            if not sql_template:
                return {"ok": False, "data": None, "message": "LLM did not return SQL."}

            # Substitute placeholders safely
            final_sql = self._render_sql(sql_template, plan, kwargs)

            # Final safety checks
            if not _only_selects(final_sql):
                return {"ok": False, "data": None, "message": "Unsafe SQL rejected."}

            # Execute
            df = self.query_executor(final_sql)
            data = _df_to_records(df)

            if data is None:
                return {"ok": False, "data": None, "message": "Query executed but returned no data or failed."}

            # Optionally collapse to single row
            if expects_single_row and isinstance(data, list):
                data = data[0] if data else None

            elapsed = time.time() - start
            logger.info(f"Specialized SQL executed in {elapsed:.2f}s")
            return {"ok": True, "data": data, "message": "ok"}

        except (APIConnectionError, RateLimitError, APIStatusError) as e:
            logger.error(f"Azure OpenAI error: {e}", exc_info=True)
            return {"ok": False, "data": None, "message": f"llm_error: {e}"}
        except ValueError as e:
            # Likely from sanitization or missing values
            return {"ok": False, "data": None, "message": f"input_error: {e}"}
        except Exception as e:
            logger.exception("Unexpected error in execute")
            return {"ok": False, "data": None, "message": f"unexpected_error: {e}"}

    # -------------------------
    # Core LLM flows
    # -------------------------
    def _synthesize_sql_plan(
        self,
        question: str,
        entities: List[Union[str, Dict[str, Any]]],
        building_id: Optional[str],
        address: Optional[str],
    ) -> Dict[str, Any]:
        """
        Calls the Azure OpenAI chat completion with a structured system prompt
        and a JSON instruction. Returns a dict with fields:
           - sql: str (contains {{BUILDING_ID}} / {{DATE_FROM}} / {{DATE_TO}} placeholders if relevant)
           - expects_single_row: bool
           - rationale: str
           - placeholders: { "BUILDING_ID": "required|optional", "DATE_FROM": "...", ...}
        """
        schema_payload = {
            "allowed_tables": self.allowed_tables,
            "foreign_keys": self.fk_notes,
            # Small domain hints (SAFE). Expand as your DB grows.
            "domain_hints": [
                "Common building-scoped key: building_id",
                "results often contains energy KPIs like energy class and yearly kWh aggregates",
                "hvac_systems may contain heating/ventilation system info",
                "meterings may contain time-series kWh with timestamps",
            ],
        }

        user_payload = {
            "question": question,
            "entities": entities,
            "known_values": {
                "building_id": building_id,
                "address": address,
            },
            "schema": schema_payload,
            "constraints": {
                "read_only": True,
                "dialect": "T-SQL (SQL Server)",
                "max_rows": 200,
                "require_building_scope_if_relevant": True,
            },
        }

        messages = [
            {"role": "system", "content": self.prompt_template},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

        # completion = self.client.chat.completions.create(
        #     model=self.deployment,
        #     messages=messages,
        #     temperature=0.1,
        #     top_p=0.2,
        #     max_tokens=500,
        # )
        completion = self._chat_with_retries(
            messages,
            temperature=0.1,
            top_p=0.2,
            max_tokens=300,   # see next section
        )

        content = completion.choices[0].message.content or ""

        # Expect JSON; if it's wrapped in markdown, extract code block.
        content_stripped = content.strip()
        if "```" in content_stripped:
            # Try to extract first json or sql-json block
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content_stripped, flags=re.S | re.M)
            if m:
                content_stripped = m.group(1)

        try:
            plan = json.loads(content_stripped)
        except Exception:
            # Fallback: try to locate "sql": "..." manually
            sql_match = re.search(r'"sql"\s*:\s*"([^"]+)"', content_stripped, flags=re.S)
            plan = {}
            if sql_match:
                plan["sql"] = sql_match.group(1)

        if not isinstance(plan, dict):
            plan = {}

        # Ensure minimal keys
        plan.setdefault("expects_single_row", False)
        plan.setdefault("rationale", "")
        plan.setdefault("placeholders", {})

        return plan

    def _render_sql(self, sql_template: str, plan: Dict[str, Any], kwargs: Dict[str, Any]) -> str:
        """
        Replace placeholders like {{BUILDING_ID}}, {{DATE_FROM}}, {{DATE_TO}},
        applying strict validation for literals.
        """
        rendered = sql_template

        # Building ID
        if "{{BUILDING_ID}}" in rendered:
            building_id = kwargs.get("building_id")
            if not building_id:
                raise ValueError("Missing building_id for building-scoped query.")
            safe_bid = _sanitize_literal(str(building_id), r"^[A-Za-z0-9_-]{1,64}$")
            rendered = rendered.replace("{{BUILDING_ID}}", safe_bid)

        # Optional dates
        if "{{DATE_FROM}}" in rendered:
            df_val = plan.get("defaults", {}).get("DATE_FROM") or kwargs.get("date_from")
            if not df_val:
                # If not provided, let the prompt default handle it; but if placeholder is present we must fill
                raise ValueError("DATE_FROM required but not provided.")
            safe_df = _sanitize_literal(str(df_val), r"^\d{4}-\d{2}-\d{2}$")
            rendered = rendered.replace("{{DATE_FROM}}", safe_df)

        if "{{DATE_TO}}" in rendered:
            dt_val = plan.get("defaults", {}).get("DATE_TO") or kwargs.get("date_to")
            if not dt_val:
                raise ValueError("DATE_TO required but not provided.")
            safe_dt = _sanitize_literal(str(dt_val), r"^\d{4}-\d{2}-\d{2}$")
            rendered = rendered.replace("{{DATE_TO}}", safe_dt)

        # Enforce TOP limiter if not present
        if re.search(r"(?is)\bselect\b\s+(?!top\s+\d+)", rendered):
            # Insert TOP 200 after SELECT
            rendered = re.sub(r"(?is)^\s*select\b", "SELECT TOP 200", rendered, count=1)

        # Remove trailing semicolons to avoid multi-statement hazards from the LLM
        rendered = rendered.strip().rstrip(";")

        return rendered



