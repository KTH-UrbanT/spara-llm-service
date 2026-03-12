import types

from tests.support import fresh_import, stub_module


class FakeDataFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient="records"):
        assert orient == "records"
        return self.rows


class FakeCompletions:
    content = "SELECT 1;"
    error = None
    last_kwargs = None

    def create(self, **kwargs):
        type(self).last_kwargs = kwargs
        if type(self).error is not None:
            raise type(self).error
        message = types.SimpleNamespace(content=type(self).content)
        choice = types.SimpleNamespace(message=message)
        return types.SimpleNamespace(choices=[choice])


class FakeChat:
    def __init__(self):
        self.completions = FakeCompletions()


class FakeOpenAIClient:
    def __init__(self, *args, **kwargs):
        self.chat = FakeChat()


class FakeDBModule:
    result = None
    last_sql = None

    @classmethod
    def query_executor(cls, sql):
        cls.last_sql = sql
        return cls.result


def import_specialized_sql_layer():
    stub_module("openai", OpenAI=FakeOpenAIClient, AzureOpenAI=FakeOpenAIClient)
    return fresh_import("src.agents.specialized_sql_layer")


def make_layer(module):
    layer = module.SpecializedSQLLayer.__new__(module.SpecializedSQLLayer)
    layer._conf = module._Conf(
        api_type="openai",
        endpoint=None,
        api_key="key",
        model_or_deployment="gpt-test",
        api_version=None,
        is_azure=False,
    )
    layer._client = FakeOpenAIClient()
    layer._system_prompt = "system prompt"
    layer._schema_str = "dbo.table(id int)"
    return layer


def test_route_extracts_question_and_metadata():
    module = import_specialized_sql_layer()
    layer = make_layer(module)

    op, kwargs = layer.route(
        {
            "last_message": "How much energy is used?",
            "metadata": {"building_id": 42, "address": "Main Street 1"},
        }
    )

    assert op == "answer_query"
    assert kwargs == {"question": "How much energy is used?", "building_id": "42", "address": "Main Street 1"}


def test_execute_returns_dataframe_rows():
    module = import_specialized_sql_layer()
    layer = make_layer(module)
    FakeCompletions.content = "SELECT * FROM demo;"
    FakeCompletions.error = None
    FakeDBModule.result = FakeDataFrame([{"id": 1}, {"id": 2}])
    layer._safe_import_hammarby = lambda: FakeDBModule

    result = layer.execute("answer_query", {"question": "show data", "building_id": "42", "address": "Main"})

    assert result == {"ok": True, "data": [{"id": 1}, {"id": 2}], "sql": "SELECT * FROM demo;"}
    assert FakeDBModule.last_sql == "SELECT * FROM demo;"
    assert FakeCompletions.last_kwargs["model"] == "gpt-test"
    assert FakeCompletions.last_kwargs["messages"][0]["content"].endswith("building_id=42 | address='Main'")


def test_execute_returns_write_message_for_string_db_result():
    module = import_specialized_sql_layer()
    layer = make_layer(module)
    FakeCompletions.content = "UPDATE demo SET value = 1;"
    FakeCompletions.error = None
    FakeDBModule.result = "OK - 1 row updated"
    layer._safe_import_hammarby = lambda: FakeDBModule

    result = layer.execute("answer_query", {"question": "update", "building_id": None, "address": None})

    assert result == {"ok": True, "message": "OK - 1 row updated", "sql": "UPDATE demo SET value = 1;"}


def test_execute_handles_model_and_db_failures():
    module = import_specialized_sql_layer()
    layer = make_layer(module)
    FakeCompletions.content = "SELECT 1;"
    FakeCompletions.error = RuntimeError("model down")

    model_error = layer.execute("answer_query", {"question": "show data"})
    assert model_error == {"ok": False, "message": "Model error: model down"}

    FakeCompletions.error = None
    FakeCompletions.content = "SELECT 2;"
    layer._safe_import_hammarby = lambda: (_ for _ in ()).throw(ImportError("missing db"))
    import_error = layer.execute("answer_query", {"question": "show data"})
    assert import_error == {"ok": False, "message": "Import error: missing db", "sql": "SELECT 2;"}

    layer._safe_import_hammarby = lambda: FakeDBModule
    FakeDBModule.result = None
    db_none = layer.execute("answer_query", {"question": "show data"})
    assert db_none == {"ok": False, "message": "DB returned no result.", "sql": "SELECT 2;"}


def test_extract_sql_handles_fenced_and_plain_content():
    module = import_specialized_sql_layer()

    assert module.SpecializedSQLLayer._extract_sql("```sql\nSELECT * FROM x;\n```") == "sql\nSELECT * FROM x;"
    assert module.SpecializedSQLLayer._extract_sql("```\nSELECT * FROM y;\n```") == "SELECT * FROM y;"
    assert module.SpecializedSQLLayer._extract_sql("SELECT * FROM z; extra words") == "SELECT * FROM z;"
    assert module.SpecializedSQLLayer._extract_sql("  `SELECT * FROM w`  ") == "`SELECT * FROM w`"
