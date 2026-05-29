"""Shared pytest fixtures for integration + e2e tests.

The strategy:
- `mini_catalog` builds a tiny standalone DuckDB with 2 views backed by
  inline-generated parquet files (no dependency on the real silver/gold tree).
- `app_client` monkeypatches `ui.search.catalog_inspector` module state to
  point at `mini_catalog`, then yields a Flask test_client.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


# ── mini catalog: real DuckDB + real parquet, no project ingest needed ──────

@pytest.fixture(scope="session")
def mini_catalog(tmp_path_factory) -> Path:
    """Build once per session, reused across all integration + e2e tests."""
    root = tmp_path_factory.mktemp("mini_catalog")

    # View 1: calendar_xtai — date column → exercise `is_time_series` detection
    cal = pa.table({
        "date": pa.array(["2026-05-27", "2026-05-28", "2026-05-29"], type=pa.string()),
        "is_trading": [True, True, True],
        "session": ["regular", "regular", "regular"],
    })
    cal_path = root / "calendar_xtai.parquet"
    pq.write_table(cal, cal_path)

    # View 2: symbol_map — non-time-series, distinct values
    sm = pa.table({
        "source": ["tej", "tej", "tej"],
        "symbol": ["2330", "2317", "1101"],
        "name":   ["台積電", "鴻海", "台泥"],
    })
    sm_path = root / "symbol_map.parquet"
    pq.write_table(sm, sm_path)

    db_path = root / "mini.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(f"CREATE OR REPLACE VIEW calendar_xtai AS SELECT * FROM read_parquet('{cal_path}');")
    con.execute(f"CREATE OR REPLACE VIEW symbol_map    AS SELECT * FROM read_parquet('{sm_path}');")
    con.close()
    return db_path


@pytest.fixture
def app_client(mini_catalog, monkeypatch):
    """Flask test_client wired to the mini_catalog.

    Monkeypatches:
    - `_temp_catalog` → mini_catalog (bypasses live CATALOG copy)
    - `_views_cache` / `_meta_cache` → empty (forces re-read from mini)
    - `_ensure_temp_catalog` → no-op (would otherwise overwrite mini on /api/refresh)
    """
    import ui.search.catalog_inspector as ci

    monkeypatch.setattr(ci, "_temp_catalog", mini_catalog)
    monkeypatch.setattr(ci, "_views_cache", [])
    monkeypatch.setattr(ci, "_meta_cache", {})
    monkeypatch.setattr(ci, "_ensure_temp_catalog", lambda: mini_catalog)

    from ui.search.app import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
