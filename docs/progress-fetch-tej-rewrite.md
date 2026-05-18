# Safe-YOLO: 改寫 fetch_tej.py 對應 API 版 TEJ 訂閱

> 啟動：2026-05-18
> 觸發：`/safe-yolo 選A`（接續 `progress-tej-prereq-setup.md` 的選項 A）
> 操作者：claude-opus-4-7

## 目標

把 `scripts/fetch_tej.py` 改成打目前訂閱有的 **`TWN/APIxxx` / `TWN/Axxx`** 資料表（不是原本的 `TWN/EWxxx`），並透過 schema adapter 把回傳結果轉成 `qd_ingest.sources.tej.*` ingester 期望的中文 header CSV 格式 — 這樣 silver 推進可以全自動，不用升級訂閱。

## 起始狀態

- `TEJAPI_KEY` / `TEJAPI_BASE` 已在 fish universal vars（前一個 /safe-yolo M2）。
- venv 已裝 `tejapi 0.1.31`。
- `scripts/fetch_tej.py` 目前的 5 個 logical table（stock_daily / inst_stock / margin / fundamentals_q / fundamentals_ytd）全部打不到（PDB003 forbidden）。
- 訂閱方案：「TQ高手過招-期貨+TQ初入江湖-個股」，2026-05-06 → 2027-05-06。
- silver 最新：個股 2025-12-31、期貨 2026-05-08。

## 可用 / 需要對應的表

| logical | EW 表（沒權限） | API 表（有權限） | dataStartYear |
|---|---|---|---|
| stock_daily | `TWN/EWPRCD` | `TWN/APIPRCD` | 2022 |
| inst_stock | `TWN/EWTINST1` | `TWN/AINVFINB` 或 `TWN/AFINST` | 2022 / 2005 |
| margin | `TWN/EWGIN` | `TWN/APIMT1` | 2005 |
| fundamentals_q | `TWN/EWIFINQ` | **沒有對應** — 訂閱不含財報 | — |
| fundamentals_ytd | `TWN/EWIFINQ` | **沒有對應** | — |

期貨 / 股期的 silver 是靠外部 vendor 給檔，不是 TEJ API 直拉，這次先不擴張。

## Milestone 計畫

| M | 目標 | 預期產出 |
|---|---|---|
| M1 | 用 `tejapi.table_info()` + 小量試打，盤點 `APIPRCD` / `AINVFINB` / `AFINST` / `APIMT1` 的實際欄位 + 單位 + 日期格式，建立 field-to-EW mapping | 進度檔表格化記錄；commit |
| M2 | 重寫 `scripts/fetch_tej.py`：dataset map 改 API 版 + 加 schema adapter（API col → EW 中文 header），fundamentals 退化成 stub 並印警告 | 新版 `fetch_tej.py`；commit |
| M3 | 對 2026-01-01 .. today 試打 stock_daily + inst_stock + margin，merge 到 RAW CSV，跑 ingest 推進 silver | silver max date 從 2025-12-31 前進；commit |
| M4 | Rebuild catalog + smoke test 確認新資料可被 catalog 查到 | catalog 更新；smoke PASS；最終 commit |

## 進度日誌

### M1 — API 表 schema 盤點

實際打 `tejapi.table_info()` + 小量試打 2330 2026-01-01..10 後的真實對應：

| logical | API 表 | 中文名 | 對應 EW 表 | 備註 |
|---|---|---|---|---|
| stock_daily | `TWN/APIPRCD` | 交易資料-股價資料 | `TWN/EWPRCD` | OHLCV 全有；EW 的「開盤價-除權息」等需用 `adjfac` 自算 |
| **inst_stock + margin (合併)** | `TWN/APISHRACT` | 交易資料-籌碼資料(日) | `TWN/EWTINST1` + `TWN/EWGIN` | **同時涵蓋三大法人 + 融資融券 + 借券**（62 欄） |
| fundamentals | `TWN/AINVFINB` | 財務資料_會計師簽證財務資料 | `TWN/EWIFINQ` | 118 個會計科目「原始數字」，EW 是「預算好的比率指標」— **schema 差距太大**，本次 /safe-yolo **不做轉換**，留作後續 |

**重大簡化**：原本擔心融資融券無對應，結果 `APISHRACT` 一張就包：

- 三大法人：`外資買進/賣出/買賣超張數`、`投信買進/賣出/買賣超張數`、`自營商買進/賣出/買賣超張數（自行 + 避險 兩組）`、`合計買進/賣出/買賣超`
- 持股率（無持股「數」，得跟 `APIPRCD.流通在外股數` join 計算）
- 融資融券：`融資買進/賣出/餘額(千股)`、`融資餘額(千元)`、`融券買進/賣出/餘額(千股)`、`融券餘額(千元)`、`融資/融券限額`、`融資/融券/整戶維持率`、`資券比`
- 借券、當沖、現金/現券償還

**Schema 差異重點**：

1. EW 用「千股」當部位單位、API 用「張」 — 但 **1 張 = 1 千股，數值完全一致**，欄名換掉即可。
2. EW 沒區分自營商「自行」vs「避險」，要把兩組加總。
3. EW 的「外資總持股數(千股)」沒有對應 — 要跟 APIPRCD 的「流通在外股數」做 join 後 `持股率 × 流通在外股數 / 100` 計算。
4. EW 的「融資使用率 / 融券使用率」沒有對應 — 要從 `餘額 / 限額 × 100` 計算。
5. APIPRCD 沒「開盤價-除權息」等四欄；可用 `OHLC × adjfac` 計算（TEJ adjfac 為除權息調整因子）。
6. APIPRCD 「資料日」是 TIMESTAMPTZ UTC（2026-01-02 00:00:00+00:00），EW 是 `YYYYMMDD` int — 需 format 轉換。

### M1（前置）progress doc 建立

## Fallback 指引

```bash
cd /home/kevin/gs-scraper/QUANTDATA
git log --oneline -10                       # 找 commit hash
git reset --hard <hash-before-M2>           # 回到 fetch_tej.py 改寫前

# RAW CSV 被 merge 動過要還原
git status RAW_SOURCES/                     # RAW 不在 repo，無法 git checkout
# 從 backup snapshot 還原（_backup/ 或 _quarantine/）
```
