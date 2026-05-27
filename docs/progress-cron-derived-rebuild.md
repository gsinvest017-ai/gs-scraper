# 2026-05-27 — daily_refresh.sh 加 derived gold rebuild step

## 觸發

`/safe-yolo 好`（接續 bottleneck recap，使用者點頭做 #1：最高槓桿、零風險的 cron derived rebuild）

## 目標

`scripts/daily_refresh.sh` 目前只跑 fetch → ingest → build-catalog → restore_finmind → gap_report，**沒有重生衍生 gold**。導致每天 TEJ silver 更新後，13+ 個 derived gold parquet（stock_factor_daily / inst_flow_factors / margin_factors / futures_inst_factors / futures_bar_factors / market_inst_aggregated 等）停在上次手動 `build_all()` 的日期，隔天在 dashboard 變 INFO（lag）。

加一個 step 把 `python -m qd_ingest.sources.derived`（= `build_all()`）接進 cron，讓 gold 每天自動跟著 silver 重生。

## 設計決策

- **插入位置**：step 3.5（restore_finmind）之後、step 4（gap_report regen）之前
  - 之後 → catalog views 已重建 + finmind 已還原，catalog-reading builders（qc_snapshot / finmind_canonical / accounting_snapshot / 3 個 *_snapshot）讀得到 fresh view
  - 之前 → dashboard regen 反映剛重生的 gold
- **非致命**：跟 restore_finmind 一樣 `|| log WARN`，因為部分 builder 讀 catalog（若 DuckDB UI 持鎖可能失敗），不該整個 cron 因此 exit。silver-based polars builders 不受鎖影響，仍會成功。
- **DRY_RUN 早退不變**：dry-run 在 step 1 後就 exit，不會跑到 derived。

## Milestone

| Mn | 內容 | 狀態 |
|---|---|---|
| **M1** | 本進度檔 | ⏳ |
| **M2** | daily_refresh.sh 加 step「rebuild derived gold」+ 更新 header 註解 step 編號；本機驗證 derived module 跑得起來 | ⏳ |
| **M3** | docs-site changelog + ops/daily-refresh.md（若存在）更新；commit；push | ⏳ |

## Fallback

- 寫壞 shell：`git revert`；daily_refresh.sh 是冪等，重跑無副作用
- derived rebuild 在 cron 失敗：non-fatal 設計，cron 仍完成 catalog + dashboard；可事後手動 `python -m qd_ingest.sources.derived`

## 完成日誌

（M2-M3 後追加）
