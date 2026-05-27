# 2026-05-27 — TXO daily features 接 cron（bottleneck #5）

## 觸發

`/safe-yolo 陸續按照推薦排序解決還未解決的問題`（#1–#4 已完成；本輪做 #5）

## 目標

2 個 P2 view 卡在 2026-04-01（55d STALE，無 auto-refresh）：

| view | 來源（舊） |
|---|---|
| `txo_daily_features` | gold copy of **手動 dump** `RAW_SOURCES/SUPPLEMENT/DERIVED/txo_daily_features.parquet`（`copy_txo_daily_features` in derived.py）|
| `txo_daily_features_snapshot` | 上面的 snapshot（`materialize_txo_daily_features_snapshot`）|

`txo_daily_features` 是**每日一列**的 TXO 摘要序列，12 欄：`date, pcr_vol, pcr_oi, max_pain, max_pain_dist, total_call_vol, total_put_vol, total_call_oi, total_put_oi(註：欄名為 total_oi), atm_iv_proxy, iv_skew_proxy, mxf_close`。

## 勘查結論

- **舊 raw tick 也是一次性 dump**：`RAW_SOURCES/選擇權日盤逐筆原始資料_TXO.parquet`（2.68M rows，max 2026-04-01，無排程）。靠它聚合無法 un-stale（它自己就停在 2026-04-01）。
- **唯一 fresh 來源 = FinMind `TaiwanOptionDaily`**：用 #3 建好的 crawler 抓。**by-date bulk 已驗證**：`data_id='TXO', start=end=2026-05-26` 單呼叫回 **12,852 列**（全 strike × call/put × session），欄位 `date, option_id, contract_date, strike_price, call_put, open/max/min/close, volume, settlement_price, open_interest, trading_session`。增量每日 1 呼叫，便宜。
- **TEJ 無選擇權**；TaiwanOptionDaily 在 FinMind catalog 已定義（`taiwan_option_daily`, global_date, pk=date,option_id,contract_date,strike_price,call_put,trading_session）但**尚未抓過**。

## 可從 FinMind daily 重建的 10 欄（乾淨）

| 欄 | 算法（aggregate 全 strike/contract，取 regular/position session）|
|---|---|
| total_call_vol / total_put_vol | `SUM(volume) WHERE call_put=...` |
| total_call_oi / total_oi(put) | `SUM(open_interest) WHERE call_put=...` |
| pcr_vol | total_put_vol / total_call_vol |
| pcr_oi | total_put_oi / total_call_oi |
| max_pain | 對每個 strike 算 call+put writer pain，取最小 → strike |
| max_pain_dist | (max_pain − spot) / spot（spot = MXF/TAIEX close）|
| mxf_close | 從 `mtx_continuous_d`（已 auto-refresh）取當日 MXF close |

## ⚠️ 阻塞點：2 個 IV proxy 欄語意不可考

`atm_iv_proxy`（例 2020-03-02 = **0.018345**）和 `iv_skew_proxy`（例 = **12.4**）由一支**不在 repo 內**的外部 script 算出。`atm_iv_proxy ≈ 1.8%` 不像年化 BS-IV（TXO 應 ~20–50%），比較像日波動或某 price-based proxy；`iv_skew_proxy = 12.4` 也無從還原定義。**沒有原始程式 → 無法逐筆對齊驗證**（不像 #4 TAIFEX 可逐筆驗證）。

這代表：若要 un-stale，這 2 欄只能**重新定義**。而重新定義會改變一個 gold 欄位在「整段歷史」的語意 —— 對下游而言不可逆。這是 medallion gold contract 的語意決策，需要使用者拍板。

### 選項

1. **重新定義 + 全史重算（推薦）**：用標準定義 —— `atm_iv_proxy` = ATM 選擇權年化 Black-Scholes IV；`iv_skew_proxy` = (OTM put IV − OTM call IV)（固定 moneyness）。**從 FinMind 全史重算整條序列**（2020→今，~1500 交易日，backfill ~1h），全欄單一一致方法、可重現、自動刷新。舊外部值整段被取代（與 #4 用 fresh 源取代手動 dump 同哲學）。代價：歷史數值改變 + ~1h backfill + BS-IV 計算。
2. **carry-forward**：只重算 10 個乾淨欄，2 個 IV 欄沿用最後已知值（或 NaN）。最小驚擾，但 IV 欄等於放棄。
3. **drop 2 欄**：txo_daily_features 改成 10 欄（移除 IV proxy）。最乾淨但破壞 schema 契約。

