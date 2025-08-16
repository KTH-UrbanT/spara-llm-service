from typing import Dict, Any, Optional, Tuple
# Swap to your real client in prod; use the mock in dev if you want
# from src.database.sql_client import SQLClient
from src.database.sql_client import MockSQLClient  # <- dev only


class SQL_Mapper_Layer:
    def __init__(self):
        # self.sql = SQLClient()
        self.sql = MockSQLClient("./src/data/buildings_augmented.csv")  # dev

    # ---------- Public API ----------
    def route(self, state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        context = state.get("context", {}) or {}
        intent = (context.get("intent") or "").lower()
        metadata = state.get("metadata", {}) or {}
        last_message = (state.get("last_message") or "").lower()
        entities = [e.lower() for e in (context.get("entities") or [])]

        if intent not in ("query generic database", "generic_sql"):
            return ("noop", {})

        building_id = metadata.get("building_id")
        address = metadata.get("address")
        if not (building_id or address):
            thread_id = state.get("thread_id") or ""
            inferred_address = self.sql.get_address_by_username(thread_id)
            if inferred_address:
                address = inferred_address
                metadata["address"] = address  # reflect back into state

        topic = self._classify_topic(last_message, entities)

        if building_id:
            return self._route_with_building_id(topic, building_id)

        if address:
            return ("building_by_address", {"extracted": address})

        return ("missing_address", {})

    def execute(self, op: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if op == "building_by_address":
                resp = self.sql.building_by_address(kwargs["extracted"])

                # Case 1: requests.Response-like
                if hasattr(resp, "status_code"):
                    if 200 <= resp.status_code < 300:
                        return {"ok": True, "data": resp.json()}
                    return {"ok": False, "data": None, "message": f"Lookup failed: {resp.status_code}"}

                # Case 2: mock list/dict
                if isinstance(resp, list):
                    return {"ok": True, "data": resp}
                if isinstance(resp, dict):
                    return {"ok": True, "data": [resp]}

                return {"ok": False, "data": None, "message": "unexpected_building_by_address_response"}

            elif op == "fetch_building_profile":
                data = self.sql.fetch_building_data(kwargs["building_id"])
                return {"ok": True, "data": data}

            # Prefer your confirmed columns, then fallbacks
            elif op == "fetch_energy_rating":
                payload = self.sql.fetch_building_data(kwargs["building_id"])
                data = self._extract_field(payload, [
                    "EgiEnergiklass2020_calc",                     # your column
                    "energy_class", "energy_rating", "epc_class",
                    "Energiklass", "EnergideklarationKlass",
                    "EgiEnergianvandning_Eindex_calc",             # fallback index
                ])
                return {"ok": True, "data": {"energy_class": data}}

            elif op == "fetch_heated_area":
                payload = self.sql.fetch_building_data(kwargs["building_id"])
                data = self._extract_field(payload, [
                    "heated_area_m2", "area_m2", "boa_m2", "boarea",
                    "Atemp", "ATEMP",
                    "EgenAtemp", "EgenAtempBostad", "EgenAtempBad", "EgenAtempButik",
                    "EgiAtemp", "EgenAtempTotal",
                ])
                return {"ok": True, "data": {"heated_area_m2": data}}

            elif op == "fetch_heating_type":
                payload = self.sql.fetch_building_data(kwargs["building_id"])
                data = self._extract_field(payload, [
                    "HuvudsakligUppvarmning_calc",                # your column
                    "heating_type", "heating_system", "primary_heating",
                    "Uppvarmningssatt", "Uppvärmningssätt",
                    "Fjärrvärme", "Fjarrvarme", "Bergvärme", "Luftvärmepump",
                ])
                return {"ok": True, "data": {"heating_type": data}}

            elif op == "fetch_yearly_usage":
                payload = self.sql.fetch_building_data(kwargs["building_id"])
                data = self._extract_field(payload, [
                    "yearly_kwh", "annual_kwh", "consumption_kwh_year",
                    "EgiEnergianvandning_Eindex_calc",             # common EPC index
                ])
                return {"ok": True, "data": {"yearly_kwh": data}}

            elif op == "fetch_tariff":
                payload = self.sql.fetch_building_data(kwargs["building_id"])
                data = self._extract_field(payload, [
                    "tariff_name", "tariff", "grid_tariff", "grid_tariff_name",
                    "Nätavgift", "Natavgift", "Tariff", "Abonnemang",
                ])
                return {"ok": True, "data": {"tariff_name": data}}

            # NEW: electricity usage (kWh) -> El_calc
            elif op == "fetch_electricity_usage":
                payload = self.sql.fetch_building_data(kwargs["building_id"])
                data = self._extract_field(payload, ["El_calc"])
                return {"ok": True, "data": {"el_calc": data}}

            # NEW: heating usage (kWh) -> EgiVarme_calc
            elif op == "fetch_heating_usage":
                payload = self.sql.fetch_building_data(kwargs["building_id"])
                data = self._extract_field(payload, ["EgiVarme_calc"])
                return {"ok": True, "data": {"varme_calc": data}}

            # NEW: basement levels -> EgenAntalKallarplan
            elif op == "fetch_basement_count":
                payload = self.sql.fetch_building_data(kwargs["building_id"])
                data = self._extract_field(payload, ["EgenAntalKallarplan"])
                return {"ok": True, "data": {"basement_count": data}}

            # NEW: stairwells -> EgenAntalTrapphus
            elif op == "fetch_stairs_count":
                payload = self.sql.fetch_building_data(kwargs["building_id"])
                data = self._extract_field(payload, ["EgenAntalTrapphus"])
                return {"ok": True, "data": {"stairs_count": data}}

            elif op == "missing_address":
                return {"ok": False, "data": None, "message": "missing_address"}

            elif op == "noop":
                return {"ok": True, "data": None}

            return {"ok": False, "data": None, "message": f"unknown_op:{op}"}

        except Exception as e:
            return {"ok": False, "data": None, "message": f"exception:{e}"}

    # ---------- Internals ----------
    def _classify_topic(self, text: str, entities: list) -> str:
        t = text
        e = " ".join(entities)

        if any(k in t or k in e for k in ["energy class", "energy rating", "epc", "energiklass", "energideklaration"]):
            return "energy_rating"

        # electricity (El_calc)
        if any(k in t or k in e for k in ["electricity", "el", "ström", "strom", "elproduktion", "elkwh", "kwh el"]):
            return "electricity_usage"

        # heating kWh (EgiVarme_calc) vs heating *type*
        if any(k in t or k in e for k in ["heating kwh", "värmeanvändning", "varmeanvandning", "värme kwh", "varme kwh"]):
            return "heating_usage"

        if any(k in t or k in e for k in ["heating type", "heating", "värme", "varme", "fjärrvärme", "fjarrvarme", "heat pump", "pump"]):
            return "heating_type"

        # basement levels (EgenAntalKallarplan)
        if any(k in t or k in e for k in ["basement", "källarplan", "kallarplan", "källare", "kallare"]):
            return "basement_count"

        # stairwells (EgenAntalTrapphus)
        if any(k in t or k in e for k in ["stairs", "staircases", "trapphus", "trappa"]):
            return "stairs_count"

        if any(k in t or k in e for k in ["yearly", "annual", "kwh", "förbrukning", "forbrukning", "consumption", "usage"]):
            return "yearly_usage"

        # primary heating (HuvudsakligUppvarmning_calc)
        if any(k in t or k in e for k in ["primary heating", "huvudsaklig uppvärmning", "huvudsaklig uppvarmning"]):
            return "primary_heat"

        if any(k in t or k in e for k in ["tariff", "nätavgift", "natavgift", "grid"]):
            return "tariff"

        return "profile"

    def _route_with_building_id(self, topic: str, building_id: str) -> Tuple[str, Dict[str, Any]]:
        mapping = {
            "energy_rating": "fetch_energy_rating",
            "heated_area": "fetch_heated_area",
            "heating_type": "fetch_heating_type",
            "yearly_usage": "fetch_yearly_usage",
            "tariff": "fetch_tariff",
            "profile": "fetch_building_profile",

            # new topics mapped to your confirmed columns
            "electricity_usage": "fetch_electricity_usage",
            "heating_usage": "fetch_heating_usage",
            "basement_count": "fetch_basement_count",
            "stairs_count": "fetch_stairs_count",
            "primary_heat": "fetch_heating_type",  # same op returns heating_type/primary
        }
        return (mapping.get(topic, "fetch_building_profile"), {"building_id": building_id})

    @staticmethod
    def _extract_field(payload: Any, candidates: list):
        if not isinstance(payload, dict):
            return None
        # direct match
        for key in candidates:
            if key in payload and payload[key] not in (None, ""):
                return payload[key]
        # case-insensitive match
        lower = {str(k).lower(): v for k, v in payload.items()}
        for key in candidates:
            lk = key.lower()
            if lk in lower and lower[lk] not in (None, ""):
                return lower[lk]
        return None
