# QUANTDATA Client — Consumer Guide

This guide covers dual-track access to QUANTDATA's catalog of 77 DuckDB views —
a local zero-copy Python client and a token-authed REST API. Both expose the same
`QuantData` Python API; transport is auto-detected at construction time.

---

## What it is

| Track | When to use | Transport | Auth |
|---|---|---|---|
| **Local (zero-copy)** | Same host as the QUANTDATA repo | DuckDB direct read — no serialization | None (filesystem) |
| **Remote (REST)** | Different machine / container / CI | HTTP `GET /data/{view}`, `POST /sql` via bearer token | `Authorization: Bearer <token>` |

The `QuantData` class is identical for both tracks. Auto-detection logic:

1. `catalog=` kwarg or `QUANTDATA_CATALOG` env var → **local**
2. `url=` kwarg or `QUANTDATA_API_URL` env var → **remote**
3. Neither set → auto-probe `catalog/quant.duckdb` under the repo root → local if found, else error

---

## Install

### Same host (local, zero-copy)

```bash
# From the QUANTDATA repo root
pip install -e .
pip install duckdb          # or: pip install -e ".[local]"
```

The `quantdata/` package lives at the repo root and is installed in editable mode.
`duckdb` is the only extra dependency needed for local mode; it is gated behind the
`local` extra to keep remote installs lightweight.

### Remote project (different machine)

```bash
pip install "git+https://github.com/<your-org>/QUANTDATA.git#subdirectory=quantdata"
```

Remote mode requires only `pandas`, `pyarrow`, `requests` — no DuckDB needed.

---

## Environment variables

| Variable | Purpose | Example |
|---|---|---|
| `QUANTDATA_CATALOG` | Absolute path to `quant.duckdb` (local mode) | `/home/kevin/gs-scraper/QUANTDATA/catalog/quant.duckdb` |
| `QUANTDATA_API_URL` | Base URL for the REST server (remote mode) | `http://100.104.1.39:5050` |
| `QUANTDATA_API_TOKEN` | Bearer token for catalog endpoints | `changeme-replace-in-prod` |

If `QUANTDATA_API_TOKEN` is not set server-side, the catalog endpoints are **open**
(suitable for LAN/dev). Set it when exposing via Tailscale or across a trust boundary.

---

## Python client

```python
from quantdata import QuantData

qd = QuantData()   # auto-detect: local catalog/quant.duckdb → DuckDB; else REST
```

### List all views

```python
qd.views()         # returns DataFrame with all 77 views + metadata
```

### Inspect a view's schema

```python
qd.schema("bars_1d")   # DataFrame: column names, dtypes, is_date, is_numeric flags
```

### Filtered read

```python
# Pull TSMC daily bars 2015-01-01 → 2024-12-31
bars = qd.get("bars_1d", symbol="2330", start="2015-01-01", end="2024-12-31")

# Pull a specific time series field
m1b = qd.get("tw_money_supply_monthly", series="m1b_eop")
```

`get()` signature:
```python
qd.get(view, *, select=None, order=None, dir="ASC", limit=None,
       start=None, end=None, **filters) -> pd.DataFrame
```

- `**filters` → exact-match WHERE clauses (e.g. `symbol="2330"`)
- `start` / `end` → auto-detected date column range filter (ISO8601 strings)
- `select` → list of column names to return
- `order` / `dir` → sort column + direction (`"ASC"` / `"DESC"`)
- `limit` → row cap (remote default: 1 000; parquet: 5 000 000)

In remote mode, `get()` always fetches Parquet (`format=parquet`) for near-zero
serialization overhead; the caller receives a normal pandas DataFrame.

### Arbitrary SQL (read-only)

```python
qd.sql("SELECT trading_date, close FROM bars_1d WHERE symbol='2330' ORDER BY trading_date")
```

Only `SELECT` / `WITH` statements are allowed. Writes, DDL, multi-statement, and
forbidden keywords (`DROP`, `CREATE`, `INSERT`, `ATTACH`, `PRAGMA`, …) are rejected
with `ValueError` client-side (local) or HTTP 400 (remote). Row cap ~30 s timeout.

### Realtime (remote URL required)

```python
qd.live.snapshot(["2330", "TAIEX"])   # current best-bid/ask + price + age_sec
qd.live.health()                       # collector health + seconds_since_poll
```

`qd.live` wraps the existing realtime endpoints documented in
[`docs/realtime-api-v1.md`](realtime-api-v1.md). A `url=` must be provided (or `QUANTDATA_API_URL`
set) — realtime is REST-only and has no local equivalent.

---

## gs-zipline-tej worked example

Pull TSMC daily bars into a Zipline backtest on the same host (zero-copy):

