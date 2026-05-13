"""IO helpers for silver/gold parquet partitions."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def write_silver_partitioned(
    table: pa.Table,
    dest_root: Path,
    partition_cols: list[str],
    *,
    compression: str = "zstd",
    compression_level: int = 3,
    existing_data_behavior: str = "delete_matching",
) -> int:
    """Write `table` as hive-partitioned parquet under `dest_root`.

    `existing_data_behavior`:
    - 'delete_matching': drop and rewrite any partitions present in this table (idempotent upsert)
    - 'overwrite_or_ignore': keep other partitions, overwrite touched
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    pq.write_to_dataset(
        table,
        root_path=str(dest_root),
        partition_cols=partition_cols,
        compression=compression,
        compression_level=compression_level,
        existing_data_behavior=existing_data_behavior,
        use_threads=True,
    )
    return table.num_rows
