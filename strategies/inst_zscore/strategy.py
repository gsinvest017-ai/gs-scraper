"""inst_zscore strategy — TAIEX timing with foreign-flow 60d z-score.

Signal:
  z = foreign_total_twd_bn_60d_zscore (from inst_market_factors)
  position(t+1) = +1 if z > +1 (順勢；外資已連續強買 → 跟單)
                  -1 if z < -1 (順勢；外資已連續強賣 → 跟空)
                   0 otherwise

Underlying: TAIEX daily close (from macro_daily, symbol='TAIEX').
Costs: 10 bps round-trip per position change.
Backtest window: 2024-08-01 (60d after data start) to 2026-04-16 (data end).
IS/OOS split: 70/30.

Output:
  strategies/inst_zscore/inst_zscore_backtest_report.md
  strategies/inst_zscore/equity_curve.png  (optional, matplotlib)

Usage:
  .venv/bin/python strategies/inst_zscore/strategy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load inst_market_factors and TAIEX daily close."""
    repo = Path(__file__).resolve().parents[2]
    inst = pd.read_parquet(repo / "gold" / "features" / "inst_market_factors.parquet")
    inst["trading_date"] = pd.to_datetime(inst["trading_date"]).dt.date

    macro = pd.read_parquet(repo / "silver" / "macro" / "macro_daily.parquet")
    macro = macro[macro["symbol"] == "TAIEX"].copy()
    macro["trading_date"] = pd.to_datetime(macro["trading_date"]).dt.date
    macro = macro[["trading_date", "close"]].sort_values("trading_date")
    return inst, macro


def backtest(z_threshold: float = 1.0, cost_bps: float = 10.0,
             oos_frac: float = 0.30) -> dict:
    inst, macro = _load_data()

    df = inst.merge(macro, on="trading_date", how="inner")
    df = df.sort_values("trading_date").reset_index(drop=True)
    df = df.dropna(subset=["foreign_total_twd_bn_60d_zscore", "close"])

    # Signal: +1 if z > +threshold, -1 if z < -threshold, else 0
    df["signal"] = 0
    df.loc[df["foreign_total_twd_bn_60d_zscore"] > z_threshold, "signal"] = 1
    df.loc[df["foreign_total_twd_bn_60d_zscore"] < -z_threshold, "signal"] = -1
    # Trade next day on signal of today (no look-ahead)
    df["position"] = df["signal"].shift(1).fillna(0).astype(int)
    df["ret"] = df["close"].pct_change().fillna(0)

    # PnL: position × forward return - cost on position change
    df["raw_pnl"] = df["position"] * df["ret"]
    df["traded"] = (df["position"].diff().abs() > 0).astype(int)
    df["cost"] = df["traded"] * (cost_bps / 10000.0)
    df["pnl"] = df["raw_pnl"] - df["cost"]

    df["equity"] = (1 + df["pnl"]).cumprod()
    df["bh_equity"] = (1 + df["ret"]).cumprod()

    # IS/OOS split
    n = len(df)
    is_end = int(n * (1 - oos_frac))
    is_df = df.iloc[:is_end].copy()
    oos_df = df.iloc[is_end:].copy()

    def _stats(d: pd.DataFrame, label: str) -> dict:
        rets = d["pnl"]
        if rets.std() == 0 or len(rets) < 2:
            sharpe = float("nan")
        else:
            sharpe = float(rets.mean() / rets.std() * np.sqrt(252))
        cum = (1 + rets).prod()
        years = (d["trading_date"].iloc[-1] - d["trading_date"].iloc[0]).days / 365.25
        cagr = float(cum ** (1 / years) - 1) if years > 0 else float("nan")
        eq = (1 + rets).cumprod()
        peak = eq.cummax()
        dd = (eq / peak - 1).min()
        bh_rets = d["ret"]
        bh_cum = (1 + bh_rets).prod()
        bh_cagr = float(bh_cum ** (1 / years) - 1) if years > 0 else float("nan")
        bh_sharpe = float(bh_rets.mean() / bh_rets.std() * np.sqrt(252)) if bh_rets.std() else float("nan")
        return {
            "split": label, "n_days": len(d),
            "start": str(d["trading_date"].iloc[0]),
            "end":   str(d["trading_date"].iloc[-1]),
            "n_trades": int(d["traded"].sum()),
            "n_long":  int((d["position"] == 1).sum()),
            "n_short": int((d["position"] == -1).sum()),
            "n_flat":  int((d["position"] == 0).sum()),
            "cagr":   round(cagr, 4),
            "sharpe": round(sharpe, 3),
            "max_dd": round(float(dd), 4),
            "bh_cagr": round(bh_cagr, 4),
            "bh_sharpe": round(bh_sharpe, 3),
            "alpha_cagr": round(cagr - bh_cagr, 4),
            "calmar": round(cagr / abs(dd), 3) if dd < 0 else float("nan"),
        }

    return {
        "params": {"z_threshold": z_threshold, "cost_bps": cost_bps, "oos_frac": oos_frac},
        "all": _stats(df, "all"),
        "is": _stats(is_df, "IS"),
        "oos": _stats(oos_df, "OOS"),
        "df": df,  # for plot
    }


