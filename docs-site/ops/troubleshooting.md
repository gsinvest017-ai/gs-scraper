# 常見問題

依症狀分類。每段附「症狀 → 原因 → 解」。

---

## DuckDB lock conflicts

**症狀：**

```
_duckdb.IOException: IO Error: Could not set lock on file
"catalog/quant.duckdb": Conflicting lock is held in
/home/kevin/.local/bin/duckdb (PID 1105).
```

**原因：** DuckDB 對單檔強制 OS-level write lock。一定有另一個 process 開了 `catalog/quant.duckdb` 在寫模式（包括 `duckdb -ui` 沒加 `-readonly`）。

**解：**

```bash
# 1. 找誰
fuser catalog/quant.duckdb
lsof catalog/quant.duckdb

# 2. 看那個 PID 是什麼
ps -p <PID> -o pid,user,etime,command

# 3. 判斷
#    - 若是閒置 duckdb -ui  →  cp 備份後 kill
#    - 若是 daily_refresh 正在跑  →  等它結束
#    - 若是另一個 Claude session 的 ingest  →  問或等

# 4. 安全終止
cp catalog/quant.duckdb "catalog/quant.duckdb.bak_$(date +%Y%m%d_%H%M%S)"
kill <PID>
```

**預防：** 互動式 explore 永遠用 `duckdb -readonly -ui catalog/quant_public.duckdb`（用 read-only flag + 公開副本）。

---

## TEJAPI_KEY 在 cron 環境拿不到

**症狀：** cron 跑出 exit code 11 / `meta/audit/cron.log` 印 `TEJAPI_KEY missing`。

**原因：** cron 不繼承 fish universal var；環境變數只在登入 shell 才有。

**解：** 見 [Cron 排程 / TEJAPI_KEY 在 cron 環境拿不到？](cron.md)。

---

## `mkdocs build --strict` 失敗

**症狀：**

```
ERROR    -  Doc file 'db/views.md' contains a link to 'doesnt-exist.md'
            which is not found in the documentation files.
Aborted with a BuildError!
```

**原因：** strict 模式對 dangling link / unreferenced file / orphan asset 都 fail。

**解：**

```bash
# 看完整錯誤
.venv/bin/mkdocs build --strict 2>&1 | head -30

# 常見錯：
# 1. nav 裡引的 .md 不存在 → 在 mkdocs.yml 修
# 2. 頁面 markdown link 指向不存在的檔 → 改連結或建檔
# 3. 圖片 ![...](path) 拼錯 → 確認 docs-site/assets/ 真的有
```

---

## Cron daemon 在 WSL2 沒跑

**症狀：** `crontab -l` 有東西，但 `daily_refresh_<date>.log` 從來沒新檔。

**原因：** WSL2 預設**不自動**起 cron daemon。

**解：**

```bash
# 一次
sudo service cron start

# 永久（在 fish config 開頭加）
echo 'sudo service cron start 2>/dev/null' >> ~/.config/fish/config.fish

# 或啟用 WSL2 systemd（一勞永逸）
sudo tee /etc/wsl.conf <<'EOF'
[boot]
systemd=true
EOF
# 然後 PowerShell 跑：wsl --shutdown，再進 WSL
```

---

## FinMind sqlite view 查不出來

**症狀：**

```sql
SELECT COUNT(*) FROM finmind_stock_price;
-- Error: Failed to read SQLite database: ...sqlite_2026-05-18.sqlite
```

**原因：** view DDL 內烤的絕對路徑跟現實不符（檔被移動 / 刪掉 / 重命名）。

**解：**

```bash
# 確認檔在哪
ls -la bronze/finmind/

# 重建 view，用實際路徑
.venv/bin/python -c "
import duckdb, os
abs_db = os.path.abspath('bronze/finmind/finmind_2026-05-18.sqlite')
con = duckdb.connect('catalog/quant.duckdb')
con.execute('INSTALL sqlite; LOAD sqlite;')
con.execute('DROP VIEW IF EXISTS finmind_stock_price')
con.execute(f\"CREATE VIEW finmind_stock_price AS SELECT * FROM sqlite_scan('{abs_db}', 'taiwan_stock_price')\")
"
```

---

## fetch_tej.py timeout / 429

**症狀：**

```
httpx.ReadTimeout: timed out
# 或
HTTP 429 Too Many Requests
```

**原因：** TEJ API rate-limit / 機器網路 / 對方 backend 慢。

**解：**