## 計畫（待 IV 決策後執行）

| Mn | 內容 |
|---|---|
| **M1** | 本進度檔（feasibility 已驗證）|
| **M2** | 擴充 `fetch_finmind.py` 抓 `TaiwanOptionDaily`（data_id=TXO, by-date）+ 註冊 bronze/silver view + 跑增量 |
| **M3** | `build_txo_daily_features()`（從 FinMind option daily aggregate；IV 依決策）+ 接 daily_refresh + 全史重算 |
| **M4** | materialize snapshot + dashboard 驗 2 P2 STALE→OK + commit |

## Fallback

- FinMind option 抓不到 → 維持舊 silver partition，non-fatal。
- 全史 backfill 太久 → 先做增量（補 2026-04-01→今的洞）證明 pipeline，全史另跑。

## 決策

使用者選 **選項 1：重新定義 + 全史重算**（標準 BS 方法）。

## 完成日誌

### M2 — fetch_finmind 擴充 + bronze view（commit `965750d`）

- `fetch_finmind.py` 加 `OPTION_DATASETS`（`TaiwanOptionDaily`→`taiwan_option_daily`, data_id=TXO）+ `by_range` kind（range endpoint 帶 data_id 會回整段，year-chunk）。
- 全史 backfill：**2,844,692 rows**（2020-01-02→2026-05-27），snapshot 3.05GB。
- `restore_finmind_views.py` 註冊 `finmind_txo_option_daily` view（best-effort，舊 snapshot 無此表）。

### M3 — `build_txo_daily_features()`（commit `92c4fbb`）

- 讀 `finmind_txo_option_daily`（`trading_session='position'` —— 該 session 才同時有 volume + OI；`after_market` OI=0）。
- **10 個乾淨欄**：total call/put vol+oi（全 contract 加總）、pcr_vol/oi。
- **front monthly contract**（6-digit contract_date，3rd-Wed expiry ≥ date 取最近）上算：
  - **spot = put-call parity**（|call_settle − put_settle| 最小的 strike → spot = K + C − P）。本資料 index scale ≈ 43,900。
  - **max_pain**：對每個 strike 算 option-holder 總內含值，取最小 → strike；`max_pain_dist = (max_pain−spot)/spot`。
  - **atm_iv_proxy**：最接近 spot 的 strike，call+put BS-IV（bisection, r=1.5%, T=到 3rd-Wed/365）平均。**年化 ~19% mean**（舊外部值 0.018 非年化）。
  - **iv_skew_proxy**：OTM put(~spot×0.95) IV − OTM call(~spot×1.05) IV，~0.03（正常 equity skew）。
- `mxf_close` ← `mtx_continuous_d.close`（join key 需 coerce Timestamp→date；MXF 落後 1 天故最新 2 日 NaN）。
- 全史重算 **1,552 日**（52s）。`build_all()` 的 `"txo"` 改呼叫新 builder → 已透過 daily_refresh step 3.7 自動接上（fetch 在 step 1.7，view restore 在 3.5）。

### M4 — materialize + dashboard

- `materialize_txo_daily_features_snapshot()` → 1,552 rows。
- gap_report registry note 更新（不再「無 auto-refresh」；category 維持 daily-trading）。
- dashboard：**OK 39 → 41，STALE 6 → 4**。`txo_daily_features` + snapshot 兩 P2 全 OK（0d, 2026-05-27）。

## 後續

- 剩 STALE=4：`bars_1m`/`bars_1m_daily_summary`（#6，需付費源）+ `tw_inst_market_daily`/`_snapshot`（可由 tw_inst_stock_daily 聚合的 bonus）。
- IV proxy 定義已寫死在 `build_txo_daily_features`（r=1.5%、±5% moneyness skew、front monthly）；如需改定義在該函式調整即可，全史會一致重算。
