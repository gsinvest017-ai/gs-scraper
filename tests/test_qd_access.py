import duckdb, pytest
from ui.search import qd_access as qa

@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1,'a'),(2,'b')) v(id,name)")
    return c

def test_safe_sql_select_ok(con):
    cols, rows = qa.safe_sql("SELECT id,name FROM t ORDER BY id", con=con, row_cap=10)
    assert cols == ["id", "name"] and rows == [[1, "a"], [2, "b"]]

@pytest.mark.parametrize("bad", [
    "INSERT INTO t VALUES (3,'c')", "UPDATE t SET id=9", "DELETE FROM t",
    "DROP TABLE t", "ATTACH 'x.db'", "COPY t TO 'x.csv'", "PRAGMA database_list",
    "INSTALL httpfs", "SELECT 1; SELECT 2", "CREATE TABLE z(x int)",
])
def test_safe_sql_rejects_non_select(con, bad):
    with pytest.raises(ValueError):
        qa.safe_sql(bad, con=con, row_cap=10)

def test_safe_sql_row_cap(con):
    with pytest.raises(ValueError, match="row cap"):
        qa.safe_sql("SELECT * FROM range(100)", con=con, row_cap=10)

from types import SimpleNamespace

def test_query_builds_and_runs(monkeypatch, con):
    import ui.search.qd_access as qa2
    import ui.search.query_builder as qb
    fake_meta = SimpleNamespace(columns=[SimpleNamespace(name="id"),
                                         SimpleNamespace(name="name")])
    monkeypatch.setattr(qa2, "_list_views", lambda: ["t"])
    monkeypatch.setattr(qa2, "_view_meta", lambda v: fake_meta)
    # build_sql validates independently against query_builder's own helpers:
    monkeypatch.setattr(qb, "list_views", lambda: ["t"])
    monkeypatch.setattr(qb, "get_view_meta", lambda v: fake_meta)
    cols, rows, nxt = qa2.query("t", filters=[{"column": "id", "op": "eq", "value": 1}],
                                con=con, limit=10, offset=0)
    assert cols == ["id", "name"] and rows == [[1, "a"]] and nxt is None

def test_check_token(monkeypatch):
    import ui.search.qd_access as qa3
    monkeypatch.delenv("QUANTDATA_API_TOKEN", raising=False)
    assert qa3.check_token(None) is True
    monkeypatch.setenv("QUANTDATA_API_TOKEN", "secret")
    assert qa3.check_token("Bearer secret") is True
    assert qa3.check_token("Bearer wrong") is False
    assert qa3.check_token(None) is False
