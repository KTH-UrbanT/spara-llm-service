# generic_sql_layer.py
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.database.sql_client import SQLClient  # type: ignore
from src.pipeline.telemetry import record_sql_call

Row = Dict[str, Any]


@dataclass
class RouteDecision:
    op: str
    kwargs: Dict[str, Any]
    reason: str = ""


class SQL_Mapper_Layer:
    """
    Mapper layer that decides *what* to fetch and calls SQLClient.
    Updated routing:
      1) If metadata.address present -> building_by_address
      2) Else if any filterable metadata key present -> buildings_by_single_filter (op='eq')
      3) Else if building_id present -> fetch profile/field by UUID
      4) Else try to infer address from text; otherwise noop
    """

    # Keys we’ll try to extract when user asks for specific fields
    ENERGY_CLASS_KEYS = [
        "energy_class", "energy_label", "energiklass",
        "energideklaration_klass", "energideklaration_energiprestandaklass",
        "energiklass_brf", "energyclass",
    ]
    HEATED_AREA_KEYS = [
        "heated_area", "atemp", "Atemp", "area", "heatedArea",
        "total_heated_area", "boarea", "boyta", "bruttoarea",
    ]
    TARIFF_KEYS = [
        "tariff", "grid_tariff", "elnät_tariff", "network_tariff",
        "gridTariff", "tariff_kundkategori",
    ]

    # If any of these appear in metadata, we’ll call buildings_by_single_filter(field, 'eq', value)
    FILTERABLE_FIELDS = [
        "byggnadsid",
        "01a_fnr",
        "50a_uuid",
        "50a_deso",
        "epc_egennybyggar",
        "epc_egenbyggnadstyp",
        "epc_egenatemp",
        "epc_egenantalplan",
        "epc_egenantaltrapphus",
        "epc_idadr",
    ]

    def __init__(self, sql_client: Optional[SQLClient] = None):
        self.sql: SQLClient = sql_client or SQLClient()

    # ----------------------------
    # Public API
    # ----------------------------
    def route(self, state: Dict[str, Any]) -> RouteDecision:
        """
        Inspect the state and decide which op to execute.
        Expected state keys:
          - intent (optional)
          - last_message / messages (optional)
          - metadata: { address?, building_id?, <filterable fields>? }
        """
        md = state.get("metadata") or {}
        last_msg = self._get_last_message(state)

        # --- 1) Address-first: if address exists, search by address (even if a building_id also exists) ---
        address = (md.get("address") or "").strip()
        if address:
            return RouteDecision("building_by_address", {"address": address}, "Address present in metadata.")

        # --- 2) If any filterable field is present, use single_filter(eq) ---
        for field in self.FILTERABLE_FIELDS:
            if field in md and md[field] not in (None, ""):
                return RouteDecision(
                    "buildings_by_single_filter",
                    {"field": field, "op": "eq", "value": md[field]},
                    f"Metadata contained '{field}'.",
                )

        # --- 3) Otherwise, if we have a building_id/uuid, fetch by uuid (or topic-specific) ---
        building_id = md.get("building_id") or md.get("50a_uuid") or md.get("uuid") or md.get("byggnadsid")
        topic = self._classify_topic(last_msg)
        if building_id:
            if topic == "energy_class":
                return RouteDecision("fetch_energy_class", {"building_id": building_id}, "Fetch energy class by UUID.")
            if topic == "heated_area":
                return RouteDecision("fetch_heated_area", {"building_id": building_id}, "Fetch heated area by UUID.")
            if topic == "tariff":
                return RouteDecision("fetch_tariff", {"building_id": building_id}, "Fetch tariff by UUID.")
            return RouteDecision("fetch_building_data", {"building_id": building_id}, "Fetch full profile by UUID.")

        # --- 4) Try to extract an address from the text; if found, search by address ---
        inferred = self._maybe_extract_address(last_msg)
        if inferred:
            return RouteDecision("building_by_address", {"address": inferred}, "Address inferred from text.")

        return RouteDecision("noop", {}, "No address, id, or filterable metadata available.")

    def execute(self, op: str, **kwargs) -> Dict[str, Any]:
        """
        Execute an op. Always return: {"ok": bool, "data": Any, "message": str}
        """
        trace = {
            "_started_at": time.perf_counter(),
            "query_type": op,
            "operation": op,
            "filters_used": {},
            "building_id": kwargs.get("building_id"),
            "address": kwargs.get("address"),
            "execution_status": "unknown",
            "rows_returned": 0,
            "returned_values_used": {},
            "error_message": None,
        }
        try:
            if op == "noop":
                trace["execution_status"] = "noop"
                return self._result(True, None, "noop", trace=trace)

            if op in ("fetch_building_data", "building_by_uuid"):
                building_id = kwargs.get("building_id")
                if not building_id:
                    trace["execution_status"] = "error"
                    trace["error_message"] = "Missing required 'building_id'."
                    return self._result(False, None, "Missing required 'building_id'.", trace=trace)
                trace["filters_used"] = {"building_id": building_id}
                payload = self.sql.building_by_uuid(building_id)
                if payload is None:
                    trace["execution_status"] = "not_found"
                    trace["error_message"] = f"No building found for uuid '{building_id}'."
                    return self._result(False, None, f"No building found for uuid '{building_id}'.", trace=trace)
                trace["execution_status"] = "success"
                trace["rows_returned"] = 1
                trace["returned_values_used"] = payload if isinstance(payload, dict) else {}
                return self._result(True, payload, "OK", trace=trace)

            if op == "building_by_address":
                address = kwargs.get("address")
                if not address:
                    trace["execution_status"] = "error"
                    trace["error_message"] = "Missing required 'address'."
                    return self._result(False, None, "Missing required 'address'.", trace=trace)
                trace["filters_used"] = {"address": address}
                rows = self.sql.building_by_address(address)
                if not rows:
                    trace["execution_status"] = "not_found"
                    trace["error_message"] = f"No buildings matched address '{address}'."
                    return self._result(False, [], f"No buildings matched address '{address}'.", trace=trace)
                trace["execution_status"] = "success"
                trace["rows_returned"] = len(rows)
                trace["returned_values_used"] = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
                return self._result(True, rows, "OK", trace=trace)

            if op == "buildings_by_single_filter":
                field = kwargs["field"]
                operator = kwargs.get("op", "eq")
                value = kwargs["value"]
                trace["filters_used"] = {field: value, "operator": operator}
                rows = self.sql.buildings_by_single_filter(field, operator, value)
                trace["execution_status"] = "success"
                trace["rows_returned"] = len(rows or [])
                trace["returned_values_used"] = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
                return self._result(True, rows, "OK", trace=trace)

            if op == "buildings_by_building_id":
                building_id = kwargs.get("building_id")
                if not building_id:
                    trace["execution_status"] = "error"
                    trace["error_message"] = "Missing required 'building_id'."
                    return self._result(False, None, "Missing required 'building_id'.", trace=trace)
                trace["filters_used"] = {"byggnadsid": building_id}
                rows = self.sql.buildings_by_building_id(building_id)
                if not rows:
                    trace["execution_status"] = "not_found"
                    trace["error_message"] = f"No buildings matched byggnadsid '{building_id}'."
                    return self._result(False, [], f"No buildings matched byggnadsid '{building_id}'.", trace=trace)
                trace["execution_status"] = "success"
                trace["rows_returned"] = len(rows or [])
                trace["returned_values_used"] = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
                return self._result(True, rows, "OK", trace=trace)

            if op == "fetch_energy_class":
                return self._resolve_then_get_field(kwargs, self.ENERGY_CLASS_KEYS, trace=trace)

            if op == "fetch_heated_area":
                return self._resolve_then_get_field(kwargs, self.HEATED_AREA_KEYS, trace=trace)

            if op == "fetch_tariff":
                return self._resolve_then_get_field(kwargs, self.TARIFF_KEYS, trace=trace)

            trace["execution_status"] = "error"
            trace["error_message"] = f"Unknown op '{op}'."
            return self._result(False, None, f"Unknown op '{op}'.", trace=trace)

        except Exception as e:
            trace["execution_status"] = "error"
            trace["error_message"] = f"exception: {type(e).__name__}: {e}"
            return self._result(False, None, f"exception: {type(e).__name__}: {e}", trace=trace)

    # ----------------------------
    # Internals
    # ----------------------------
    def _resolve_then_get_field(self, kwargs: Dict[str, Any], candidates: List[str], *, trace: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Used by fetch_energy_class / fetch_heated_area / fetch_tariff.
        Accepts building_id; if only address is given, resolve to first match.
        """
        building_id = kwargs.get("building_id")
        address = kwargs.get("address")
        trace = trace or {}
        trace["filters_used"] = {
            key: value
            for key, value in {"building_id": building_id, "address": address}.items()
            if value not in (None, "")
        }

        if not building_id and address:
            rows = self.sql.building_by_address(address) or []
            first = self._first(rows)
            building_id = self._extract_field(first or {}, ["50a_uuid", "uuid", "building_id", "oden_uuid", "byggnadsid"])
            trace["rows_returned"] = len(rows)

        if not building_id:
            target = f"address '{address}'" if address else "unknown target"
            trace["execution_status"] = "error"
            trace["error_message"] = f"Could not resolve building_id from {target}."
            return self._result(False, None, f"Could not resolve building_id from {target}.", trace=trace)

        payload = self.sql.building_by_uuid(building_id)
        if not payload:
            trace["execution_status"] = "not_found"
            trace["error_message"] = f"No building found for uuid '{building_id}'."
            return self._result(False, None, f"No building found for uuid '{building_id}'.", trace=trace)

        value = self._extract_field(payload, candidates)
        if value is None:
            value = self._search_deep(payload, candidates)

        if value is None:
            trace["execution_status"] = "error"
            trace["error_message"] = f"Field not found. Tried keys: {candidates}"
            return self._result(False, None, f"Field not found. Tried keys: {candidates}", trace=trace)

        trace["building_id"] = building_id
        trace["execution_status"] = "success"
        trace["rows_returned"] = max(int(trace.get("rows_returned") or 0), 1)
        trace["returned_values_used"] = {"building_id": building_id, "value": value}
        return self._result(True, {"building_id": building_id, "value": value}, "OK", trace=trace)

    @staticmethod
    def _result(ok: bool, data: Any, message: str, *, trace: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = {"ok": ok, "data": data, "message": message}
        if trace is not None:
            started_at = trace.pop("_started_at", None)
            record_sql_call(
                component="generic_sql",
                latency_seconds=time.perf_counter() - started_at if started_at else None,
                row_count=trace.get("rows_returned"),
                success=ok and trace.get("execution_status") not in {"error", "not_found"},
                error=None if ok and trace.get("execution_status") not in {"error", "not_found"} else message,
            )
            result["trace"] = trace
        return result

    @staticmethod
    def _first(items: Optional[List[Row]]) -> Optional[Row]:
        if not items:
            return None
        return items[0]

    @staticmethod
    def _get_last_message(state: Dict[str, Any]) -> str:
        if isinstance(state.get("last_message"), str):
            return state["last_message"]
        msgs = state.get("messages") or []
        if msgs and isinstance(msgs[-1], dict):
            return msgs[-1].get("content") or ""
        return ""

    @staticmethod
    def _classify_topic(text: str) -> str:
        t = (text or "").lower()
        if any(k in t for k in ["energy class", "energy label", "energy rating", "energiklass", "energideklaration"]):
            return "energy_class"
        if any(k in t for k in ["heated area", "atemp", "m2", "m²", "sqm", "square meter", "area"]):
            return "heated_area"
        if any(k in t for k in ["tariff", "grid tariff", "elnät", "nätavgift", "network tariff"]):
            return "tariff"
        return "unknown"

    @staticmethod
    def _maybe_extract_address(text: str) -> Optional[str]:
        if not text:
            return None
        t = text.strip()
        if '"' in t:
            parts = [p for p in t.split('"') if p.strip()]
            for p in parts:
                if any(c.isdigit() for c in p) and " " in p:
                    return p.strip()
        import re
        m = re.search(r"([A-Za-zÅÄÖåäö\s\-]+)\s+(\d{1,4}[A-Za-z]?)", t)
        if m:
            return m.group(0).strip()
        return None

    @staticmethod
    def _extract_field(payload: Any, candidates: List[str]) -> Any:
        if payload is None:
            return None
        if isinstance(payload, list):
            payload = payload[0] if payload else None
            if payload is None:
                return None
        if isinstance(payload, dict):
            for key in candidates:
                if key in payload and payload[key] not in (None, ""):
                    return payload[key]
            lower = {str(k).lower(): v for k, v in payload.items()}
            for key in candidates:
                lk = key.lower()
                if lk in lower and lower[lk] not in (None, ""):
                    return lower[lk]
        return None

    @staticmethod
    def _search_deep(payload: Any, candidates: List[str]) -> Any:
        from collections import deque
        def norm_keys(d: Dict[str, Any]) -> Dict[str, Any]:
            return {str(k).lower(): v for k, v in d.items()}
        target_keys = {k.lower() for k in candidates}
        q = deque([payload])
        seen: set[int] = set()
        while q:
            node = q.popleft()
            if id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(node, dict):
                lk = norm_keys(node)
                for k, v in lk.items():
                    if k in target_keys and v not in (None, ""):
                        return v
                for v in node.values():
                    q.append(v)
            elif isinstance(node, list):
                for v in node:
                    q.append(v)
        return None
