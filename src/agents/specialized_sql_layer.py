"""
Specialized SQL Layer — simplified & always-executes via hammarby_data.

Environment (minimal):
- OPENAI_API_TYPE: "azure" | "openai" (default: "openai")
- OPENAI_API_KEY:  API key for the selected provider
- AZURE_ENDPOINT:  required when OPENAI_API_TYPE=azure (e.g., https://myres.openai.azure.com)

Optional:
- OPENAI_API_VERSION: Azure API version (default: 2025-01-01-preview)
- AZURE_DEPLOYMENT:  Azure *deployment name* (default: "gpt-4o-mini")
- OPENAI_MODEL:      OpenAI model name (default: "gpt-4o-mini")
- SCHEMA_JSON_PATH:  (diagnostics only)
- SPEC_SQL_PROMPT_PATH: path to specialized_sql_prompt.txt; if missing, fallback prompt below is used.

Behavior:
- route(state) -> ("answer_query", {"question": <str>, "building_id": <str|None>})
- execute("answer_query", ...) :
    1) Ask model for ONE SQL statement (read or write).
    2) ALWAYS call src.database.hammarby_data.query_executor(sql).
       - SELECT/CTE -> pandas.DataFrame -> data: list[dict]
       - INSERT/UPDATE/DELETE -> returns a short string message -> message: str
       - None -> error
    3) Return dict with ok, sql, and data/message.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Tuple
import json
from collections import defaultdict

# OpenAI clients (1.x SDK)
try:
    from openai import OpenAI, AzureOpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = object  # type: ignore
    AzureOpenAI = object  # type: ignore

DEFAULT_PROMPT = (
    "You are the SQL brain for the Hammarby energy/building dataset.\n\n"
    "GOAL\n"
    "- Given a natural-language question and optional metadata (like building_id), "
    "produce exactly ONE SQL statement that answers or performs the requested operation.\n\n"
    "RULES\n"
    "- Return ONLY the SQL statement. No backticks, no comments, no JSON, no explanations.\n"
    "- You MAY generate reads (SELECT/CTE) or writes (INSERT/UPDATE/DELETE). "
    "Use writes only when the user intent clearly requires a change.\n"
    "- Prefer safe, minimal-impact writes. Never drop or truncate tables unless the user explicitly demands it.\n"
    "- For large reads, include a reasonable limit (e.g., LIMIT 100 for Postgres or TOP 100 for SQL Server) "
    "unless the user asks for all rows.\n"
    "- Use the schema and naming conventions consistent with the project (e.g., dbo.<table> if applicable).\n"
    "- If a building_id is provided in metadata, use it to scope the query when relevant.\n"
    "- If the request is ambiguous, choose a reasonable interpretation (e.g., 'latest' -> ORDER BY time DESC + limit).\n"
    "- Do not invent nonexistent tables or columns.\n\n"
    "OUTPUT\n"
    "- ONE and only ONE SQL statement as plain text.\n"
)

@dataclass
class _Conf:
    api_type: str
    endpoint: str | None
    api_key: str
    model_or_deployment: str
    api_version: str | None
    is_azure: bool


class SpecializedSQLLayer:
    def __init__(self, *, execute_query: bool = True) -> None:
        # execute_query is ignored (we always execute), kept for compatibility with your test harness
        self._conf = self._load_conf()
        self._client = self._init_client(self._conf)
        self._system_prompt = self._load_prompt()
        self.schema_json = os.getenv("SCHEMA_JSON_PATH", "src/config/schema.json")
        self._schema_str = self._load_and_format_schema(self.schema_json, max_chars=8000)
        print(self._schema_str)

    # ---------- Config & Client ----------
    def _load_and_format_schema(self, path: str, max_chars: int = 8000) -> str:
        """
        Load schema.json (array of {schema, table, column, data_type, ...}) and
        condense to a compact prompt string like:
        dbo.buildings(building_id int PK, buildingName text, ...)
        dbo.meterings(uuid uuid PK, building_id int, year int, ...)
        Duplicates are removed; columns are ordered by col_ordinal when available.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                rows = json.load(f)
        except Exception as e:
            return f"(schema not available: {e})"

        # Group columns by (schema, table)
        tables: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for r in rows if isinstance(rows, list) else []:
            sch = str(r.get("schema") or "dbo")
            tbl = str(r.get("table") or "unknown")
            # Deduplicate duplicate rows (appearing in your file) by (column, data_type)
            col = str(r.get("column") or "")
            if not col:
                continue
            col_key = (col, str(r.get("data_type") or ""))
            # Keep latest occurrence by replacing same (col, dtype); we also track ordinal
            existing = { (c.get("column"), c.get("data_type")): i for i, c in enumerate(tables[(sch, tbl)]) }
            idx = existing.get(col_key)
            if idx is None:
                tables[(sch, tbl)].append(r)
            else:
                tables[(sch, tbl)][idx] = r

        # Build lines like: dbo.table(col type [PK], ...)
        lines: list[str] = []
        for (sch, tbl), cols in sorted(tables.items()):
            # Order by col_ordinal if present, else by column name
            def _ord(x):
                try:
                    return int(x.get("col_ordinal") or 10**6)
                except Exception:
                    return 10**6
            cols_sorted = sorted(cols, key=lambda x: (_ord(x), str(x.get("column"))))

            parts = []
            for c in cols_sorted:
                name = str(c.get("column"))
                dtype = str(c.get("data_type") or "").replace(" without time zone", "")
                pk = c.get("is_primary_key") is True
                piece = f"{name} {dtype}" if dtype else name
                if pk:
                    piece += " PK"
                parts.append(piece)

            line = f"{sch}.{tbl}(" + ", ".join(parts) + ")"
            lines.append(line)

        text = "\n".join(lines)
        if len(text) > max_chars:
            # Truncate softly to avoid token bloat
            text = text[:max_chars].rsplit("\n", 1)[0] + "\n... (schema truncated)"
        return text or "(schema empty)"

    def _load_conf(self) -> _Conf:
        api_type = os.getenv("OPENAI_API_TYPE", "openai").strip().lower()
        if api_type not in {"azure", "openai"}:
            raise ValueError(f"OPENAI_API_TYPE must be 'azure' or 'openai' (got {api_type!r})")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required.")

        if api_type == "azure":
            endpoint = os.getenv("AZURE_ENDPOINT")
            if not endpoint:
                raise RuntimeError("AZURE_ENDPOINT is required when OPENAI_API_TYPE=azure.")
            api_version = os.getenv("OPENAI_API_VERSION", "2025-01-01-preview")
            deployment = os.getenv("AZURE_DEPLOYMENT", "gpt-4o-mini")
            return _Conf(
                api_type=api_type,
                endpoint=endpoint,
                api_key=api_key,
                model_or_deployment=deployment,
                api_version=api_version,
                is_azure=True,
            )

        # OpenAI (non-Azure)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        return _Conf(
            api_type=api_type,
            endpoint=None,
            api_key=api_key,
            model_or_deployment=model,
            api_version=None,
            is_azure=False,
        )

    def _init_client(self, conf: _Conf):
        if conf.is_azure:
            try:
                return AzureOpenAI(
                    azure_endpoint=conf.endpoint,
                    api_key=conf.api_key,
                    api_version=conf.api_version,
                )
            except Exception as e:  # pragma: no cover
                raise RuntimeError(
                    "Failed to initialize AzureOpenAI client. "
                    "Check AZURE_ENDPOINT / OPENAI_API_KEY / OPENAI_API_VERSION."
                ) from e
        else:
            try:
                return OpenAI(api_key=conf.api_key)
            except Exception as e:  # pragma: no cover
                raise RuntimeError("Failed to initialize OpenAI client. Check OPENAI_API_KEY.") from e

    def _load_prompt(self) -> str:
        path = os.getenv("SPEC_SQL_PROMPT_PATH")
        if not path:
            # default to a file named specialized_sql_prompt.txt next to this module
            here = os.path.dirname(__file__)
            path = os.path.join("./src/prompts/specialized_sql_prompt.txt")
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = f.read().strip()
                return txt if txt else DEFAULT_PROMPT
        except Exception:
            return DEFAULT_PROMPT
        


    # ---------- Routing ----------

    def route(self, state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        last_message = (state or {}).get("last_message") or ""
        metadata = (state or {}).get("metadata") or {}
        building_id = metadata.get("building_id")
        address = metadata.get("address")  # <-- NEW

        kwargs = {
            "question": str(last_message),
            "building_id": None if building_id is None else str(building_id),
            "address": None if address is None else str(address),   # <-- NEW
        }
        return "answer_query", kwargs

    # ---------- Execution ----------

    def execute(self, op: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if op != "answer_query":
            return {"ok": False, "message": f"Unknown operation: {op}"}

        # inside SpecializedSQLLayer.execute(...)
        question = kwargs.get("question", "")
        building_id = kwargs.get("building_id")
        address = kwargs.get("address")

        meta_lines = []
        if building_id is not None:
            meta_lines.append(f"building_id={building_id}")
        if address is not None:
            meta_lines.append(f"address={address!r}")
        metadata_block = " | ".join(meta_lines) if meta_lines else "none"

        combined_system = (
            self._system_prompt.strip()
            + "\n\nSCHEMA:\n"
            + (self._schema_str or "(schema not available)")
            + "\n\nMETADATA:\n"
            + metadata_block
        )

        messages = [
            {"role": "system", "content": combined_system},
            {"role": "user", "content": str(question)},
        ]


        # Ask the model for ONE SQL statement
        try:
            resp = self._client.chat.completions.create(
                model=self._conf.model_or_deployment,
                messages=messages,
                temperature=0.0,
                top_p=1.0,
                max_tokens=600,
            )
        except Exception as e:
            msg = str(e)
            if self._conf.is_azure and any(t in msg for t in ["401", "Unauthorized", "authorization"]):
                msg += (
                    "\nHint: Azure mode detected. Verify OPENAI_API_KEY is your Azure key, "
                    f"AZURE_ENDPOINT='{self._conf.endpoint}', and that AZURE_DEPLOYMENT="
                    f"'{self._conf.model_or_deployment}' exists."
                )
            return {"ok": False, "message": f"Model error: {msg}"}

        try:
            content = resp.choices[0].message.content  # type: ignore[attr-defined]
        except Exception:
            content = str(resp)

        sql = self._extract_sql(content)
        if not sql:
            return {"ok": False, "message": "Model did not return SQL."}

        # ALWAYS execute via hammarby_data
        try:
            db = self._safe_import_hammarby()
        except Exception as e:
            return {"ok": False, "message": f"Import error: {e}", "sql": sql}

        try:
            result = db.query_executor(sql)  # SELECT -> DataFrame; writes -> str; errors -> None
        except Exception as e:
            return {"ok": False, "message": f"DB error: {e}", "sql": sql}

        # Normalize result
        try:
            import pandas as pd  # type: ignore
        except Exception:
            pd = None  # type: ignore

        if result is None:
            return {"ok": False, "message": "DB returned no result.", "sql": sql}

        # If it's a pandas DataFrame (read query)
        if (pd is not None) and hasattr(result, "to_dict"):
            try:
                data = result.to_dict(orient="records")  # type: ignore
                return {"ok": True, "data": data, "sql": sql}
            except Exception:
                pass  # fall through if not a real DF

        # If it's a string (write queries return a message)
        if isinstance(result, str):
            return {"ok": True, "message": result, "sql": sql}

        # Fallback: try to coerce iterables of rows
        try:
            data = list(result)  # may raise
            return {"ok": True, "data": data, "sql": sql}
        except Exception:
            # As a last resort, just stringify it
            return {"ok": True, "message": str(result), "sql": sql}

    # ---------- Helpers ----------

    @staticmethod
    def _safe_import_hammarby():
        # Lazy import so the module can be used without DB in other contexts.
        try:
            from src.database import hammarby_data as db  # type: ignore
        except Exception as e:
            raise ImportError(
                "Could not import src.database.hammarby_data. "
                "Ensure your PYTHONPATH/repo layout matches and the module exists."
            ) from e
        if not hasattr(db, "query_executor"):
            raise ImportError("hammarby_data module missing query_executor(sql) function.")
        return db

    @staticmethod
    def _extract_sql(text: str) -> str:
        """
        Extract ONE SQL statement from model output.
        - Prefer fenced ```sql ... ``` or ``` ... ``` blocks.
        - Else, take the first semicolon-terminated statement or the whole trimmed text.
        - Strip any leading/trailing backticks.
        """
        if not text:
            return ""

        # 1) ```sql ... ``` fenced
        m = re.search(r"```sql\\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()

        # 2) ``` ... ``` fenced
        m = re.search(r"```\\s*(.*?)```", text, flags=re.DOTALL)
        if m:
            return m.group(1).strip()

        # 3) First semicolon-terminated statement
        m = re.search(r";", text)
        if m:
            first_stmt = text[: m.end()].strip()
            # Avoid capturing stray backticks
            return first_stmt.strip("`").strip()

        # 4) Fallback: whole text
        return text.strip("`").strip()
