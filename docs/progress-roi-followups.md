# 2026-06-02 — 3 件 ROI 高的後續：strategy + PCP-spot + rf curve

## 目標

1. **inst_market_factors strategy backtest**：用 `foreign_total_twd_bn_60d_zscore`
   當 signal 做 TAIEX 多空，10 bps 成本，回報 Sharpe / CAGR / MaxDD vs B&H
2. **txo_1min_intraday → PCP-implied spot realized vol**：每 minute 用 ATM
   put-call parity 反推 spot，再計算 realized vol（比現在用單一 contract close
   的 proxy 精準很多）
3. **rf curve interpolation**：用 macro_daily 的 IRX (13w) / TNX (10y) 做
   curve，給 BS-IV 按 contract maturity 取對應 rf

## 計畫

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔 + 設計 |
| **M2** | rf curve（最簡單）：`_get_rf_curve(date, T_years)` linear interp IRX↔TNX；`_bs_price/_bs_iv` 與 `build_txo_daily_features` 用新版 |
| **M3** | 重寫 `build_txo_1min_intraday_features` 用 PCP 隱含 spot 算 realized vol |
| **M4** | `strategies/inst_zscore/strategy.py` + 跑回測 → 出 `inst_zscore_backtest_report.md`（CAGR / Sharpe / MaxDD / IS vs OOS） |
| **M5** | 重生 dashboard + 進度檔收尾 |

## 設計

### M2 rf curve

```python
# macro_daily 已有 IRX (3M)、TNX (10Y)；都是 % yield
# 假設名稱對應：IRX → 0.25 年、TNX → 10 年
def _get_rf_curve(d: dt.date, T_years: float) -> float:
    """Linear interp between (0.25y, IRX/100) and (10y, TNX/100) for date d.
    Fallback: _get_rf(d) flat rate."""
```

`_bs_price` / `_bs_iv` 改成接 `T_years` 參數時 lookup curve；原本只接 rf 仍維持。

### M3 PCP-spot

對每個 (trade_date, minute)，挑最接近 spot 的 strike（initial guess = top
volume strike），抓出 C/P 兩筆同 strike 同 minute 的 close。Spot ≈ C - P +
K·exp(-rT)。T 從 expiry_month 算（到月底）。

得 (date, minute, spot) 序列，per-day 計算 log-return std × √(N_minutes_per_year)。

### M4 inst_zscore strategy

Signal：每天看 `foreign_total_twd_bn_60d_zscore`：
- z > +1.0 → next day long TAIEX (一個 60d standard buy 強度)
- z < -1.0 → next day short
- 中間 → flat

Underlying：TAIEX from macro_daily（trading_date + close）

回測：
- 10 bps round-trip cost
- 日 rebalance
- 2024-08 ~ 2026-04（取 60d 後資料 ~ 415 trading days）
- IS / OOS split: 70/30

回報：CAGR / Sharpe / MaxDD / Calmar / Hit rate / vs B&H

`strategies/inst_zscore/strategy.py` + `inst_zscore_backtest_report.md`。

## 進度日誌

### M2 — rf curve  `08bf8ca`

`_get_rf(date, T_years)`：log-T 線性 interp IRX(0.25y) → TNX(10y)，再用
`rf_daily/IRX` 比率拉到 TWD 水準。`build_txo_daily_features` 4 處 `_bs_iv`
自動吃 curve。

實測 2026-01-15：0.25y→1.225%、10y→1.429%（upward sloping，合理）。
30 BS unit test backward-compat 全綠。

### M3 — PCP-implied spot realized vol  `9d481ff`

`build_txo_1min_intraday_features` 重寫：每 minute 取 ATM strike 同 minute
的 call/put close，spot = C-P+K·exp(-rT)；T 從 expiry_month 推第 3 週三；
rf 從 M2 的 curve 取。

37 rows 結果：mean realized vol **41%**（vs 舊版 280%），range 18-96%
——接近真實 TAIEX 隱含 vol。

### M4 — inst_zscore strategy backtest  `(M4 commit)`

`strategies/inst_zscore/strategy.py` + `inst_zscore_backtest_report.md`：

| 期間 | strategy Sharpe | B&H Sharpe |
|---|---|---|
| 全期 (415d) | -0.66 | 1.37 |
| IS (290d) | -0.95 | 0.72 |
| OOS (125d) | -0.05 | 2.93 |

**Negative result**：signal 沒 alpha。後續 M5 補了 threshold sweep + 逆勢測
試，兩邊都輸（逆勢 -0.96）。報告含完整 caveats + 可嘗試方向。

### M5 — 收尾

- inst_zscore 報告補 threshold sweep + 逆勢結論
- dashboard 重生 OK 32 / STALE 9 / EMPTY 0（不變）
- 全 pytest 148 passed

## 三件事總結

| 任務 | 結果 |
|---|---|
| rf curve | ✅ 實作完成，IRX/TNX 形狀 × TWD 水準的混合 |
| PCP-spot vol | ✅ 從 280%/280% 改到 41% mean，貼近真實 vol |
| inst_zscore 策略 | ⚠️ negative result（無 alpha），但已有完整 framework 可繼續 iterate |

## Fallback

```bash
git revert HEAD~5..HEAD
git checkout HEAD~5 -- src/qd_ingest/sources/derived.py
rm -rf strategies/inst_zscore/
```
