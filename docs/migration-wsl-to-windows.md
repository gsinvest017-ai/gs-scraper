# QUANTDATA 遷移 checklist — WSL2 → Windows host

> **狀態:DRY-RUN 計畫文件。本檔只列步驟,未執行任何遷移。**
> 每一段指令都標了「在哪邊跑」(WSL bash / Windows PowerShell)。實際執行前
> 請逐段確認,尤其是 robocopy / venv 重建那兩步。

> **一鍵版**:本檔第 3~7 步(robocopy → 重建 venv → 重生 catalog → 驗收)
> 已包成 `scripts/migrate_to_windows.ps1`。在 **Windows PowerShell** 跑:
> ```powershell
> .\scripts\migrate_to_windows.ps1            # DRY-RUN 預覽(預設)
> .\scripts\migrate_to_windows.ps1 -Apply     # 真的執行
> ```
> 預設自動偵測 WSL distro,目標 `C:\QUANTDATA`(可用 `-Target` / `-Distro` 覆寫)。
> 下面的逐步說明保留作為手動 / 排錯參考。

---

## 0. 前置認知

- 來源:WSL2 `/home/kevin/gs-scraper/QUANTDATA`(約 **39 GB**:bronze 21G / silver 16G / gold 906M / catalog 268K)
- 目標:Windows native,例如 `C:\QUANTDATA`
- 核心事實:
  - **資料(parquet)跨平台二進位通用** → 直接複製即可
  - **DuckDB catalog 只是 view(268K)** → 在 Windows 重生最保險(view 可能含 WSL 絕對路徑)
  - **`.venv` 不可搬** → Windows 重建(依賴 `duckdb/pyarrow/pandas` 都有 Windows wheel,無編譯地雷)
  - **`.sh` / cron 在 Windows native 跑不動** → 用 `run.ps1` + Task Scheduler

---

## 1. 遷移前快照(WSL bash)— 記錄基準,事後比對

```bash
# [WSL] 不執行也可,但建議先存一份 manifest 供事後核對
cd /home/kevin/gs-scraper/QUANTDATA
du -sh bronze silver gold catalog reference meta        # 各層大小
find bronze silver gold -name '*.parquet' | wc -l       # parquet 檔數
git rev-parse HEAD                                       # 當前 commit
git status --short                                       # 未 commit 變更(注意:有未 commit 的 audit jsonl)
.venv/bin/python -m pytest -q tests/                     # 確認搬移前是綠的
```

> ⚠️ 目前 `git status` 有未追蹤的 `meta/audit/ingest_2026-06-*.jsonl` 與
> `tmp/`。決定要不要一起搬(audit 要、tmp 不要)。

---

## 2. 停掉會鎖檔的程序(WSL bash)

```bash
# [WSL] DuckDB CLI / Search UI 會鎖 catalog,搬之前先確認 lock free
fuser catalog/quant.duckdb 2>/dev/null && echo "LOCKED — 先關掉" || echo "lock free"
pgrep -af 'ui.search.app|duckdb' || echo "無 UI / duckdb 程序"
```

---

## 3. 複製資料(從 Windows PowerShell 用 robocopy 拉,最穩)

```powershell
# [Windows PowerShell] 同機 WSL→Windows,robocopy 比 WSL 寫 /mnt/c 快又可續傳
# /E=含子目錄 /XD=排除目錄 /XF=排除檔 /R:2 /W:5=重試  /TEE /LOG=記錄
$src = "\\wsl$\<distro>\home\kevin\gs-scraper\QUANTDATA"   # 換成你的 distro 名
$dst = "C:\QUANTDATA"

robocopy $src $dst /E `
  /XD .venv .git __pycache__ tmp node_modules `
  /XF "*.pyc" `
  /R:2 /W:5 /TEE /LOG:C:\QUANTDATA_migrate.log
