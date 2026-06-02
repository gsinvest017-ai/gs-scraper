# 2026-06-02 — txo_1min + tw_inst_market_daily 衍生 gold + rf 進 BS-IV

## 目標

三件事，全部圍繞「把新近 ingest 的 silver 升上 gold」+「BS-IV 變動態 rf」：

1. **`build_txo_1min_intraday_features()`** — 從 silver/options/txo_1min 衍生
   per-day intraday vol/volume features → `gold/features/txo_1min_intraday.parquet`
2. **`build_inst_market_factors()`** — 從 silver/flows/tw_inst_market_daily
   衍生 z-score / rolling sum factors → `gold/features/inst_market_factors.parquet`
3. **`_bs_price` / `_bs_iv` 接 rf 參數** — 取代 module-level `_TXO_RF=0.015`
   寫死；`build_txo_daily_features` 用 `rf_daily` 對應日 lookup

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 |
| **M2** | `build_txo_1min_intraday_features()`：per-day total_volume / n_strikes / peak_minute / atm_realized_vol → gold |
| **M3** | `build_inst_market_factors()`：每個 identity 的 5d/20d/60d 滾動 sum + 60d z-score → gold |
| **M4** | `_bs_price` / `_bs_iv` 加 rf 參數（default None → 退回 `_TXO_RF`）；`build_txo_daily_features` 每日 lookup rf_daily；BS pytest 確認 backward-compat |
| **M5** | 重建 catalog + 重生 dashboard + 收尾 |

## 設計

### M2 txo_1min intraday

Source 2.19M rows: (trade_date, expiry_month, strike, option_type, minute, OHLCV).
Aggregate per (trade_date) 一條 row：

| col | 算法 |
|---|---|
| trading_date | from `trade_date` |
| total_volume | `SUM(volume)` |
| n_strikes | `COUNT(DISTINCT strike_price)` |
| n_minutes_active | `COUNT(DISTINCT minute) WHERE volume > 0` |
| peak_minute | `argmax(volume) over per-day` |
| peak_volume | max minute volume |
| atm_close_realized_vol | std(log-return) of most-traded strike+option_type close-price across minutes × √252×N |

預計 30~40 rows（2026-03-09 ~ 04-22）。

### M3 inst_market_factors

Source 474 rows daily TWD flows。Per trading_date 計算（across all 4 entity cols）：

| col base | rolling window |
|---|---|
| `foreign_total_twd_bn` | 5d sum、20d sum、60d sum、60d z-score |
| `sitc_twd_bn` | 同上 |
| `dealer_total_twd_bn` | 同上 |
| `three_inst_total_twd_bn` | 同上 |

→ `gold/features/inst_market_factors.parquet` 約 414 rows（前 60 天 z-score 為 NaN）。

### M4 rf 進 BS-IV

設計：

```python
_RF_BY_DATE: dict[dt.date, float] | None = None

def _get_rf(d: dt.date) -> float:
    """Lookup risk-free rate for date d; fall back to constant if missing."""
    global _RF_BY_DATE
    if _RF_BY_DATE is None:
        try:
            df = pd.read_parquet(SILVER / "macro" / "rf_daily.parquet")
            df["date"] = pd.to_datetime(df["date"]).dt.date
            _RF_BY_DATE = dict(zip(df["date"], df["rf"]))
        except FileNotFoundError:
            _RF_BY_DATE = {}
    return float(_RF_BY_DATE.get(d, _TXO_RF))

def _bs_price(S, K, T, sigma, is_call, rf=None):
    rf = _TXO_RF if rf is None else rf
    ...  # 既有公式不動
```

既有 12+ BS unit tests 用 default None → fallback `_TXO_RF=0.015`，不會壞。

## 進度日誌

### M2 — 兩個 gold builder  `4537b0e`

- `build_txo_1min_intraday_features()`：37 rows / 2026-03-09 ~ 04-22；
  per-day total_volume / n_strikes / peak_minute / peak_volume +
  atm_close_realized_vol（最熱門 strike+option 的 close-log-return std × √75600）
- `build_inst_market_factors()`：474 rows / 2024-05-02 ~ 2026-04-16；
  4 個 entity 各 5d/20d/60d 滾動 sum + 60d z-score（共 16 個 factor cols）
- 兩條都加進 `build_all()`

驗證：實跑 sample row z-score 範圍合理（-1.5 ~ +1.7），最近日 foreign 1.087
反映外資買盤強勢。

### M3 — rf 進 BS-IV  `6eefbc2`

- 加 `_get_rf(date)` lazy-load `silver/macro/rf_daily.parquet`（fallback `_TXO_RF=0.015`）
- `_bs_price` / `_bs_iv` 加 `rf=None` 參數（default fallback 維持 backward-compat）
- `build_txo_daily_features` 4 處 `_bs_iv` 改傳 `rf=_get_rf(d)`
- 加 6 個新 unit test（U-027）：parameter override round-trip / rf=0 vs 5%
  價差 / default fallback 等價；**全 148 passed**（既有 142 + 6）

實測：
- `_get_rf(2026-01-15)` → 0.01225（vs 寫死 0.015，貼近真實 Taiwan rf）
- 超出 rf_daily 日期範圍自動 fallback
- `build_txo_daily_features` 重生 1552 rows，IV proxy 微調但流程不爆

### M4 — catalog 註冊 + dashboard regen + 收尾

- `catalog.py` gold loop 加 txo_1min_intraday + inst_market_factors
- `gap_report.DATASETS` 加兩條 P2 / P1 entry
- catalog 重建 swap：**61 → 63 views**
- dashboard：OK 32 / STALE 9（兩條新 gold 繼承上游 STALE）/ EMPTY 0
- `meta/gap_comments.json` 補兩條註解

## 下一輪建議（按 ROI）

- **`inst_market_factors` 串進 strategy backtest**：60d z-score 是經典 「外資逆勢
  / 順勢」 signal，可加進 strategy pool 測 OOS
- **`txo_1min_intraday` 精緻化**：目前 realized vol 用單一最熱門 contract，
  可改為「ATM put-call parity 隱含 spot 序列 → realized vol」更接近 underlying
- **接 cron**：兩條 gold 已在 build_all 內 → step 3.7 自動跑，無須額外 step
- **rf curve**：目前只用單一 rf 對應日；要更精細可用 rf_daily + tenor IRX/TNX
  做曲線 interpolation（短期 IRX、長期 TNX）給長天期 TXO

## Fallback

```bash
git revert HEAD~4..HEAD
git checkout HEAD~4 -- src/qd_ingest/sources/derived.py
```
