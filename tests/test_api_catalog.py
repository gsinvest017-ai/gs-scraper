import io, duckdb, pytest, pandas as pd
from types import SimpleNamespace

@pytest.fixture
def client(monkeypatch):
    from ui.search import qd_access as qa
    monkeypatch.setattr(qa, "_list_views", lambda: ["t"])
    monkeypatch.setattr(qa, "list_views", lambda: [{"name": "t", "row_count": 2}])
    monkeypatch.setattr(qa, "view_schema", lambda v: {"name": "t", "columns": [{"name": "id"}]})
    def fake_query(view, **k):
        return ["id", "name"], [[1, "a"], [2, "b"]], None
    monkeypatch.setattr(qa, "query", fake_query)
    monkeypatch.setattr(qa, "safe_sql", lambda sql, **k: (["n"], [[1]]))
    monkeypatch.setattr(qa, "_get_connection", lambda: duckdb.connect(":memory:"))
    from ui.search.app import app
    app.config["TESTING"] = True
    return app.test_client()

def test_views(client):
    r = client.get("/api/v1/views")
    assert r.status_code == 200 and r.get_json()[0]["name"] == "t"

def test_data_json(client):
    r = client.get("/api/v1/data/t?format=json")
    b = r.get_json()
    assert b["columns"] == ["id", "name"] and len(b["rows"]) == 2

def test_data_parquet(client):
    r = client.get("/api/v1/data/t?format=parquet")
    assert r.status_code == 200
    df = pd.read_parquet(io.BytesIO(r.data))
    assert list(df.columns) == ["id", "name"] and len(df) == 2

def test_sql_json(client):
    r = client.post("/api/v1/sql", json={"sql": "SELECT 1 n"})
    assert r.status_code == 200 and r.get_json()["columns"] == ["n"]

def test_token_401(client, monkeypatch):
    monkeypatch.setenv("QUANTDATA_API_TOKEN", "secret")
    assert client.get("/api/v1/views").status_code == 401
    assert client.get("/api/v1/views", headers={"Authorization": "Bearer secret"}).status_code == 200

def test_openapi_lists_catalog_paths(client):
    spec = client.get("/api/v1/openapi.json").get_json()
    for p in ("/views", "/data/{view}", "/sql"):
        assert p in spec["paths"], f"{p} missing from OpenAPI"
