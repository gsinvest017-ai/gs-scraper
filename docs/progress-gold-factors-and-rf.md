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

## Fallback

```bash
git revert HEAD~4..HEAD
git checkout HEAD~4 -- src/qd_ingest/sources/derived.py
```
