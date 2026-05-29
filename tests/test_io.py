"""P0 unit tests for qd_ingest.common.io.write_silver_partitioned.

Covers U-004..006 round-trip + idempotent upsert + zstd compression.
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from qd_ingest.common.io import write_silver_partitioned


def _sample_table() -> pa.Table:
    return pa.table({
        "source": ["tej", "tej", "tej", "tej"],
        "symbol": ["2330", "2330", "2317", "2317"],
        "date":   ["2026-01-02", "2026-01-03", "2026-01-02", "2026-01-03"],
        "close":  [600.0, 610.5, 100.0, 101.5],
        "ingestion_ts": pa.array(
            ["2026-05-29T00:00:00Z"] * 4, type=pa.string()
        ),
    })


def test_U004_round_trip_preserves_values(tmp_path: Path):
    tbl = _sample_table()
    n = write_silver_partitioned(tbl, tmp_path / "silver", partition_cols=["source"])
    assert n == 4

    # Read back the whole dataset and assert content equality (order may vary
    # across partitions, so sort before compare)
    dataset = ds.dataset(tmp_path / "silver", format="parquet", partitioning="hive")
    back = dataset.to_table().sort_by([("symbol", "ascending"), ("date", "ascending")])
    orig = tbl.sort_by([("symbol", "ascending"), ("date", "ascending")])

    # Drop partition column from comparison (hive partition gets re-encoded as
    # dictionary on read) and verify the data columns
    for col in ("symbol", "date", "close"):
        assert back.column(col).to_pylist() == orig.column(col).to_pylist()


def test_U004_hive_partition_directory_layout(tmp_path: Path):
    write_silver_partitioned(_sample_table(), tmp_path / "silver", partition_cols=["source"])
    # Expect tmp/silver/source=tej/<one or more>.parquet
    part_dir = tmp_path / "silver" / "source=tej"
    assert part_dir.is_dir(), f"missing partition dir: {part_dir}"
    parquet_files = list(part_dir.glob("*.parquet"))
    assert parquet_files, "no parquet files written under source=tej/"


def test_U005_delete_matching_replaces_partition(tmp_path: Path):
    """Re-writing same partition with delete_matching should not duplicate rows."""
    root = tmp_path / "silver"
    write_silver_partitioned(_sample_table(), root, partition_cols=["source"])
    # Re-ingest a smaller subset; with delete_matching it should replace.
    smaller = pa.table({
        "source": ["tej"],
        "symbol": ["2330"],
        "date":   ["2026-01-02"],
        "close":  [600.0],
        "ingestion_ts": pa.array(["2026-05-29T01:00:00Z"], type=pa.string()),
    })
    write_silver_partitioned(smaller, root, partition_cols=["source"])

    back = ds.dataset(root, format="parquet", partitioning="hive").to_table()
    # delete_matching drops the whole partition and replaces with new table → 1 row
    assert back.num_rows == 1
    assert back.column("symbol").to_pylist() == ["2330"]


def test_U006_zstd_compression_applied(tmp_path: Path):
    write_silver_partitioned(_sample_table(), tmp_path / "silver", partition_cols=["source"])
    files = list((tmp_path / "silver").rglob("*.parquet"))
    assert files, "no parquet output"
    md = pq.read_metadata(files[0])
    # Every column chunk in every row group should be zstd-compressed
    for rg in range(md.num_row_groups):
        for col_idx in range(md.row_group(rg).num_columns):
            comp = md.row_group(rg).column(col_idx).compression
            assert comp == "ZSTD", f"row_group={rg} col={col_idx} compression={comp}"