def _write_report(r: dict, fp: Path) -> None:
    p = r["params"]
    sec = lambda s: f"## {s['split']}（{s['n_days']} days, {s['start']} → {s['end']}）\n\n" \
                    f"| 指標 | strategy | B&H | Δ |\n|---|---|---|---|\n" \
                    f"| CAGR | {s['cagr']:.2%} | {s['bh_cagr']:.2%} | {s['alpha_cagr']:+.2%} |\n" \
                    f"| Sharpe | {s['sharpe']} | {s['bh_sharpe']} | — |\n" \
                    f"| Max DD | {s['max_dd']:.2%} | — | — |\n" \
                    f"| Calmar | {s['calmar']} | — | — |\n" \
                    f"| n_trades | {s['n_trades']} | — | — |\n" \
                    f"| n_long/short/flat | {s['n_long']}/{s['n_short']}/{s['n_flat']} | — | — |\n\n"
    body = f"""# inst_zscore strategy — 回測報告

> generated by `strategies/inst_zscore/strategy.py`

## 設計

- **Signal**：`foreign_total_twd_bn_60d_zscore` (from `inst_market_factors`)
- **規則**：z > +{p['z_threshold']} → next-day +1（外資強買 → 跟單 long）
- **規則**：z < -{p['z_threshold']} → next-day -1（外資強賣 → 跟單 short）
- **其他**：position = 0（flat）
- **Underlying**：TAIEX 日 close（from `macro_daily`）
- **Cost**：{p['cost_bps']} bps/部位變化（round-trip 算在 traded day）
- **IS/OOS**：{int((1 - p['oos_frac']) * 100)}% / {int(p['oos_frac'] * 100)}%

## 樣本全期

{sec(r['all'])}

## In-sample

{sec(r['is'])}

## Out-of-sample

{sec(r['oos'])}

## Caveats

1. **樣本短**：only ~415 trading days post-60d-warmup（2024-08 ~ 2026-04）。
   Sharpe / CAGR 高方差，OOS 不穩。
2. **signal 用「順勢」**（z>+1 long, z<-1 short）— 也可測「逆勢」（反向），
   單跑一次無法判斷哪邊有 alpha。
3. **沒考慮 sub-day execution**：close-to-close return；實務 spread 衝擊可能高
   於 10 bps。
4. **沒考慮借券成本**：short TAIEX 實務不可直接做（用期貨 / inverse ETF）；
   此處純粹學術 demo。

## 後續

- 跑 grid search of z_threshold ∈ {{0.5, 0.75, 1.0, 1.5, 2.0}}
- 加 sitc + dealer z-score 多 signal combo
- 換 underlying 為 TXF（可雙向）
- walk-forward CV 取代單一 70/30 split
"""
    fp.write_text(body, encoding="utf-8")


def main() -> int:
    r = backtest()
    repo = Path(__file__).resolve().parents[2]
    out_md = repo / "strategies" / "inst_zscore" / "inst_zscore_backtest_report.md"
    _write_report(r, out_md)
    # Print summary to stdout
    print(f"=== inst_zscore 回測 (全期 {r['all']['n_days']} days) ===")
    for k, v in r["all"].items():
        if k != "split":
            print(f"  {k:>14}: {v}")
    print(f"=== IS ===")
    for k, v in r["is"].items():
        if k != "split":
            print(f"  {k:>14}: {v}")
    print(f"=== OOS ===")
    for k, v in r["oos"].items():
        if k != "split":
            print(f"  {k:>14}: {v}")
    print(f"\nreport → {out_md.relative_to(repo)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
