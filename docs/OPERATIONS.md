# 安裝、部署與維護

契約7.0.0、SQLite9／LABSQL9。本文件提供操作者指令；是否實際執行見[檢查紀錄](IMPLEMENTATION_STATUS.md)。資料規則見[DATABASE](DATABASE.md)，部署與安全邊界見[架構](../ARCHITECTURE.md)。

本版移除people的slug／sourceUrl／affiliation及publications.slug；CSV模板已同步。englishName仍可留空，首頁合照仍設metadata.purpose=group_photo。SQLite8及更早資料庫／舊備份不能直接交給本版；沒有自動遷移、覆寫或刪除執行資料。

若已有舊庫，保留原檔，先用對應舊版工具匯出內容CSV；在副本依新版模板移除上述欄位，再預覽並匯入另一個新初始化目錄。更早版本的preferredName需改名englishName，合照需指定purpose；不要直接修改舊庫user_version。帳戶與審計不在普通內容CSV匯入範圍。

## 安裝與空庫初始化

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
export LAB_STORAGE=/absolute/path/to/storage
.venv/bin/python -m lab_backend.cli init
.venv/bin/python -m lab_backend.cli create-admin
```

init建立五張空表，拒絕覆寫非空目錄；不讀lab.json，也無--seed或自動遷移。create-admin在私人終端提示帳戶及15–128字元密碼；沒有預設帳密，不以命令列／日誌保存密碼。後續可用管理頁新增帳戶。Python不自動載入.env，所有主機CLI／API的LAB_STORAGE須一致。

## 本機啟動

```sh
export LAB_DEV=true
export LAB_ADMIN_ORIGIN=http://127.0.0.1:8001
.venv/bin/python -m uvicorn lab_backend.application:admin_app --factory --host 127.0.0.1 --port 8001 --workers 1 --no-proxy-headers --no-access-log
# 另一終端，使用相同LAB_STORAGE
.venv/bin/python -m uvicorn lab_backend.application:public_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --no-access-log
```

管理頁在http://127.0.0.1:8001/，原始碼admin_frontend/admin，勿以file://打開。總覽、成員、論文、資產、帳戶、審計、改密碼七個入口覆蓋管理API；儲存時鎖定輸入，衝突時保留原值。不確定寫入是否成功時先重讀結果。

## Docker部署設定

只使用單台Linux本機POSIX磁碟、單一admin worker，不用NFS／SMB。依[.env.example](../.env.example)設定絕對儲存路徑、管理hostname、精確HTTPS Origin及TLS cert/key；保留LAB_DEV=false。先初始化及建立帳戶，目錄須供UID/GID 10001存取，目錄0700、檔案0600，不用chmod 777。

掛整個content／private目錄，讓SQLite建立journal。兩API均無主機端口；gateway預設127.0.0.1:9443。API映像只COPY程式及依賴，lab.json、資料庫、機密、匯入及備份排除建置context。

以下由操作者另行執行，並非本輪已部署：

```sh
docker compose --env-file .env -f deploy/compose.yml config
docker compose --env-file .env -f deploy/compose.yml build
docker compose --env-file .env -f deploy/compose.yml up -d
```

公開前端獨立部署，僅加入lab-content、不掛DB或秘密。使用[網路範例](../deploy/frontend-network.example.yml)及[代理白名單](../deploy/frontend-proxy.example.conf)，只代理四個公開GET，不轉Cookie／管理接口或快取回應。PDF在官網assets提供application/pdf。子模組未初始化時可用git submodule update --init --recursive；有修改先保存，不reset覆寫。

## 離線CSV匯入

**離線寫入先停止管理服務。** 不刪admin.lock或SQLite journal；異常中斷先由管理程序在原環境／原掛載路徑恢復，再正常停服維護。

```sh
.venv/bin/python -m lab_backend.cli csv-templates /private/path/batch
.venv/bin/python scripts/maintenance.py import-csv /private/path/batch
.venv/bin/python scripts/maintenance.py import-csv /private/path/batch --apply
```

目錄只含assets.csv、people.csv、publications.csv其中一至三種；保留模板完整欄位與順序，不放帳戶／審計或其他檔案。等價入口為`python -m lab_backend.cli import-csv`。預覽不改庫，--apply重新驗證後整批新增；既有ID跳過，同批重複ID／欄位／關聯／容量錯誤整批拒絕，成功立即公開。

UTF-8、csv.QUOTE_NOTNULL：未加引號的空值為null，`""`為空字串，物件／陣列為JSON儲存格並按CSV轉義；勿讓表格編輯器自動轉換ID／JSON。普通匯入不含歷史或衍生欄位。論文按檔內列序新增，最後新增列顯示在最前。

## 離線缺檔檢查

```sh
.venv/bin/python scripts/maintenance.py check-files /absolute/path/public-site/dist
```

讀SQLite中的資產、照片及PDF引用，核對assets內普通檔案，拒絕符號連結，不發HTTP請求。JSON回checked／missing／externalUnchecked，有缺檔退出1；外站網址只列未驗證。此結果只代表檢查當下。

## 配對備份與恢復

停管理服務並暫停靜態檔更新；備份目錄須已存在，位於網站公開目錄之外。使用相同版本代號，先DB、再靜態檔：

```sh
.venv/bin/python -m lab_backend.cli backup /private/backups/release-001.enc --version release-001
.venv/bin/python scripts/maintenance.py backup-static /absolute/path/public-site/dist /private/backups/release-001-static.zip --version release-001 --database-backup /private/backups/release-001.enc
```

DB為五表完整已提交快照，以終端輸入的獨立密碼加密；ZIP只含assets與manifest.json。manifest記版本、DB檔SHA256、內容雜湊及逐檔路徑／大小／SHA256。版本不符、內容改變或缺檔拒絕；靜態檔每檔≤256MiB、合計≤2GiB、最多10000檔。外部URL不下載，公開靜態ZIP不另加密。

兩份備份一起異地保存，密碼分開保管；需要靜態檔加密時使用受控加密儲存／通道。private/backup-status.json僅記工作狀態與最近成功時間，不是備份本身。

```sh
# 先預覽：解密／檢查，不寫資料
.venv/bin/python -m lab_backend.cli restore /private/backups/release-001.enc
.venv/bin/python scripts/maintenance.py restore-static /private/backups/release-001-static.zip --database-backup /private/backups/release-001.enc --target /private/restored-site
# 明確套用；管理服務須停止，靜態目標目錄必須不存在
.venv/bin/python -m lab_backend.cli restore /private/backups/release-001.enc --apply
.venv/bin/python scripts/maintenance.py restore-static /private/backups/release-001-static.zip --database-backup /private/backups/release-001.enc --target /private/restored-site --apply
.venv/bin/python scripts/maintenance.py check-files /private/restored-site
```

DB恢復以交易替換兩庫並提升帳戶授權版本，保留論文順序；不接受舊格式。靜態恢復只建立新目錄，核對後由操作者切換對應assets與前端版本，不直接覆寫現場網站。

```sh
.venv/bin/python -m lab_backend.cli recover-admin
.venv/bin/python -m lab_backend.cli export-audit /private/backups/audit.csv
.venv/bin/python -m lab_backend.cli prune-audit /private/backups/archive.csv --keep-days 90
```

recover-admin及prune-audit也需停管理服務；後者先完整匯出再清理。匯出CSV中的不可信文字不得作試算表公式執行。

## 監測與告警

/healthz只看程序存活；/readyz讀內容，管理端另查users，不可用回503；合法空庫仍就緒。兩者不經官網代理，Compose健康檢查用healthz。

```sh
.venv/bin/python scripts/maintenance.py monitor --env-file /absolute/path/.env --compose-file deploy/compose.yml --min-free-mib 256 --max-backup-hours 48 --threshold 3
```

此指令供已部署主機使用，透過docker compose exec探測既有容器loopback，不啟動容器或開端口。執行帳戶需讀content、讀寫private監測狀態並操作既有容器。

| 條件 | 告警 |
|---|---|
| healthz非200 | PROCESS_UNAVAILABLE，立即 |
| readyz連續3次非200，含503／連線失敗 | CONTENT_UNAVAILABLE，成功後歸零 |
| 兩庫目錄較小剩餘空間低於256MiB | LOW_DISK |
| DB／靜態備份失敗、缺失或超過48小時 | BACKUP_FAILED_OR_MISSING／BACKUP_OVERDUE |
| DB與靜態備份版本或指紋不配對 | BACKUP_VERSION_MISMATCH |
| 狀態損壞 | MONITOR_STATE_INVALID／BACKUP_STATUS_INVALID |

輸出JSON、stderr的ALERT與退出碼（正常0、告警1）；計數存private/monitor-state.json。[service](../deploy/lab-monitor.service.example)／[timer](../deploy/lab-monitor.timer.example)範例每分鐘執行，修改路徑與帳戶後另行安裝。尚無外部通知渠道；主機／排程停止須外部監控發現，異地副本仍須另行核對，monitor不自動備份。

## 契約同步

```sh
.venv/bin/python scripts/sync_contracts.py
.venv/bin/python scripts/sync_contracts.py --check
```

從路由產生兩份OpenAPI，再生成子模組contracts/public-api.d.ts；--check只核對、不寫入。不讀業務資料或啟動API。生成型別與前端變更需在子模組保存，再更新父專案gitlink；不要手改生成檔。
