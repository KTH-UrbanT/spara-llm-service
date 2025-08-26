# sql_client.py
from typing import Any, Dict, List, Optional, Tuple, Union
import requests

Row = Dict[str, Any]
Filter = Tuple[str, str, Union[str, List[str]]]


class SQLClient:
    """
    ODEN buildings client exposing four explicit APIs:

      1) building_by_uuid(building_uuid) -> Optional[Row]
      2) building_by_address(address, ...) -> List[Row]
      3) buildings_by_single_filter(field, op, value, ...) -> List[Row]
      4) buildings_by_double_filter(f1, f2, ...) -> List[Row]

    Endpoints:
      - GET /buildings/{building_uuid}/
      - GET /buildings/single_filter?filter_name=<field>&filter_value=<value>[&op=<op>]
      - GET /buildings/?<field>=... or <field>__<lookup>=... (AND filters)

    Allowed fields (exact strings):
      "byggnadsid" "01a_fnr" "50a_uuid" "50a_deso"
      "epc_egennybyggar" "epc_egenbyggnadstyp" "epc_egenatemp"
      "epc_egenantalplan" "epc_egenantaltrapphus" "epc_idadr"

    Notes on return shapes:
      - All *list* retrieval methods (#2–#4) normalize the HTTP JSON to List[Row],
        even if the server sometimes responds with a pagination dict.
      - ID lookup (#1) returns a single Row (dict) or None.
    """

    BASE_PATH = "/buildings/"
    SINGLE_FILTER_PATH = "/buildings/single_filter"

    ALLOWED_FIELDS = {
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
    }

    # Multi-filter lookup mapping (DRF-style)
    OP_TO_LOOKUP = {
        "eq": "",
        "exact": "",
        "icontains": "icontains",
        "contains": "icontains",
        "istartswith": "istartswith",
        "startswith": "istartswith",
        "in": "in",
    }

    # Single-filter ops (passed verbatim as ?op=)
    SINGLE_FILTER_ALLOWED_OPS = {"eq", "exact", "icontains", "contains", "istartswith", "startswith", "in"}

    def __init__(
        self,
        base_url: str = "https://oden.abe.kth.se/api/v1",
        token: Optional[str] = None,
        timeout: int = 15,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "oden-sql-client/2.1",
            }
        )
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})

    # 1) Get building by ID (UUID) -> single row
    def building_by_uuid(self, building_uuid: str) -> Optional[Row]:
        url = f"{self.base_url}{self.BASE_PATH}{building_uuid}/"
        r = self.session.get(url, timeout=self.timeout)
        if not r.ok:
            return None
        try:
            data = r.json()
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    # Optional alias
    def building_by_id(self, building_uuid: str) -> Optional[Row]:
        return self.building_by_uuid(building_uuid)

    # 2) Get buildings by address (tiered single_filter on epc_idadr) -> list
    def building_by_address(
        self,
        address: str,
        limit: int = 10,
        offset: int = 0,
        ordering: Optional[str] = None,
    ) -> List[Row]:
        tiers: List[str] = ["eq", "istartswith", "icontains"]
        for op in tiers:
            rows = self.buildings_by_single_filter(
                field="epc_idadr",
                op=op,
                value=address,
                limit=limit,
                offset=offset,
                ordering=ordering,
            )
            if rows:
                return rows
        return []

    # 3) Get buildings by a single filter (exactly /buildings/single_filter) -> list
    def buildings_by_single_filter(
        self,
        field: str,
        op: str,
        value: Union[str, List[str]],
        limit: int = 50,
        offset: int = 0,
        ordering: Optional[str] = None,
    ) -> List[Row]:
        self._validate_field(field)
        self._validate_single_op(op)

        params: Dict[str, Any] = {
            "filter_name": field,
            "filter_value": ",".join(map(str, value)) if (op == "in" and isinstance(value, list)) else str(value),
            "op": op,
            "limit": int(limit),
            "offset": int(offset),
        }
        if ordering:
            params["ordering"] = ordering

        url = f"{self.base_url}{self.SINGLE_FILTER_PATH}"
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return self._json_to_list(r.json())

    # 4) Get buildings by double filter (AND) via /buildings/ -> list
    def buildings_by_double_filter(
        self,
        f1: Filter,
        f2: Filter,
        limit: int = 50,
        offset: int = 0,
        ordering: Optional[str] = None,
    ) -> List[Row]:
        params = self._compose_params([f1, f2], limit=limit, offset=offset, ordering=ordering)
        url = f"{self.base_url}{self.BASE_PATH}"
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return self._json_to_list(r.json())

    # ---------- Internals ----------

    def _compose_params(
        self,
        filters: List[Filter],
        limit: int,
        offset: int,
        ordering: Optional[str],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        for field, op, value in filters:
            self._validate_field(field)
            lookup = self._lookup_suffix(op)
            key = f"{field}__{lookup}" if lookup else field

            if lookup == "in":
                if isinstance(value, list):
                    params[key] = ",".join(map(str, value))
                else:
                    params[key] = str(value)
            else:
                params[key] = value

        params["limit"] = int(limit)
        params["offset"] = int(offset)
        if ordering:
            params["ordering"] = ordering
        return params

    def _validate_field(self, field: str) -> None:
        if field not in self.ALLOWED_FIELDS:
            raise ValueError(f"Unsupported field '{field}'. Allowed fields: {sorted(self.ALLOWED_FIELDS)}")

    def _validate_single_op(self, op: str) -> None:
        if op not in self.SINGLE_FILTER_ALLOWED_OPS:
            raise ValueError(
                f"Unsupported single_filter op '{op}'. Allowed: {sorted(self.SINGLE_FILTER_ALLOWED_OPS)}"
            )

    def _lookup_suffix(self, op: str) -> str:
        if op not in self.OP_TO_LOOKUP:
            raise ValueError(f"Unsupported lookup op '{op}'. Allowed: {sorted(self.OP_TO_LOOKUP.keys())}")
        return self.OP_TO_LOOKUP[op]

    @staticmethod
    def _json_to_list(payload: Union[List[Row], Dict[str, Any], None]) -> List[Row]:
        """
        Normalize various server responses to a List[Row]:
          - If list -> return as-is.
          - If dict with 'results' list -> return that.
          - If dict (single object) -> wrap into a list.
          - Else -> [].
        """
        if payload is None:
            return []
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            results = payload.get("results")
            if isinstance(results, list):
                return results
            # Sometimes APIs return a single object dict
            return [payload]
        return []
