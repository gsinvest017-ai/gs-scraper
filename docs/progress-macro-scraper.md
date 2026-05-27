# 2026-05-27 — yfinance macro scraper（bottleneck #2）

## 觸發

`/safe-yolo 陸續按照推薦排序解決問題`（#1 已完成；本輪做 #2）

## 目標

寫一支 yfinance daily scraper 把 `RAW_SOURCES/SUPPLEMENT/<category>/<stem>_daily.parquet` 的 45 個 macro symbol（VIX / USDTWD / WTI / 美 10Y / SPX / SOX / 各國指數 / 商品 / 信用 ETF）刷到當日，解開 `macro_daily`（silver，19d STALE）→ `macro_factors`（gold）這條鏈。

`cross_market_features` 嚴格說是來自 `SUPPLEMENT/DERIVED/` 的手動 dump（目前 EMPTY，date 欄 NULL），不直接由 macro_daily 衍生；本輪先不碰，留 backlog（需另寫 cross-market builder）。

## 上游結構（已勘查）

`SUPPLEMENT/<CAT>/<STEM>_daily.parquet`：

| Category | stems |
|---|---|
| US_INDEX | DJI / GSPC / IRX / NDX / RUT / SOX / TNX / VIX |
| US_FUTURES | ES_F / NQ_F / RTY_F / YM_F |
| US_SECTOR_ETF | GLD / IWM / QQQ / SPY / TLT / XLE / XLF / XLI / XLK / XLV |
| COMMODITY | CL_F / GC_F / HG_F / NG_F / SI_F |
| FX | CNY_X / DX-Y_NYB / EURUSD_X / JPY_X / USDTWD |
| TW_INDEX | 0050_TW / 0056_TW / TWII |
| ASIA | 000001_SS / HSI / KS11 / N225 / STI |
| CREDIT | HYG / IEF / LQD / SHY / TIP |

**兩種 schema**：
- 一般檔：DatetimeIndex 名 `Date` + `open/high/low/close[/adj_close]/volume`（VIX 無 adj_close）
- USDTWD 特例：`date` 欄（tz-aware）+ `usdtwd_*` 前綴 + 衍生 ret1/ret5/ma20/z20

## stem → yfinance ticker 映射

| 規則 | 範例 |
|---|---|
| 指數加 `^` | DJI→`^DJI`, GSPC→`^GSPC`, VIX→`^VIX`, TWII→`^TWII`, HSI→`^HSI`, KS11→`^KS11`, N225→`^N225`, STI→`^STI`, SOX→`^SOX`, RUT→`^RUT`, NDX→`^NDX`, IRX→`^IRX`, TNX→`^TNX` |
| `_F` → `=F` | ES_F→`ES=F`, NQ_F→`NQ=F`, CL_F→`CL=F`, GC_F→`GC=F` ... |
| `_X` → `=X` | CNY_X→`CNY=X`, EURUSD_X→`EURUSD=X`, JPY_X→`JPY=X` |
| `_TW`/`_SS` → `.TW`/`.SS` | 0050_TW→`0050.TW`, 000001_SS→`000001.SS` |
| 特例 | DX-Y_NYB→`DX-Y.NYB`, USDTWD→`TWD=X` |
| 純 ETF as-is | SPY/QQQ/GLD/XLE/HYG/... |

用**顯式 dict**（45 個）最安全，不靠規則推導。

## Append-since-last 設計

每個檔：
1. `pd.read_parquet(fp)` 還原（一般檔 Date index；USDTWD 把 usdtwd_* rename 回標準 + 只留 OHLCV）
2. 找 max date
3. `yfinance.download(ticker, start=max+1, auto_adjust=False)` 抓增量
4. normalize 成標準 schema（Date index + open/high/low/close/adj_close/volume）
5. concat + dedup(date, keep last) + sort + `to_parquet`（保留 Date index）

USDTWD 統一改寫成標準 schema —— macro.py 的 `_normalize_one` 對「sym==USDTWD 但無 usdtwd_close 欄」會走一般路徑，相容。

`auto_adjust=False` 才會同時給 Close + Adj Close，對齊歷史 schema。

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 + 映射設計 | ⏳ |
| **M2** | `scripts/fetch_macro.py` | ⏳ |
| **M3** | pip install yfinance + 跑 fetch + re-ingest macro + rebuild macro_factors + 驗 dashboard | ⏳ |
| **M4** | 接進 daily_refresh.sh + docs + commit + push | ⏳ |

## Fallback

- **網路擋 yfinance**：sandbox 可能無法外連 Yahoo。若 fetch 失敗 → 仍 commit scraper 程式（有價值），在進度檔記網路限制，標 stuck
- yfinance schema 變動（auto_adjust 預設）：明確設 `auto_adjust=False`
- 寫壞 SUPPLEMENT parquet：fetcher 寫前先 `.bak` 備份

## 完成日誌

（M2-M4 後追加）
