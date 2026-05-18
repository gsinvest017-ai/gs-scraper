# Safe-YOLO: TEJ refresh 前置作業永久化

> 啟動：2026-05-18
> 觸發指令：`/safe-yolo 幫我在系統永久設定前置作業 tej api key 是 ...`
> 操作者：claude-opus-4-7

## 目標

讓 `scripts/fetch_tej.py` 與後續 ingest 流程在這台機器上**重開機後依然能跑**，
不用每次手動 `export TEJAPI_KEY=...`，也不用每次重裝 tejapi。

## 起始狀態

- Shell：`/usr/bin/fish`
- `~/.config/fish/config.fish` 已有 PATH 設定，**沒有任何 TEJAPI 變數**
- `~/.config/fish/fish_variables` 存在，**沒有任何 TEJAPI 變數**
- venv `/home/kevin/gs-scraper/QUANTDATA/.venv` 已啟用，但 `tejapi` 套件未安裝
- `pyproject.toml` 的 `ingest` optional-deps 沒列 `tejapi`

## Milestone 計畫

| M | 目標 | 預期產出 |
|---|---|---|
| M1 | 把 `tejapi` 永久裝進 venv + 寫進 `pyproject.toml [project.optional-dependencies] ingest` | `.venv` 內可 `import tejapi`；pyproject.toml 更新；commit |
| M2 | 把 TEJAPI_KEY / TEJAPI_BASE 設成 fish universal var（重開機後仍在） | `set -U` 寫入 `~/.config/fish/fish_variables`；`config.fish` 加可見性註解；commit（只 commit `config.fish` 註解，**key 本身不會被 commit**） |
| M3 | 用真的 tejapi call 驗證 key 有效 | 對 TEJ API 發出一個小 query，確認 200 OK |

## 安全聲明

- TEJAPI_KEY 只會寫到 `~/.config/fish/fish_variables`（家目錄外、不在 repo 內）
- 不會出現在任何 commit 過的檔案、log 檔、progress doc 內
- 不會 echo 到 Bash log 之外的地方

## 進度日誌

### M3 — Key 有效性驗證 + 訂閱範圍盤點

**結論：Key 認證有效，但訂閱不含 `fetch_tej.py` 預設打的 EW 版資料表。**

驗證細節（key 本體已去除）：

- `tejapi.ApiConfig.info()` 成功回傳 user info，確認 key 通過認證。
- 訂閱方案名稱：「TQ高手過招-期貨+TQ初入江湖-個股」
- 訂閱期間：2026-05-06 → 2027-05-06
- 配額：rowsDayLimit=40,000,000、reqDayLimit=2,000、multiConn=True
- 用 `tejapi.get("TWN/EWPRCD", ...)` 打 2330 兩週日 K，回 `ForbiddenError (PDB003) 您沒有存取資料表的權限`。
- 訂閱實際包含的 25 張資料表（**API 版而非 EW 版**）：

| 類別 | 可用表 |
|---|---|
| 個股 OHLCV / 屬性 | `TWN/APIPRCD`（股價）、`TWN/APISTOCK`、`TWN/APISTK1`、`TWN/APISTKATTR` |
| 個股籌碼 | `TWN/AINVFINB`、`TWN/AFINST`、`TWN/APIMT1`（融資融券） |
| 個股財務 / 配發 | `TWN/APISALE`/`APISALE1`（營收）、`TWN/ADIV`、`TWN/APIDV1`、`TWN/APISHRACT`/`APISHRACTW`、`TWN/AFESTM1` |
| 期貨 | `TWN/AFUTR`（期貨日 K）、`TWN/AFUTRSTK`（股期）、`TWN/AFUTRSTD`、`TWN/AFUTRHU`、`TWN/ASTK1` |
| 其他 | `TWN/ARATE`、`TWN/AGBD8A`、`TWN/EWISAMPLE`、`TWN/TRADEDAY_TWSE`、`GLOBAL/WIBOR1` |

**重要**：QUANTDATA silver 的歷史檔是 EW 版 schema（中文欄位、千股單位、固定欄序），而這個訂閱出的是 API 版 schema（不同欄名 / 欄序）。

**下一步建議（不在這個 /safe-yolo 範圍內）**：

1. 重寫 `scripts/fetch_tej.py` 的 dataset 對應到 API 版（`TWN/APIPRCD`、`TWN/APIMT1`、`TWN/AINVFINB`、`TWN/AFUTR`），並寫 schema adapter 把欄名 / 單位轉成 ingester 預期的中文 header → 才能 drop-in 接到既有 silver。
2. 或者升級 TEJ 訂閱到「TQ高手過招-個股」以拿到 EW 版資料表。
3. **無論走 1 還是 2**，env var 設定 + tejapi 套件這兩件 prereq 已完成；之後不用再設。

### M2 — TEJAPI_KEY / TEJAPI_BASE 設成 fish universal var

- 執行 `fish -c 'set -Ux TEJAPI_KEY ...; set -Ux TEJAPI_BASE https://api.tej.com.tw'`，寫入 `~/.config/fish/fish_variables`（家目錄外、非 repo）。
- 開新 fish process 驗證：`$TEJAPI_KEY` 長度 30、`$TEJAPI_BASE` 為 `https://api.tej.com.tw`，皆持續存在。
- `~/.config/fish/config.fish` 補一段註解（無 key），指明 TEJAPI 從哪載入、怎麼改：
  ```fish
  # TEJ API credentials live in fish universal vars (~/.config/fish/fish_variables).
  # Set / inspect / clear with:
  #   set -Ux TEJAPI_KEY <key>
  #   set | grep TEJAPI
  #   set -e TEJAPI_KEY
  ```
- 這個 milestone 沒有 repo file 變更（fish config 不在 repo 內），純粹 commit progress doc 紀錄。

### M1 — tejapi 裝進 venv + pyproject.toml

- `.venv/bin/pip install tejapi` 成功，版本 `0.1.31`，連帶帶入 `requests / fastparquet / fsspec / certifi / urllib3 / charset_normalizer / inflection / cramjam / idna / more-itertools`。
- `import tejapi` 在 venv 內 OK。
- `pyproject.toml` 的 `[project.optional-dependencies] ingest` 補上 `tejapi>=0.1.31`，未來換機器 `pip install -e ".[ingest]"` 就會自動帶。

## Fallback 指引

```bash
# 取消 fish universal var
fish -c 'set -e TEJAPI_KEY; set -e TEJAPI_BASE'

# 移除 venv 內 tejapi
cd /home/kevin/gs-scraper/QUANTDATA
.venv/bin/pip uninstall -y tejapi

# 回 commit 之前的狀態
git log --oneline -10
git reset --hard <hash>
```