- fetch_tej.py 內建 exponential backoff（retry 3 次）；先讓它跑完
- 仍失敗：把 backfill 切小段 `--date-from / --date-to`
- 半夜跑（02:00-08:00 CST）成功率高
- 看 `meta/audit/ingest_<date>.jsonl` 找出哪個 (table, symbol, date) 沒抓到，手動補

---

## silver parquet schema mismatch

**症狀：**

```
parquet schema in '/.../year=2018/x.parquet' does not match
the schema of '/.../year=2024/x.parquet':
  column 'foo' DOUBLE vs INTEGER
```

**原因：** 跨年的 schema 漂移（TEJ 中途加欄 / 改型別 / qd-ingest 標準化邏輯改過）。

**解：**

1. 對單一年份 ad-hoc query 加 `union_by_name=true`:

   ```sql
   SELECT * FROM read_parquet('silver/.../year=*/*.parquet', union_by_name=true);
   ```

2. 永久解：對舊年份重跑 ingest，讓所有年份 schema 一致

   ```bash
   .venv/bin/qd-ingest tej-stock --backfill-from 2010-01-01 --rewrite
   ```

---

## 寫鎖 + zombie process

**症狀：** `fuser catalog/quant.duckdb` 顯示某 PID，但 `ps -p <PID>` 說 process 不存在。

**原因：** 偶發 stale lock — process 被 OOM-killed 但 OS lock metadata 沒清。

**解：**

```bash
# 確認 process 真的不在
ps -ef | grep <PID>      # 應該空

# 重啟 catalog（DuckDB 1.5+ 會 detect stale lock）
.venv/bin/python -c "
import duckdb
con = duckdb.connect('catalog/quant.duckdb', read_only=True)
print(con.execute('SELECT COUNT(*) FROM bars_1d').fetchone())
"
```

若還是 lock：

```bash
# 暴力解：rsync 內容到新檔再換掉
cp catalog/quant.duckdb catalog/quant.duckdb.bak
mv catalog/quant.duckdb catalog/quant.duckdb.locked
mv catalog/quant.duckdb.bak catalog/quant.duckdb
```

---

## `qd-ingest build-catalog` 沒效果

**症狀：** 跑完沒報錯，但 view 還是舊的。

**原因：** 寫鎖被卡住，`qd-ingest` 走了 staging swap，但 swap 沒完成（debugging 路徑）。

**解：**

```bash
# 1. 確認沒寫鎖
fuser catalog/quant.duckdb

# 2. 強制重建
mv catalog/quant.duckdb catalog/quant.duckdb.old
.venv/bin/qd-ingest build-catalog
# 應該寫出新檔

# 3. 驗證 view 數量
.venv/bin/python -c "
import duckdb
con = duckdb.connect('catalog/quant.duckdb', read_only=True)
print(con.execute(\"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='main'\").fetchone())
"
```

---

## Disk full

**症狀：**

```
OSError: [Errno 28] No space left on device
```

**原因：** WSL2 磁碟空間 / `/tmp` 滿了 / bronze 累積過多檔。

**解：**

```bash
# 看哪滿了
df -h
du -sh /home/kevin/gs-scraper/{QUANTDATA,RAW_SOURCES}/*

# 常見大檔
ls -lhS RAW_SOURCES/                # 大 zip
ls -lhS bronze/finmind/             # FinMind 2.5 GB sqlite
ls -lhS catalog/                    # *.bak 累積

# 清 bak（手動確認再刪）
rm catalog/quant.duckdb.bak_*

# 清舊的 daily_refresh log
find meta/audit/daily_refresh_*.log -mtime +30 -delete
```

---

## Git status 帶一堆 ?? ingest jsonl

**症狀：** `git status` 一直冒 `?? meta/audit/ingest_2026-05-XX.jsonl`。

**原因：** `.gitignore` 允許 `meta/audit/*.jsonl` 進版控（為了 manifest trail），但你不想 commit。

**解：**

```bash
# 該檔案要 commit:
git add meta/audit/ingest_<date>.jsonl

# 不想 commit、又要從 git status 消音:
# 加進 .git/info/exclude（只影響本機）
echo "meta/audit/ingest_*.jsonl" >> .git/info/exclude
```

---

## 還是不行？

1. 看 `meta/audit/daily_refresh_<date>.log` 找最後一行 ERROR
2. 看 `git log --oneline -10` 確認沒被同事誤改 schema
3. 把 `catalog/quant.duckdb` 還原到備份 `catalog/quant.duckdb.bak_*`
4. 跑 `bash scripts/daily_refresh.sh --dry-run` 看流線哪步壞