```

- `.venv` 排除(重建)、`.git` 看你要不要搬(也可在 Windows 重新 `git clone`)
- 39 GB 視磁碟速度約數分鐘~數十分鐘
- robocopy 退出碼 0~7 都算成功(8+ 才是錯誤)

### 3b.(替代)git 走 clone、只搬資料

```powershell
# [Windows] 程式碼用 git 拿乾淨版,只 robocopy 大資料目錄
git clone <repo-url> C:\QUANTDATA
robocopy "\\wsl$\<distro>\home\kevin\gs-scraper\QUANTDATA\bronze" C:\QUANTDATA\bronze /E
robocopy "...\silver" C:\QUANTDATA\silver /E
robocopy "...\gold"   C:\QUANTDATA\gold   /E
robocopy "...\reference" C:\QUANTDATA\reference /E
robocopy "...\meta"   C:\QUANTDATA\meta   /E
```

---

## 4. Windows 端重建 Python 環境(Windows PowerShell)

```powershell
# [Windows] 需 Python 3.11+(py launcher)
cd C:\QUANTDATA
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -e ".[ingest]"
.\.venv\Scripts\python -c "import duckdb, pyarrow, pandas; print('deps OK')"
```

> 全是 pure-wheel 依賴,不需要 MSVC / Cython(本 repo 的 `zipline` extra 是空的)。

---

## 5. 重生 catalog(Windows PowerShell)— 關鍵步驟

DuckDB view 內若指向 `/home/kevin/...` 的 parquet,在 Windows 會失效。catalog
本來就可重生,直接重建:

```powershell
# [Windows] 用倉內 CLI 重建 view 指向 Windows 路徑下的 parquet
cd C:\QUANTDATA
.\.venv\Scripts\qd-ingest build-catalog        # 確認子指令名(見 pyproject [project.scripts])
# 驗證
.\.venv\Scripts\python -c "import duckdb; con=duckdb.connect('catalog/quant.duckdb'); print(con.execute('show tables').fetchall())"
```

> 若 `qd-ingest` 沒有 `build-catalog` 子指令,改查 `scripts/` 內對應的建 catalog
> 腳本,用 `python scripts/<...>.py` 跑(避免依賴 bash)。

---

## 6. 設定環境變數(Windows PowerShell)

```powershell
# [Windows] 兩處硬編 /home/kevin 的 FinMind 路徑有 env 覆寫,Windows 要指到本機
[Environment]::SetEnvironmentVariable("FINMIND_REPO", "C:\gs-scraper\FINMIND資料集", "User")
# 若有 TEJ / FinMind API key 也一併設
[Environment]::SetEnvironmentVariable("TEJAPI_KEY", "<your_key>", "User")
```

---

## 7. 啟動驗證(Windows PowerShell)

```powershell
# [Windows] 用 run.ps1 起 Search UI(對應 WSL 的 ./run.sh ui)
cd C:\QUANTDATA
.\run.ps1 ui          # 確認 run.ps1 支援 ui 子指令;否則 python -m ui.search.app
# 瀏覽器開 http://127.0.0.1:5050/
.\.venv\Scripts\python -m pytest -q tests\     # 跑測試確認搬移無損
```

---

## 8. 排程 / 維運腳本對應(Windows 無 cron / bash)

| WSL bash 腳本 | Windows 對應做法 |
|---------------|------------------|
| `scripts/install_cron.sh` | **Task Scheduler**(`schtasks` 或 GUI)排程 `run.ps1 ingest` |
| `scripts/daily_refresh.sh` | 若無 `.ps1`,用 `python` 直接呼叫各 ingest 模組;或在 WSL 保留 |
| `scripts/backup_snapshot.sh`(rsync) | robocopy `/MIR` 取代 rsync |
| `scripts/migrate_to_host.sh`(sshpass+rsync) | 已有 `migrate_to_host.ps1` |
| `scripts/ngrok_tunnel.sh` / `tailscale_funnel.sh` | 裝 Windows 版 ngrok / tailscale 後手動跑 |

> 這些是 `/platform-compatible` 稽核標為「中嚴重度」的缺 `.ps1` 項。若要在
> Windows 完整自動化,建議先跑 `/platform-compatible --fix` 補這些 `.ps1`。

---

## 9. git hook(Windows PowerShell / git bash)

`.git/hooks/commit-msg` 在 WSL 是 symlink → `../../scripts/git-hooks/commit-msg`。
Windows git 預設不跟 symlink,重新 clone 後需重接:

```powershell
# [Windows] 複製一份(不靠 symlink)
Copy-Item scripts\git-hooks\commit-msg .git\hooks\commit-msg
```

---

## 10. 收尾核對(Windows PowerShell)

```powershell
# [Windows] 對照第 1 步的快照
(Get-ChildItem -Recurse C:\QUANTDATA\bronze -Filter *.parquet).Count   # parquet 檔數應一致
git -C C:\QUANTDATA rev-parse HEAD                                     # commit 一致
.\.venv\Scripts\python -m pytest -q tests\                            # 全綠
```

驗收標準:
- [ ] parquet 檔數 = WSL 端
- [ ] `git rev-parse HEAD` 一致 + 未 commit 變更已處理
- [ ] catalog `show tables` 回傳完整 view 清單
- [ ] `pytest` 全綠
- [ ] Search UI 在 `http://127.0.0.1:5050/` 起得來
- [ ] 確認資料無誤後,WSL 端原始資料**先留著**(驗收通過再清,別急著刪)

---

## 替代方案(不物理搬移)

若目的只是「在 Windows 用」而非「徹底脫離 WSL」,可以:
- 資料留 WSL,Windows 透過 `\\wsl$\<distro>\...` 直接存取,或
- WSL 起 server + Windows portproxy(目前 5050 已設好)從 Windows 瀏覽器連

省掉 39 GB 搬移 + 環境重建。是否真的搬到 Windows native,取決於你要不要完全
不依賴 WSL。
