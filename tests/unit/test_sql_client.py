import pytest
import requests

from src.database.sql_client import SQLClient


class FakeResponse:
    def __init__(self, payload, ok=True, status_code=None):
        self.payload = payload
        self.ok = ok
        self.status_code = status_code if status_code is not None else (200 if ok else 404)
        self.raise_called = False

    def json(self):
        return self.payload

    def raise_for_status(self):
        self.raise_called = True
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_building_by_uuid_returns_object_for_successful_lookup():
    session = FakeSession([FakeResponse({"50a_uuid": "uuid-1"})])
    client = SQLClient(base_url="https://example.com/api", session=session)

    result = client.building_by_uuid("uuid-1")

    assert result == {"50a_uuid": "uuid-1"}
    assert session.calls[0]["url"] == "https://example.com/api/buildings/uuid-1/"


def test_building_by_uuid_returns_none_only_when_response_is_not_found():
    session = FakeSession([FakeResponse({"detail": "not found"}, ok=False)])
    client = SQLClient(session=session)

    assert client.building_by_uuid("missing") is None


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_building_by_uuid_preserves_access_and_server_errors(status):
    response = FakeResponse({"detail": "request failed"}, ok=False, status_code=status)
    session = FakeSession([response])
    client = SQLClient(session=session)

    with pytest.raises(requests.HTTPError) as raised:
        client.building_by_uuid("uuid-1")

    assert raised.value.response.status_code == status
    assert response.raise_called is True
    assert len(session.calls) == 1


def test_building_by_uuid_preserves_exhausted_connection_timeout():
    session = FakeSession([requests.ConnectTimeout("connect timeout")])
    client = SQLClient(session=session)
    client.retries = 0

    with pytest.raises(requests.ConnectTimeout):
        client.building_by_uuid("uuid-1")

    assert len(session.calls) == 1


def test_unknown_building_id_fallback_404_is_a_mapper_not_found_result():
    from tests.unit.test_generic_sql_layer import import_generic_sql_layer

    session = FakeSession([
        FakeResponse([]),
        FakeResponse({"detail": "not found"}, ok=False, status_code=404),
    ])
    client = SQLClient(base_url="https://example.test/api", session=session)
    result = import_generic_sql_layer()(sql_client=client).execute(
        "buildings_by_building_id", building_id="00-00-PILOTFINNSEJ999-0"
    )

    assert result["ok"] is False
    assert result["data"] == []
    assert result["trace"]["execution_status"] == "not_found"
    assert result["message"] == "No buildings matched byggnadsid '00-00-PILOTFINNSEJ999-0'."
    assert len(session.calls) == 2
    assert session.calls[1]["params"]["byggnadsid"] == "00-00-PILOTFINNSEJ999-0"


@pytest.mark.parametrize("operation", ["fetch_building_data", "fetch_energy_class", "fetch_heated_area", "fetch_tariff"])
@pytest.mark.parametrize("status,expected", [(404, "not_found"), (401, "error"), (403, "error"), (500, "error")])
def test_real_client_mapper_preserves_http_failure_category_for_profile_and_fields(operation, status, expected):
    from tests.unit.test_generic_sql_layer import import_generic_sql_layer

    session = FakeSession([FakeResponse({"detail": "request failed"}, ok=False, status_code=status)])
    result = import_generic_sql_layer()(sql_client=SQLClient(session=session)).execute(
        operation, building_id="missing-id"
    )

    assert result["ok"] is False
    assert result["data"] is None
    assert result["trace"]["execution_status"] == expected
    assert result["trace"]["returned_values_used"] == {}
    assert len(session.calls) == 1


def test_building_by_address_calls_dedicated_endpoint_case_insensitively():
    session = FakeSession([FakeResponse([{"epc_idadr": "Main Street 1"}])])
    client = SQLClient(session=session)

    result = client.building_by_address("Main Street 1")

    assert result == [{"epc_idadr": "Main Street 1"}]
    assert session.calls[0]["url"] == "https://oden.abe.kth.se/api/v1/buildings/address"
    assert session.calls[0]["params"] == {
        "address": "Main Street 1",
        "case_sensitive": "false",
    }


def test_building_by_address_does_not_truncate_default_results():
    rows = [{"byggnadsid": f"b{i}", "epc_idadr": "Ringvägen 10"} for i in range(12)]
    session = FakeSession([FakeResponse(rows)])
    client = SQLClient(session=session)

    result = client.building_by_address("Ringvägen 10")

    assert result == rows


def test_building_by_address_applies_local_ordering_and_pagination():
    session = FakeSession(
        [
            FakeResponse(
                [
                    {"byggnadsid": "b3", "epc_egenantalplan": 3},
                    {"byggnadsid": "b1", "epc_egenantalplan": 1},
                    {"byggnadsid": "b2", "epc_egenantalplan": 2},
                ]
            )
        ]
    )
    client = SQLClient(session=session)

    result = client.building_by_address(
        "Main Street 1",
        limit=1,
        offset=1,
        ordering="epc_egenantalplan",
    )

    assert result == [{"byggnadsid": "b2", "epc_egenantalplan": 2}]


def test_building_by_address_retries_transient_request_errors():
    session = FakeSession(
        [
            requests.Timeout("first request timed out"),
            FakeResponse([{"epc_idadr": "Main Street 1"}]),
        ]
    )
    client = SQLClient(session=session)

    result = client.building_by_address("Main Street 1")

    assert result == [{"epc_idadr": "Main Street 1"}]
    assert len(session.calls) == 2


def test_buildings_by_single_filter_normalizes_paginated_payload():
    session = FakeSession([FakeResponse({"results": [{"byggnadsid": "b1"}]})])
    client = SQLClient(session=session)

    result = client.buildings_by_single_filter("byggnadsid", "eq", "b1", limit=5, offset=2, ordering="-byggnadsid")

    assert result == [{"byggnadsid": "b1"}]
    assert session.calls[0]["params"] == {
        "filter_name": "byggnadsid",
        "filter_value": "b1",
        "op": "eq",
        "limit": 5,
        "offset": 2,
        "ordering": "-byggnadsid",
    }


def test_buildings_by_double_filter_builds_lookup_params():
    session = FakeSession([FakeResponse([{"byggnadsid": "b2"}])])
    client = SQLClient(session=session)

    result = client.buildings_by_double_filter(
        ("epc_egenbyggnadstyp", "icontains", "residential"),
        ("50a_uuid", "in", ["a", "b"]),
    )

    assert result == [{"byggnadsid": "b2"}]
    assert session.calls[0]["params"] == {
        "epc_egenbyggnadstyp__icontains": "residential",
        "50a_uuid__in": "a,b",
        "limit": 50,
        "offset": 0,
    }


def test_invalid_fields_and_ops_raise_value_error():
    client = SQLClient(session=FakeSession([]))

    with pytest.raises(ValueError):
        client.buildings_by_single_filter("unknown", "eq", "x")

    with pytest.raises(ValueError):
        client.buildings_by_single_filter("byggnadsid", "bad-op", "x")

    with pytest.raises(ValueError):
        client.buildings_by_double_filter(("byggnadsid", "bad-op", "x"), ("50a_uuid", "eq", "y"))
