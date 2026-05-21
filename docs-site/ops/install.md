# 安裝與環境

## 系統需求

| 項目 | 推薦 |
|---|---|
| OS | Linux / WSL2（已測 Ubuntu 22.04 on WSL2，kernel 6.6） |
| Python | 3.12（pyproject.toml 要求 ≥ 3.11） |
| DuckDB CLI | 1.5.2（bundled binary） |
| 磁碟 | 至少 30 GB（含 RAW_SOURCES + bronze + silver + duckdb） |
| 記憶體 | 至少 16 GB（polars / DuckDB 跑大 group-by 會吃） |

## 1. Clone repo

```bash
mkdir -p ~/gs-scraper && cd ~/gs-scraper
git clone <repo-url> QUANTDATA
cd QUANTDATA
```

## 2. 建 venv + 裝套件

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[ingest,dev]"
```

`pyproject.toml` 的 extras：

| extra | 內容 |
|---|---|
| `[ingest]` | duckdb / polars / pandas / pyarrow / pandera / httpx / python-dotenv |
| `[dev]` | pytest / ruff / mypy（CI 用） |

驗證：

```bash
.venv/bin/python -c "import duckdb, polars, pandera; print(duckdb.__version__)"
# 1.5.2
.venv/bin/qd-ingest --help
```

## 3. DuckDB CLI（選）

DuckDB Python 套件本身夠用；但要用 Web UI / interactive CLI 需要單獨裝 binary：

```bash
mkdir -p ~/.local/bin
curl -fsSL https://install.duckdb.org | sh
# 把 ~/.local/bin 加到 PATH（fish）
fish -c "fish_add_path ~/.local/bin"
duckdb --version
# v1.5.2 ...
```

## 4. 設定 TEJ 訂閱

從 [TEJ 訂閱頁面](https://api.tej.com.tw/) 拿到 API key，放進 fish universal var（每個 session 都能拿到）：

```fish
set -Ux TEJAPI_KEY '<your-key-here>'
set -Ux TEJAPI_BASE 'https://api.tej.com.tw'
```

驗證：

```bash
env | grep TEJAPI
.venv/bin/python -c "
import os, httpx
r = httpx.get(f'{os.environ[\"TEJAPI_BASE\"]}/api/v1/datatables/TWN/EWPRCD',
              params={'api_key': os.environ['TEJAPI_KEY'], 'date_from': '20260518', 'opts.columns': 'coid,mdate,close'},
              timeout=30)
print(r.status_code, r.json().get('datatable', {}).get('data', [])[:2])
"
```

成功應該印 `200 [['2330', '2026-05-19', ...], ...]`。

## 5. 第一次 ingest

從零開始建一個能查的 catalog：

```bash
# 抓 TEJ 所有 dataset 從歷史開始
.venv/bin/python scripts/fetch_tej.py --table all --backfill-from 2010-01-01

# 把 RAW_SOURCES 內的固定資料 ingest 進 silver
.venv/bin/qd-ingest tej-stock
.venv/bin/qd-ingest tej-inst-stock
.venv/bin/qd-ingest tej-margin

# 建 catalog views
.venv/bin/qd-ingest build-catalog

# 驗證
.venv/bin/python -c "
import duckdb
print(duckdb.connect('catalog/quant.duckdb', read_only=True)
        .execute('SELECT COUNT(*) FROM bars_1d').fetchone())
"
```

完整 backfill 約 2-4 小時（取決於 TEJ rate limit）。

## 6.（選）FinMind snapshot

如果你想把 2000-2009 段 + 興櫃補進來：

```bash
# 1. 把 zip 放進 RAW_SOURCES/FINMIND資料集.zip
# 2. stream-extract sqlite
.venv/bin/python -c "
import zipfile
zp = '/home/kevin/gs-scraper/RAW_SOURCES/FINMIND資料集.zip'
with zipfile.ZipFile(zp) as z:
    z.extract('FINMIND資料集/data/finmind.sqlite', 'bronze/finmind/')
import shutil; shutil.move('bronze/finmind/FINMIND資料集/data/finmind.sqlite',
                            'bronze/finmind/finmind_2026-05-18.sqlite')
import os; os.removedirs('bronze/finmind/FINMIND資料集/data')
"

# 3. SHA256
sha256sum bronze/finmind/finmind_2026-05-18.sqlite \
  > bronze/finmind/finmind_2026-05-18.sqlite.sha256

# 4. 建 view（先看 db/finmind.md 的範例）
```

詳見 [FinMind 整合頁](../db/finmind.md)。

## 7. 排程 daily_refresh

```bash
bash scripts/install_cron.sh          # 預設每工作日 17:30 CST
crontab -l | grep quantdata           # 確認已寫入
```

詳見 [Cron 排程頁](cron.md)。

## 8. 本地預覽 docs-site

```bash
.venv/bin/pip install mkdocs==1.6.1 mkdocs-material==9.7.6
.venv/bin/mkdocs serve -a 127.0.0.1:8080
# 開 http://127.0.0.1:8080
```

存檔即重整。

## 確認你裝對了

跑這串「煙霧測試」：

```bash
# 1. catalog 可開
.venv/bin/python -c "import duckdb; duckdb.connect('catalog/quant.duckdb', read_only=True)"

# 2. silver 有資料
.venv/bin/python -c "
import duckdb
con = duckdb.connect('catalog/quant.duckdb', read_only=True)
print('bars_1d rows:', con.execute('SELECT COUNT(*) FROM bars_1d').fetchone()[0])
print('latest date:', con.execute('SELECT MAX(trading_date) FROM bars_1d').fetchone()[0])
"

# 3. gap dashboard 跑得起來
.venv/bin/python scripts/gap_report.py --format html
ls -la docs/gap_dashboard.html

# 4. docs build strict
.venv/bin/mkdocs build --strict
ls site/index.html
```

四個都過 → 安裝完成。

## 常見裝壞

| 症狀 | 原因 | 解 |
|---|---|---|
| `_duckdb.IOException: ... Conflicting lock` | 其他 process 開 catalog 寫鎖 | `fuser catalog/quant.duckdb` 找 PID kill |
| `httpx.ReadTimeout` 抓 TEJ | API 慢 / network | `fetch_tej.py` 內建 backoff；重跑 |
| `ModuleNotFoundError: duckdb` | 用了系統 python | 必須用 `.venv/bin/python` |
| `mkdocs build` 報 `[strict] Doc file ... contains a link to '...' which is not found` | 自己 doc 內 dangling link | 把 link 改對或刪掉 |

完整見 [常見問題](troubleshooting.md)。