```python
from quantdata import QuantData
import pandas as pd

qd = QuantData()   # local on this host (zero-copy — no network, no serialization)

bars = qd.get("bars_1d", symbol="2330", start="2015-01-01")
# bars columns: trading_date, symbol, open, high, low, close, volume, ...

# Reindex for Zipline
bars = (bars
        .rename(columns={"trading_date": "date"})
        .set_index("date")
        .sort_index())
bars.index = pd.to_datetime(bars.index, utc=True)
```

For bundles that ingest directly from QUANTDATA views, pass
`QUANTDATA_CATALOG=/path/to/catalog/quant.duckdb` before starting the ingest script
so the bundle writer can call `QuantData()` without additional configuration.

---

## REST API contract

Base URL: `http://<host>:5050/api/v1`

Bearer token required on catalog endpoints when `QUANTDATA_API_TOKEN` is set
server-side. Realtime endpoints (`/health`, `/snapshot`, `/ticks`, `/bars`) remain
open (governed by Tailnet ACL / firewall, not token).

Interactive docs: `http://<host>:5050/api/v1/docs` (Swagger UI, vendored offline).
Machine-readable spec: `http://<host>:5050/api/v1/openapi.json`.

### Catalog endpoints (token-gated)

| Method / Path | Purpose | Key params |
|---|---|---|
| `GET /views` | List all 77 views + metadata (name, row_count, max_date) | — |
| `GET /views/{view}/schema` | Column schema for one view | — |
| `GET /data/{view}` | Filtered read of a view | `col=val`, `col__gte` / `col__lte` / `col__in`, `start` / `end`, `select`, `order` + `dir`, `limit`, `offset`, `format=json\|csv\|parquet` |
| `POST /sql` | Read-only SELECT / WITH | JSON body `{"sql":"...","format":"json\|parquet"}` |

Response formats:
- `format=json` (default) → `{"view":"...","columns":[...],"rows":[[...]],"next_offset":...}`
- `format=csv` → `text/csv` attachment
- `format=parquet` → `application/octet-stream` (zstd-compressed Parquet — preferred for bulk)

Pagination: `offset` + `limit`. `next_offset` is `null` when no more rows.

### Realtime endpoints (open)

See [`docs/realtime-api-v1.md`](realtime-api-v1.md) for the full realtime contract.

| Method / Path | Purpose |
|---|---|
| `GET /health` | Collector health + `seconds_since_poll` staleness guard |
| `GET /snapshot` | Per-symbol best price, bid/ask, change, `age_sec` |
| `GET /ticks` | Incremental tick stream (ring buffer, `since_seq`) |
| `GET /bars` | Aggregated OHLCV bars |

---

## curl examples

```bash
export TOKEN="$QUANTDATA_API_TOKEN"
HOST="http://100.104.1.39:5050"

# List all views
curl -H "Authorization: Bearer $TOKEN" "$HOST/api/v1/views"

# Schema for bars_1d
curl -H "Authorization: Bearer $TOKEN" "$HOST/api/v1/views/bars_1d/schema"

# Pull M1B (5 rows, JSON)
curl -H "Authorization: Bearer $TOKEN" \
     "$HOST/api/v1/data/tw_money_supply_monthly?series=m1b_eop&format=json&limit=5"

# Bulk pull as Parquet
curl -H "Authorization: Bearer $TOKEN" \
     "$HOST/api/v1/data/bars_1d?symbol=2330&start=2024-01-01&format=parquet" \
     -o bars_2330.parquet

# Ad-hoc SQL
curl -H "Authorization: Bearer $TOKEN" \
     -X POST "$HOST/api/v1/sql" \
     -H "Content-Type: application/json" \
     -d '{"sql":"SELECT count(*) n FROM bars_1d"}'

# Realtime snapshot (no token required)
curl "$HOST/api/v1/snapshot?symbols=2330,TAIEX"
```

---

## Notes

### Bulk pulls — use Parquet

`format=parquet` uses zstd compression; the Python client automatically requests
Parquet in remote mode (`_remote_get` and `_remote_sql`). JSON is fine for small
result sets or shell inspection; avoid it for millions of rows.

### `/sql` safety constraints

- Only `SELECT` and `WITH` are accepted; DDL, writes, and multi-statement (`;`)
  are rejected immediately.
- ~30 s timeout enforced server-side (thread interrupt).
- Row cap: 5 000 000 rows per query.

### Auth

Store the token in the environment (`QUANTDATA_API_TOKEN`); never commit it.

```bash
export QUANTDATA_API_TOKEN="$(cat ~/.quantdata-token)"
```

The server reads the same env var. If unset, catalog endpoints are open — acceptable
on a private LAN or Tailnet; set a token before exposing beyond the local subnet.

### Network exposure

The server binds to `0.0.0.0:5050` by default (WSL2 / Linux). Use Tailscale funnel
for secure WAN access; do not expose port 5050 directly to the public internet
without a token **and** TLS termination (e.g. Caddy reverse proxy).

For LAN sharing from WSL2, see the `/set-serve-eth` skill or
[`docs/progress-tailscale-funnel.md`](progress-tailscale-funnel.md).
