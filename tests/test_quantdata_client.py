import io
import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pathlib import Path
from quantdata import QuantData


@pytest.fixture
def local_db(tmp_path):
    p = tmp_path / "q.duckdb"
    c = duckdb.connect(str(p))
    c.execute("CREATE VIEW bars AS SELECT * FROM (VALUES "
              "(DATE '2024-01-01','2330',100.0),(DATE '2024-01-02','2330',101.0)) v(d,symbol,close)")
    c.close()
    return p


def test_local_get(local_db):
    qd = QuantData(catalog=local_db)
    df = qd.get("bars", symbol="2330")
    assert isinstance(df, pd.DataFrame) and len(df) == 2 and "close" in df.columns


def test_local_sql(local_db):
    qd = QuantData(catalog=local_db)
    df = qd.sql("SELECT count(*) n FROM bars")
    assert df.iloc[0]["n"] == 2


def test_local_sql_rejects_write(local_db):
    qd = QuantData(catalog=local_db)
    with pytest.raises(ValueError):
        qd.sql("DROP VIEW bars")


# --- Task 9: remote transport ---

class _Resp:
    def __init__(self, status, content=b"", js=None):
        self.status_code = status; self.content = content; self._js = js
    def json(self): return self._js
    @property
    def text(self): return str(self._js)


def test_remote_get(monkeypatch):
    df0 = pd.DataFrame({"d": ["2024-01-01"], "close": [100.0]})
    buf = io.BytesIO(); pq.write_table(pa.Table.from_pandas(df0, preserve_index=False), buf)
    def fake_get(url, headers=None, params=None, timeout=None):
        assert "/data/bars" in url and params["format"] == "parquet"
        return _Resp(200, content=buf.getvalue())
    import quantdata.client as cl
    monkeypatch.setattr(cl.requests, "get", fake_get)
    qd = cl.QuantData(url="http://x:5050", token="t")
    df = qd.get("bars", symbol="2330")
    assert list(df.columns) == ["d", "close"] and len(df) == 1


def test_remote_401(monkeypatch):
    import quantdata.client as cl
    monkeypatch.setattr(cl.requests, "get", lambda *a, **k: _Resp(401, js={"error": "nope"}))
    qd = cl.QuantData(url="http://x:5050", token="bad")
    with pytest.raises(cl.AuthError):
        qd.views()


def test_version():
    import quantdata
    assert quantdata.__version__ == "0.1.0"


def test_local_sql_rejects_table_function(local_db):
    qd = QuantData(catalog=local_db)
    with pytest.raises(ValueError):
        qd.sql("SELECT * FROM read_text('/etc/hostname')")


def test_local_get_rejects_unknown_column(local_db):
    qd = QuantData(catalog=local_db)
    with pytest.raises(ValueError):
        qd.get("bars", **{'symbol" = symbol OR "close': 'x'})   # injection key
    with pytest.raises(ValueError):
        qd.get("bars", nonexistent_col="x")
    with pytest.raises(ValueError):
        qd.get("nonexistent_view", symbol="2330")


def test_local_schema_shape(local_db):
    qd = QuantData(catalog=local_db)
    sch = qd.schema("bars")
    assert "name" in sch.columns and "dtype" in sch.columns
    assert "d" in set(sch["name"]) and "close" in set(sch["name"])


def test_local_views_has_name(local_db):
    qd = QuantData(catalog=local_db)
    assert "name" in qd.views().columns and "bars" in set(qd.views()["name"])
