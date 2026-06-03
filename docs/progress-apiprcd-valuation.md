# progress — APIPRCD 估值/微結構欄（tw_stock_valuation_daily）落 silver

## 目標

`TWN/APIPRCD`（交易資料-股價資料，29 欄）的 OHLCV 部分早已在 `bars_1d`
(asset_class=tw_stock)，但 APIPRCD 真正獨有的**估值 + 微結構欄**（報酬率 roi、
高低價差 hmlpct、周轉率 turnover、最後揭示買/賣價 bid/offer、當日均價、流通在外
股數、個股市值、市值比重、成交金額比重、本益比 per、股價淨值比 pbr、股利殖利率、
現金股利率、本益比/淨值比/營收比 TEJ 版）目前**不在任何 catalog view**——當初
`adapt_apiprcd_to_ew_stock` 只把 OHLCV + 少數欄寫進 EW CSV，下游 `bars_1d` 又只
留 OHLCV，估值欄被丟掉。

本任務新增 silver 表 `tw_stock_valuation_daily`，補齊這些欄，key `(stock_id,
trading_date)` 可與 `bars_1d` join。

## 計畫 milestone

- **M1 — 程式接線**：fetch_tej.py 新增 `stock_valuation` logical table
  （`adapt_apiprcd_to_valuation_silver` + `write_silver_stock_valuation`，English
  schema、year 分區、OHLCV 不重複）、`fetch()` 30-day chunk branch、
  `_silver_max_date` 接點；catalog.py view `tw_stock_valuation_daily`；
  dataset_meta.py + gap_report.py 註冊。
- **M2 — 回補 + 驗證**：`--table stock_valuation --start 20200101` 落 silver，
  build-catalog，DuckDB 驗證行數/欄位/per-pbr-yield 非空；pytest。
- **M3 — dashboard + UI refresh**：gap_report 重生、`POST /api/refresh`。

## 設計決策

- **欄位策略**：與 accounting_raw/capital_changes 的「保留中文寬表」不同，APIPRCD
  是 tidy 29 欄、欄義明確，值得正規化成 **English schema + 顯式 pyarrow type**
  （float64 估值、Int64 大整數如股數/市值/筆數、string market）。
- **不重複 OHLCV**：open/high/low/close/volume 已是 bars_1d 的 canonical，不再寫一份；
  本表專注估值 + 微結構，靠 (stock_id, trading_date) join bars。
- **位置**：`silver/flows/tw_stock_valuation_daily/year=YYYY/`（比照其他 per-stock
  daily 表如 tw_stock_trading_attrs_daily）。
- **chunk_days=30**：實測 30 天≈54K rows / 11s，未觸 LimitExceeded；auto-halving 兜底。
- **回補起點 20200101**：與 capital_changes 一致、訂閱包 API 可得範圍；bars_1d
  tw_stock 雖回到 2010，但估值欄先補近 6 年，深歷史可日後 --start 往前 top-up。

## 進度日誌

（每完成一個 milestone 追加）
