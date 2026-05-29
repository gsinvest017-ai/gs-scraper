"""P0 integration — DuckDB + parquet real I/O round-trip.

Covers I-001..002, I-006..007 abridged: writes silver parquet via
qd_ingest.common.io, opens a real DuckDB connection, queries it back.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa

from qd_ingest.common.io import write_silver_partitioned


def test_I001_duckdb_can_read_silver_parquet(tmp_path: Path):
    """write parquet → CREATE VIEW → SELECT count(*) should match input row count."""
    tbl = pa.table({
        "source": ["tej"] * 4,
        "symbol": ["2330", "2330", "2317", "2317"],
        "date":   ["2026-01-02", "2026-01-03", "2026-01-02", "2026-01-03"],
        "close":  [600.0, 610.5, 100.0, 101.5],
        "ingestion_ts": pa.array(["2026-05-29T00:00:00Z"] * 4, type=pa.string()),
    })
    silver_root = tmp_path / "silver" / "bars_1d"
    n = write_silver_partitioned(tbl, silver_root, partition_cols=["source"])
    assert n == 4

    db = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db))
    glob = (silver_root / "**" / "*.parquet").as_posix()
    con.execute(f"CREATE VIEW bars_1d AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true);")
    n_back = con.execute("SELECT count(*) FROM bars_1d").fetchone()[0]
    assert n_back == 4

    # Filter pushdown: per-symbol count
    n_2330 = con.execute("SELECT count(*) FROM bars_1d WHERE symbol = '2330'").fetchone()[0]
    assert n_2330 == 2
    con.close()


def test_I002_mini_catalog_has_expected_views(mini_catalog: Path):
    """Session-scoped mini_catalog fixture exposes calendar_xtai + symbol_map."""
    con = duckdb.connect(str(mini_catalog), read_only=True)
    try:
        tables = sorted(r[0] for r in con.execute("SHOW TABLES").fetchall())
        assert tables == ["calendar_xtai", "symbol_map"]

        cal_rows = con.execute("SELECT count(*) FROM calendar_xtai").fetchone()[0]
        assert cal_rows == 3

        sm_names = sorted(r[0] for r in con.execute("SELECT name FROM symbol_map").fetchall())
        assert sm_names == ["台泥", "台積電", "鴻海"]
    finally:
        con.close()


def test_I007_dedup_upsert_via_delete_matching(tmp_path: Path):
    """Re-write same partition with delete_matching → 1 row (replaces, not appends)."""
    root = tmp_path / "silver"
    first = pa.table({
        "source": ["tej", "tej", "tej"],
        "symbol": ["2330"] * 3,
        "date":   ["2026-01-02", "2026-01-03", "2026-01-04"],
        "close":  [600.0, 610.5, 605.0],
        "ingestion_ts": pa.array(["2026-05-28T00:00:00Z"] * 3, type=pa.string()),
    })
    write_silver_partitioned(first, root, partition_cols=["source"])

    # Second ingest: same key but one row, fresher ingestion_ts
    second = pa.table({
        "source": ["tej"],
        "symbol": ["2330"],
        "date":   ["2026-01-02"],
        "close":  [600.0],
        "ingestion_ts": pa.array(["2026-05-29T00:00:00Z"], type=pa.string()),
    })
    write_silver_partitioned(second, root, partition_cols=["source"])

    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    glob = (root / "**" / "*.parquet").as_posix()
    con.execute(f"CREATE VIEW v AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true);")
    n = con.execute("SELECT count(*) FROM v").fetchone()[0]
    # delete_matching drops the whole partition before writing second batch → only 1 row remains
    assert n == 1
    con.close()
