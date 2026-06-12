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
