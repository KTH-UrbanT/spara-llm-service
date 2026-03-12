from tests.support import fresh_import, stub_module


class FakeSQLClient:
    def __init__(self):
        self.calls = []
        self.uuid_payload = {"50a_uuid": "uuid-1", "energy_class": "A", "nested": {"tariff": "t1"}}
        self.address_rows = [{"50a_uuid": "uuid-1", "epc_idadr": "Main Street 1"}]
        self.single_filter_rows = [{"byggnadsid": "b-1"}]

    def building_by_uuid(self, building_id):
        self.calls.append(("building_by_uuid", building_id))
        return self.uuid_payload

    def building_by_address(self, address):
        self.calls.append(("building_by_address", address))
        return self.address_rows

    def buildings_by_single_filter(self, field, op, value):
        self.calls.append(("buildings_by_single_filter", field, op, value))
        return self.single_filter_rows


def import_generic_sql_layer():
    stub_module("src.agents.base_agent", BaseAgent=object)
    stub_module("src.agents.building_agent", BuildingAgent=object)
    return fresh_import("src.agents.generic_sql_layer").SQL_Mapper_Layer


def test_route_prefers_address_metadata():
    SQL_Mapper_Layer = import_generic_sql_layer()
    layer = SQL_Mapper_Layer(sql_client=FakeSQLClient())

    decision = layer.route(
        {
            "last_message": "Show me this building",
            "metadata": {"address": "Main Street 1", "building_id": "uuid-1"},
        }
    )

    assert decision.op == "building_by_address"
    assert decision.kwargs == {"address": "Main Street 1"}


def test_route_uses_filterable_metadata_before_building_id():
    SQL_Mapper_Layer = import_generic_sql_layer()
    layer = SQL_Mapper_Layer(sql_client=FakeSQLClient())

    decision = layer.route(
        {
            "last_message": "lookup",
            "metadata": {"byggnadsid": "01-80-FILOSOFEN2-3", "building_id": "uuid-1"},
        }
    )

    assert decision.op == "buildings_by_single_filter"
    assert decision.kwargs == {"field": "byggnadsid", "op": "eq", "value": "01-80-FILOSOFEN2-3"}


def test_route_uses_topic_for_uuid_requests():
    SQL_Mapper_Layer = import_generic_sql_layer()
    layer = SQL_Mapper_Layer(sql_client=FakeSQLClient())

    decision = layer.route(
        {
            "last_message": "What is the energy class?",
            "metadata": {"building_id": "uuid-1"},
        }
    )

    assert decision.op == "fetch_energy_class"
    assert decision.kwargs == {"building_id": "uuid-1"}


def test_route_infers_address_from_last_message():
    SQL_Mapper_Layer = import_generic_sql_layer()
    layer = SQL_Mapper_Layer(sql_client=FakeSQLClient())

    decision = layer.route(
        {
            "last_message": 'Can you check "Filosofgatan 12" for me?',
            "metadata": {},
        }
    )

    assert decision.op == "building_by_address"
    assert decision.kwargs == {"address": "Filosofgatan 12"}


def test_route_returns_noop_when_no_identifiers_exist():
    SQL_Mapper_Layer = import_generic_sql_layer()
    layer = SQL_Mapper_Layer(sql_client=FakeSQLClient())

    decision = layer.route({"last_message": "general question", "metadata": {}})

    assert decision.op == "noop"
    assert decision.kwargs == {}


def test_execute_fetch_building_data_success_and_failure():
    SQL_Mapper_Layer = import_generic_sql_layer()
    sql = FakeSQLClient()
    layer = SQL_Mapper_Layer(sql_client=sql)

    ok_result = layer.execute("fetch_building_data", building_id="uuid-1")
    assert ok_result == {"ok": True, "data": sql.uuid_payload, "message": "OK"}

    sql.uuid_payload = None
    missing_result = layer.execute("fetch_building_data", building_id="uuid-2")
    assert missing_result == {"ok": False, "data": None, "message": "No building found for uuid 'uuid-2'."}


def test_execute_building_by_address_returns_matches_or_empty_message():
    SQL_Mapper_Layer = import_generic_sql_layer()
    sql = FakeSQLClient()
    layer = SQL_Mapper_Layer(sql_client=sql)

    ok_result = layer.execute("building_by_address", address="Main Street 1")
    assert ok_result == {"ok": True, "data": sql.address_rows, "message": "OK"}

    sql.address_rows = []
    empty_result = layer.execute("building_by_address", address="Unknown 99")
    assert empty_result == {"ok": False, "data": [], "message": "No buildings matched address 'Unknown 99'."}


def test_execute_single_filter_returns_rows():
    SQL_Mapper_Layer = import_generic_sql_layer()
    sql = FakeSQLClient()
    layer = SQL_Mapper_Layer(sql_client=sql)

    result = layer.execute("buildings_by_single_filter", field="byggnadsid", value="b-1")

    assert result == {"ok": True, "data": [{"byggnadsid": "b-1"}], "message": "OK"}


def test_execute_resolves_requested_fields_from_building_payload():
    SQL_Mapper_Layer = import_generic_sql_layer()
    sql = FakeSQLClient()
    layer = SQL_Mapper_Layer(sql_client=sql)

    energy = layer.execute("fetch_energy_class", building_id="uuid-1")
    tariff = layer.execute("fetch_tariff", building_id="uuid-1")

    assert energy == {"ok": True, "data": {"building_id": "uuid-1", "value": "A"}, "message": "OK"}
    assert tariff == {"ok": True, "data": {"building_id": "uuid-1", "value": "t1"}, "message": "OK"}


def test_execute_can_resolve_building_id_from_address_before_fetching_field():
    SQL_Mapper_Layer = import_generic_sql_layer()
    sql = FakeSQLClient()
    layer = SQL_Mapper_Layer(sql_client=sql)

    result = layer.execute("fetch_energy_class", address="Main Street 1")

    assert result == {"ok": True, "data": {"building_id": "uuid-1", "value": "A"}, "message": "OK"}
    assert sql.calls[0] == ("building_by_address", "Main Street 1")
    assert sql.calls[1] == ("building_by_uuid", "uuid-1")


def test_execute_returns_error_when_address_cannot_resolve_to_building():
    SQL_Mapper_Layer = import_generic_sql_layer()
    sql = FakeSQLClient()
    sql.address_rows = []
    layer = SQL_Mapper_Layer(sql_client=sql)

    result = layer.execute("fetch_energy_class", address="Missing 1")

    assert result == {
        "ok": False,
        "data": None,
        "message": "Could not resolve building_id from address 'Missing 1'.",
    }


def test_execute_handles_unknown_ops_and_exceptions():
    SQL_Mapper_Layer = import_generic_sql_layer()
    sql = FakeSQLClient()
    layer = SQL_Mapper_Layer(sql_client=sql)

    assert layer.execute("unknown") == {"ok": False, "data": None, "message": "Unknown op 'unknown'."}

    def broken_lookup(_building_id):
        raise RuntimeError("boom")

    sql.building_by_uuid = broken_lookup
    result = layer.execute("fetch_building_data", building_id="uuid-1")
    assert result["ok"] is False
    assert "exception: RuntimeError: boom" == result["message"]
