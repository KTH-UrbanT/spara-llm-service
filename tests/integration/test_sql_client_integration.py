import pytest

from src.database.sql_client import SQLClient


pytestmark = pytest.mark.integration


def test_oden_building_lookup_byggnadsid_smoke():
    client = SQLClient()

    rows = client.buildings_by_single_filter(
        field="byggnadsid",
        op="eq",
        value="01-80-FILOSOFEN2-3",
        limit=5,
    )

    assert isinstance(rows, list)
    assert rows
    assert rows[0].get("byggnadsid") == "01-80-FILOSOFEN2-3"
