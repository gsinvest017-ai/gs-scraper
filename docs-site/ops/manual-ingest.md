# 手動 ingest

`daily_refresh.sh` 處理日常增量。手動 ingest 用在：

- 補某段歷史段（例如新加入的 dataset 要 backfill 5 年）
- 加入新的 dataset 第一次跑
- 重新 ingest 已 stale 的整段
- bronze 有新 snapshot（FinMind、新 RAW_SOURCES zip）

## 工具樹

```
.venv/bin/qd-ingest <subcommand>     # 主要 CLI，封裝下面所有
scripts/fetch_tej.py                  # TEJ API client（被 qd-ingest 呼叫）
scripts/fetch_<source>.py             # 各 source 的 fetcher（往後擴充）
```

`qd-ingest` 用法：

```bash
.venv/bin/qd-ingest --help
```

## TEJ：補某段歷史

```bash
# 補 2010-01-01 到今天的個股日 K（從零開始）
.venv/bin/python scripts/fetch_tej.py --table stock_daily --backfill-from 2010-01-01

# 補某月（既有資料外的 hole）
.venv/bin/python scripts/fetch_tej.py --table stock_daily \
    --date-from 2015-06-01 --date-to 2015-06-30

# 只一檔（debug 用）
.venv/bin/python scripts/fetch_tej.py --table stock_daily --symbol 2330 \
    --date-from 2020-01-01

# 列所有 dataset
.venv/bin/python scripts/fetch_tej.py --list-tables
```

`fetch_tej.py` flag 速查：

| flag | 用途 |
|---|---|
| `--table <name>` | 哪張 dataset。`all` = 全部 |
| `--append-since-silver` | 從 silver 既有最大日期之後抓（增量） |
| `--backfill-from <date>` | 全量回填到此日期 |
| `--date-from / --date-to` | 指定區間 |
| `--symbol <id>` | 限制某檔 |
| `--dry-run` | 印計畫不執行 |

抓完會：

1. 寫 `bronze/tej/<table>/<YYYY-MM-DD>/*.csv` + `.sha256`
2. **同步寫** `silver/<class>/<view>/year=YYYY/*.parquet`（schema 標準化在 fetch 端做）
3. 在 `meta/audit/ingest_<YYYY-MM-DD>.jsonl` 加一行 manifest

## qd-ingest subcommands

```bash
.venv/bin/qd-ingest tej-stock            # silver/bars/asset_class=tw_stock
.venv/bin/qd-ingest tej-inst-stock       # silver/flows/inst_stock_daily
.venv/bin/qd-ingest tej-margin           # silver/flows/margin
.venv/bin/qd-ingest tej-revenue          # silver/fundamentals/revenue_monthly
.venv/bin/qd-ingest tej-fundamentals     # silver/fundamentals/q
.venv/bin/qd-ingest tej-accounting       # silver/fundamentals/accounting_raw
.venv/bin/qd-ingest tej-chip             # silver/flows/chip_dist
.venv/bin/qd-ingest tej-attrs            # silver/events/trading_attrs
.venv/bin/qd-ingest tej-dividend         # silver/events/cash_dividend
.venv/bin/qd-ingest tej-stock-futures    # silver/bars/asset_class=tw_stock_future
.venv/bin/qd-ingest tej-futures-large-trader  # silver/flows/futures_large_trader
.venv/bin/qd-ingest tej-inst-futures-full     # silver/flows/inst_futures_full

.venv/bin/qd-ingest build-catalog        # 重建 catalog/quant.duckdb 全部 view
.venv/bin/qd-ingest rebuild-stock-factors # gold/features/stock_factor_daily
```

每個 subcommand 都 idempotent，可以重跑。

## 從 RAW_SOURCES bulk import

非 TEJ 的一次性大檔：

### histdata 美股 1min

```bash
# 解壓
cd RAW_SOURCES && unzip NQ_1min_2010-2024.zip -d ../bronze/histdata/

# 跑 ingest
cd ..
.venv/bin/python scripts/ingest_histdata.py --symbol NQ --years 2010-2024
# (寫到 silver/bars/asset_class=us_future/symbol=NQ/year=YYYY/*.parquet)
```

