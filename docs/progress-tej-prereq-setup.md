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