### FinMind sqlite snapshot

詳見 [FinMind 整合](../db/finmind.md)。

```bash
# 1. 解壓 sqlite
.venv/bin/python -c "
import zipfile
zp = 'RAW_SOURCES/FINMIND資料集.zip'
with zipfile.ZipFile(zp) as z:
    z.extract('FINMIND資料集/data/finmind.sqlite', 'bronze/finmind/')
"
mv bronze/finmind/FINMIND資料集/data/finmind.sqlite bronze/finmind/finmind_2026-05-18.sqlite
rmdir -p bronze/finmind/FINMIND資料集/data

# 2. SHA256
sha256sum bronze/finmind/finmind_2026-05-18.sqlite \
  > bronze/finmind/finmind_2026-05-18.sqlite.sha256

# 3. 建 view（一次性）
.venv/bin/python -c "
import duckdb, os
abs_db = os.path.abspath('bronze/finmind/finmind_2026-05-18.sqlite')
con = duckdb.connect('catalog/quant.duckdb')
con.execute('INSTALL sqlite; LOAD sqlite;')
for src, name in [
    ('taiwan_stock_price',     'finmind_stock_price'),
    ('taiwan_stock_price_adj', 'finmind_stock_price_adj'),
    # ... 其餘見 db/finmind.md
]:
    con.execute(f\"DROP VIEW IF EXISTS {name}; CREATE VIEW {name} AS SELECT * FROM sqlite_scan('{abs_db}', '{src}')\")
"
```

## 加新 TEJ dataset

加一張 TEJ 沒接過的 dataset（例如新發布的法人別 X）：

1. 在 `scripts/fetch_tej.py` 加 `--table x` 的 fetch + schema 標準化邏輯
2. 在 `src/qd_ingest/tej_<x>.py` 寫 silver writer
3. 在 `src/qd_ingest/catalog/views.sql`（或 macro）加 view DDL
4. 在 `scripts/gap_report.py` 的 `DATASETS` registry 加一行
5. 在 `docs-site/db/views.md` 補一段

跑：

```bash
.venv/bin/python scripts/fetch_tej.py --table x --backfill-from 2020-01-01
.venv/bin/qd-ingest tej-x
.venv/bin/qd-ingest build-catalog
.venv/bin/python scripts/gap_report.py --format all
```

## Backfill 全量

從零鎖建整個 lakehouse：

```bash
# 1. 一個個 dataset 抓（避免 TEJ rate-limit）
for t in stock_daily inst_stock margin revenue_monthly fundamentals_q accounting_raw \
         chip_dist trading_attrs cash_dividend stock_futures \
         futures_large_trader inst_futures_full; do
    .venv/bin/python scripts/fetch_tej.py --table "$t" --backfill-from 2010-01-01
done

# 2. silver ingest 全部
for cmd in tej-stock tej-inst-stock tej-margin tej-revenue tej-fundamentals \
           tej-accounting tej-chip tej-attrs tej-dividend tej-stock-futures \
           tej-futures-large-trader tej-inst-futures-full; do
    .venv/bin/qd-ingest "$cmd"
done

# 3. catalog
.venv/bin/qd-ingest build-catalog

# 4. derived
.venv/bin/qd-ingest rebuild-stock-factors

# 5. dashboard
.venv/bin/python scripts/gap_report.py --format all
```

預估時間：2-4 小時（取決於 TEJ rate-limit 與 CPU）。

## 防呆

- **永遠** 跑前先 `cp catalog/quant.duckdb catalog/quant.duckdb.bak_$(date +%s)`
- 大 backfill 用 `tmux` / `nohup` 避免 ssh 斷線丟工作
- `--dry-run` 是好朋友，特別是 backfill 整年的時候
- 看到 `_duckdb.IOException: ... lock` 別硬殺，先 `lsof` 確認
