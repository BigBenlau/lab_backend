# 香港 SAS 網站操作手冊

網站已上線。日常運作有三個容器：`gateway` 提供兩個網站和 HTTPS，`public-api` 唯讀，`admin-api` 負責登入與寫入。成員、論文、作者關聯及相簿存在 SQLite，照片、PDF 與固定頁面設定存在同一個 Docker 資料卷的 `public/`。正式官網接受 80 轉 443，管理後台使用 27182。

| 現在要做的事 | 從哪裡開始 |
|---|---|
| 已部署的 SAS 套用完整更新（可停機） | [更新程式、檢查入口、啟用每月備份](#update) |
| 確認備份可用，繼續對外服務 | [恢復演練](#verify) |
| 回到指定日期，或從異地備份重建 | [正式恢復與退回](#recover) |
| 定期換證、檢查端口 | [HTTPS 與續期](#site-cutover) |
| 新購主機、建立測試站 | [新主機](#new-host) 或 [HTTP 測試](#http-test) |

以下主機指令假設已進入 root shell（`sudo -i`）。首次設定以外，進入專案目錄都可使用 `cd -- "$(cat /var/lib/lab-tls/project)"`。這個固定路徑及父目錄只允許受信任部署者寫入，排程會用 root 執行其中的程式。

<a id="update"></a>
<a id="http-redirect-cutover"></a>

## 1. 現有正式站用 git pull 更新

適用於已運行 HTTPS、使用 SQLite10 的 SAS。依序執行，每段成功才繼續。這次更新 HTTP 轉址、管理頁、兩個 API、續期與備份工具。gateway 會打包 Git 已追蹤的官網版本。帳戶、網站內容及實體檔案沿用目前資料卷。先確認有可恢復的備份或 SAS 磁碟快照。

### 1.1 拉取已發布的 main

**開發電腦，只在發布這版時執行。** 完成審查和修正後，在專案根目錄的 `main` 分支暫存這次要發布的檔案。官網目錄及配套的本機 Retry 測試留待單獨提交。檢查暫存清單後才提交並推送。

```sh
git status --short --branch
git add -u -- . ':(exclude)public_frontend/**' ':(exclude)tests/public_frontend.test.cjs'
git add -- deploy/backup.sh deploy/check-site.sh deploy/compose.recovery.yml
git diff --cached --stat
git diff --cached --check
```

```sh
test "$(git branch --show-current)" = main &&
  git diff --cached --quiet -- public_frontend/ tests/public_frontend.test.cjs &&
  git commit -m "Update production deployment and monthly backups" &&
  git push origin main
```

**生產 SAS，每次更新從這裡開始。** 以平常維護這份 Git 工作目錄的帳戶執行。若有本機程式修改或分支分歧，先處理再更新。記下拉取前的提交號供退回時使用。

```sh
cd -- "$(sudo cat /var/lib/lab-tls/project)"
git status --short --branch
git rev-parse --short HEAD
```

```sh
(
  set -eu
  test "$(git branch --show-current)" = main
  test -z "$(git status --porcelain)"
  git pull --ff-only origin main
  test "$(git rev-parse HEAD)" = "$(git rev-parse FETCH_HEAD)"
  git ls-files --error-unmatch deploy/backup.sh deploy/check-site.sh deploy/compose.recovery.yml
  git log -1 --oneline
)
```

### 1.2 第 3 步（上） 在舊站運行時建置新版

完成 SAS 上的 `git pull --ff-only origin main` 後，執行 `sudo -i`，再貼下面的命令。第 3、4 步都用同一個 SAS root 終端。先用 `ps` 確認原站三個容器正在運行，再對照兩份端口輸出。80 可以空閒或由本專案 gateway 佔用；如果是其他容器或主機 Nginx，先釐清它服務哪個網站，再安排釋放端口。

```sh
cd -- "$(cat /var/lib/lab-tls/project)"
sh deploy/lab.sh ps
ss -ltnp 'sport = :80'
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

下面這組命令先核對線上管理 API 正在使用的資料卷，接著建置兩個映像。兩個 API 共用 `lab-api:demo`；`lab-gateway:demo` 包含兩個網站的程式與 Nginx 設定。`check-config` 用現有憑證啟動臨時設定檢查。成功後才安裝續期與備份命令。此時原站仍在服務。

```sh
(
  set -eu
  selected=lab-backend_lab_data
  if [ -f secrets/active-volume ]; then selected=$(cat secrets/active-volume); fi
  live=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$(sh deploy/lab.sh ps -q admin-api)")
  [ "$live" = "$selected" ] || { echo '請核對線上資料卷' >&2; exit 1; }
  sh deploy/lab.sh check-web
  sh deploy/lab.sh config --quiet
  sh deploy/lab.sh build public-api gateway
  sh deploy/lab.sh run --rm --no-deps -T gateway check-config
  docker image inspect goacme/lego:v5.6.0 >/dev/null 2>&1 || docker pull goacme/lego:v5.6.0
  install -o root -g root -m 0700 deploy/tls.sh /usr/local/sbin/lab-tls
  install -o root -g root -m 0700 deploy/backup.sh /usr/local/sbin/lab-backup
)
```

<a id="backup-setup"></a>

### 1.3 第 3 步（下） 備份目前資料並驗證能還原

執行下面的命令即可。首次備份會在登記的專案根目錄建立私人 `versions/`（UID/GID 10001，權限 0700）。手動執行與 root cron 都寫入同一位置，每次新增一個版本，免設備份密碼。

```sh
/usr/local/sbin/lab-backup run
```

預期輸出包含 `"ok": true` 和 `"version"`。複製本次的版本名稱，省略引號，執行下面兩行並在提示後貼上。工具在獨立暫存卷重建並核對同一版本的 `site.json`、`database.zip` 和 `public.zip`。

```sh
printf '備份 version：'
read -r VERSION && /usr/local/sbin/lab-backup verify "$VERSION"
```

演練成功時會輸出相同的 `version` 和 `"applied": false`，代表線上卷仍在原位。若 `run` 或 `verify` 失敗，保留原站繼續服務，先處理錯誤。尤其當 `run` 成功而 `verify` 報路徑無效時，檢查靜態檔名（例如含 `#` 的 PDF），等新備份可驗證後再繼續。

### 1.4 第 4 步 在維護時段切換容器

在 SAS 原專案目錄執行以下命令。替換容器會短暫中斷服務，管理員需要重新登入。`flock` 會等待正在執行的續期，`--no-build` 使用第 3 步建好的映像。現有站沿用資料卷，跳過 `init`、`seed` 和 `down -v`。正式命令省略 `http` 參數；明文測試另見[第 6 節](#http-test)。

```sh
(
  set -eu
  flock -w 360 /var/lib/lab-tls/lock sh deploy/lab.sh up -d --no-build --force-recreate --wait --wait-timeout 100 public-api admin-api gateway
  sh deploy/lab.sh ps
  sh deploy/lab.sh admin-info
  sh deploy/lab.sh exec -T gateway nginx -t -c /tmp/nginx.conf
  for service in public-api admin-api; do
    sh deploy/lab.sh exec -T "$service" python -c "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:8000/readyz', timeout=5).status)"
  done
  sh deploy/lab.sh maintenance check-files /data/public
  /usr/local/sbin/lab-tls renew
)
```

預期兩個 API 都是 healthy，兩個 `readyz` 各回 200，`admin-info` 顯示 `origin_scheme=https`。`ps` 應顯示 gateway 映射 `80->8080`、`443->8443`、`27182->27182`。Nginx 設定與缺檔檢查都要成功，`renew` 會核對目前線上憑證，達續期門檻才申請更新。若啟動失敗，留在這一步，用 `sh deploy/lab.sh logs --tail 80 gateway public-api admin-api` 查看原因。原先已啟用監測的主機，再執行 `sh deploy/lab.sh watch`。

### 1.5 第 5 步 逐一驗證四個 HTTP 入口，核對管理後台與排程

**5a．在 SAS 檢查 Nginx。** 這台 SAS 共用一個公網 IPv4 和 TCP 80。以下模擬訪客使用四個不同的 Host 造訪同一路徑，直接連本機，先排除公網 DNS 及防火牆的影響。

```sh
SAS_IPV4=$(cat /var/lib/lab-tls/ip)
for site_host in casino.ye.wang crab.ye.wang ye.wang "$SAS_IPV4"; do
  printf '%s -> ' "$site_host"
  curl -4 -sS --noproxy '*' --max-time 8 -o /dev/null \
    -H "Host: $site_host" -w '%{http_code} %{redirect_url}\n' \
    'http://127.0.0.1:80/api/home?check=1'
done
```

四行都應顯示 `301 https://原來的主機/api/home?check=1`。確認後，在阿里雲香港 SAS 的防火牆對公眾放行 TCP **80、443**，TCP **27182** 仍只允許管理員的出口 IP，測試用 **27181** 保持關閉。

**5b．在外部電腦檢查訪客的完整路徑。** 使用自己的終端，輸入剛才 SAS 上 `cat /var/lib/lab-tls/ip` 的值。這個檢查只使用公開端口，會使用正常 DNS 與系統憑證信任鏈。路徑特意帶查詢字串，以檢查轉址是否保留原頁面。

```sh
printf 'SAS 公網 IPv4：'
read -r SAS_IPV4
```

```sh
(
  : "${SAS_IPV4:?請先輸入 SAS 公網 IPv4}"
  probe_uri='/api/home?check=1'
  failed=0
  for site_host in casino.ye.wang crab.ye.wang ye.wang "$SAS_IPV4"; do
    start_url="http://$site_host:80$probe_uri"
    target_url="https://$site_host$probe_uri"
    last='未執行'
    if first=$(curl -4 -sS --noproxy '*' --max-time 12 -o /dev/null \
         -w '%{http_code} %{redirect_url}' "$start_url") &&
       [ "$first" = "301 $target_url" ] &&
       last=$(curl -4 -sSL --noproxy '*' --proto-redir '=https' --max-redirs 1 \
         --max-time 15 -o /dev/null -w '%{num_redirects} %{http_code} %{url_effective}' "$start_url") &&
       [ "$last" = "1 200 $target_url" ]; then
      printf '通過 %s -> %s -> HTTPS 200\n' "$site_host" "$first"
    else
      printf '失敗 %s，首跳 [%s]，跟隨結果 [%s]\n' "$site_host" "$first" "$last" >&2
      failed=1
    fi
  done
  exit "$failed"
)
```

預期四行「通過」。每行表示訪客使用該域名或 IP 從 80 進站，收到 301，跟隨一次跳轉後以有效憑證連上同一主機的 443，首頁 API 回 200。

| 觀察到的結果 | 下一個檢查 |
|---|---|
| SAS 本機回 200 或看到 Nginx 歡迎頁 | 查 `sh deploy/lab.sh admin-info` 和 `docker ps`，確認正式 gateway 取得主機 80。 |
| 本機四項成功，外網四項都連不上 80 | 查 SAS 防火牆的入站 TCP 80 規則、目標實例及來源範圍。 |
| 公網 IP 成功，只有某個域名失敗 | 查該名稱的 A／CNAME 是否解析到同一 IPv4。若首跳回 400，在 SAS 用 `openssl x509 -in secrets/public-tls/cert.pem -noout -ext subjectAltName` 核對憑證涵蓋該名稱。 |
| 首跳 301，跟隨後出現 TLS 錯誤或 API 回 503 | TLS 錯誤查 443 提供的憑證名稱、IP、期限及憑證鏈；503 查 public-api 的 `readyz` 和容器日誌。 |

**5c．在獲准的管理員網絡驗證後台。** 從已 `git pull` 的本機專案目錄執行以下命令，沿用 5b 的 IPv4。腳本另外檢查管理入口的明文導航跳回 HTTPS 27182、登入頁回 200，以及未登入的 session 查詢回 401。之後用瀏覽器核對成員、論文及一個 PDF，並用原帳密登入管理後台。

```sh
sh deploy/check-site.sh "$SAS_IPV4"
```

**5d．回到 SAS root 終端啟用排程。** 使用 `crontab -e` 將以下兩項各保留一行，保留其他無關工作。已有相同排程的主機可跳過編輯。

```sh
systemctl enable --now cron
crontab -e
```

```cron
0 */6 * * * if /usr/local/sbin/lab-tls renew >/dev/null 2>&1; then /usr/bin/logger -t lab-tls OK; else /usr/bin/logger -p daemon.err -t lab-tls FAILED; exit 1; fi
30 2 1 * * /usr/local/sbin/lab-backup run >/dev/null
```

```sh
crontab -l
systemctl is-active cron
date '+%F %T %Z'
journalctl -t lab-tls --since yesterday --no-pager
journalctl -t lab-backup --since '35 days ago' --no-pager
```

預期 cron 為 `active`。憑證每天 00:00、06:00、12:00、18:00 檢查，每月 1 日 02:30 按主機時區產生新備份。`crontab -l` 顯示已儲存的排程；要確認自動執行，請在下一個排程時間後查看同時段的日誌，月備份再核對 `./versions/` 出現的新版本。按[第 2 節](#backup)將同一版本的三個檔案複製到異地。

**若更新失敗**，先在 SAS 執行 `sh deploy/lab.sh logs --tail 80 gateway public-api admin-api`。若要退回程式，可在開發端依 1.1 記下的提交號找出需要撤回的變更，將修正提交推送到 `main`，再依 1.1、1.2、1.4 拉取和部署修正版。若要回復某次備份的內容，再使用[資料恢復流程](#recover)。

<a id="backup"></a>

## 2. 每月產生一份新的 site.json

每次備份讀取一次已提交的資料庫快照，產生獨立 UTC 版本，例如 `20261124T183000Z-0123456789ab/`。輸出目錄是 SAS 專案根目錄的 `versions/`。整組成功後才會出現該版本目錄。一般內容編輯和瀏覽可照常進行。大量更換實體圖片、PDF 或固定設定請避開備份時段，程式發現檔案在複製期間改變會報錯。

| 一個版本內的檔案 | 可恢復的內容 |
|---|---|
| `site.json` | 可閱讀的 UTF-8 整站內容。包含固定設定及三類業務資料，保留 ID、作者關聯、論文入庫順序與檔案路徑。含成員資料，請私下保存。 |
| `public.zip` | 實際照片、品牌圖、PDF、該版本的公開固定設定及逐檔校驗清單。外部網站連結只保留網址。 |
| `database.zip` | 免密碼的 SQLite10 邏輯備份，保存五張表，包含管理帳戶、密碼雜湊、編輯版本及審計。可讀取壓縮檔的人也可讀取其中資料，請限制存取。 |

瀏覽器讀取的 `/data/site.json` 只有品牌及固定頁面設定。**備份的整站 `site.json` 要放在私人版本目錄**。把它上傳到官網或複製到 `/data/public/site.json` 會暴露整站資料，也會破壞前端的固定設定格式。

在 SAS 專案根目錄執行 `sudo ls -lt ./versions/` 可查看已有版本。每次的完整目錄是 `./versions/<版本>/`，維護容器執行備份時將它的父目錄掛載為 `/versions`。根 `.gitignore` 排除整個 `/versions/`，兩個映像的建置白名單也排除它。只有備份／恢復工具掛載這些檔案。重新取得專案或清理工作目錄時要保留這個資料夾；`git clean -fdx` 會刪除被忽略的備份。

**已上線，只更新備份工具。** 在包含專案內備份路徑的版本發布到 GitHub 後，先以原部署帳戶在專案目錄執行 `git pull --ff-only origin main`；再於 SAS 的 root 終端執行下面的命令。維護容器會使用新建置的共用 Python 映像。本次可以保持三個網站服務容器運行。

```sh
cd -- "$(cat /var/lib/lab-tls/project)"
sh deploy/lab.sh build public-api &&
  install -o root -g root -m 0700 deploy/backup.sh /usr/local/sbin/lab-backup
```

首次啟用或手動產生一份新備份，請執行[備份設定與演練](#backup-setup)的命令。已有每月 1 日的 root cron 時可直接沿用。舊版本目錄若含 `database.enc`，驗證及恢復時仍需要原來的 `/var/lib/lab-tls/backup-password`；請與舊備份一併妥善保存。

**已有舊路徑備份的 SAS**。如需在新版工具管理先前的版本，可在安裝新版 `lab-backup` 後於 root 終端執行以下一次性複製。每個版本先複製到隱藏暫存目錄，完成後才改名並執行驗證。若目的地已有同名版本則跳過複製，只驗證現有版本。請保留來源直到已核對目的地及異地副本。

```sh
(
  set -eu
  cd -- "$(cat /var/lib/lab-tls/project)"
  [ ! -L versions ] || { echo 'versions 須為實體目錄'; exit 1; }
  install -d -o 10001 -g 10001 -m 0700 -- versions
  for src in /var/backups/lab/versions/*; do
    [ -d "$src" ] || continue
    [ ! -L "$src" ] || exit 1
    version=${src##*/}
    if [ ! -e "versions/$version" ]; then
      stage=$(mktemp -d versions/.copy.XXXXXXXX)
      cp -a -- "$src" "$stage/"
      mv -nT -- "$stage/$version" "versions/$version"
      rmdir -- "$stage"
    fi
    /usr/local/sbin/lab-backup verify "$version"
  done
)
```

**啟用排程**。執行 `systemctl enable --now cron` 與 `crontab -e`。在 root 的 crontab 中，將既有的 `lab-backup run` 那一行替換為下列設定；首次啟用時才新增。每月 **1 日凌晨 02:30**（SAS 主機時區）執行一次。若只靠排程備份，還原時可能失去上次成功備份後最多約一個月的編輯。

```cron
30 2 1 * * /usr/local/sbin/lab-backup run >/dev/null
```

```sh
crontab -l
date '+%F %T %Z'
journalctl -t lab-backup --since '35 days ago' --no-pager
du -sh ./versions
df -h ./versions
```

已啟用常駐監測的主機，同步新版專案後執行 `sh deploy/lab.sh build public-api` 和 `sh deploy/lab.sh watch`，讓監測容器載入新的 32 天逾期門檻。

每次保留新版本，備份不會自動輪替。按磁碟容量訂定保留期限，只清理已核對異地副本且已超出保留期的版本。**同機備份無法應付整台 SAS 或磁碟遺失。** 將每個已完成的版本目錄整組複製到另一台受控主機或禁止公開讀取、開啟加密及版本保護的物件儲存。有可用的受控 SSH 備份主機時，可在 SAS 端以如下命令傳送（替換帳戶及目的地，SSH 可只開在接收端）。定期從異地取回一組實際演練。

```sh
rsync -a --exclude='.*' ./versions/ backup-user@backup-host:/private/lab/versions/
```

<a id="verify"></a>
<a id="recover"></a>

## 3. 恢復演練、正式切換與退回

**恢復流程**是選定一份備份，重建出當時的 SQLite 和實體檔案，核對可讀後再決定是否讓網站使用它。只想知道「備份真的能用嗎」時，做到第 1 步即可。

1. **演練**。在 root shell 填入實際版本。工具讀取資料備份、重建五張表、檢查版本與內容、照片與 PDF 的 SHA256、作者及檔案關聯，完成後移除本次演練卷。現有三個容器可繼續服務。失敗時先核對是否取齊同一版本的三個檔，以及目錄權限和磁碟空間。

```sh
VERSION='請填寫版本目錄名稱'
/usr/local/sbin/lab-backup verify "$VERSION"
```

2. **重建可上線的卷**。需要還原時才執行。若曾從異地取回，先將三個檔放回 `./versions/$VERSION/`，版本目錄限 0700、文件限 0600，擁有者設為 10001:10001，讓維護容器讀取。工具再次完整核對，成功後輸出 `volume=lab-restore-…`，此時網站仍讀原卷。

```sh
/usr/local/sbin/lab-backup rebuild "$VERSION"
```

3. **切換**。確認接受回到該時間點。先停止人工編輯；目前資料若仍可讀，先再跑一次 `lab-backup run`。切換會短暫停止服務並結束現有登入。把下一行替換為剛才的完整卷名。

```sh
NEW_VOLUME='lab-restore-請填寫完整名稱'
/usr/local/sbin/lab-backup activate "$NEW_VOLUME"
cd -- "$(cat /var/lib/lab-tls/project)"
sh deploy/lab.sh ps
sh deploy/lab.sh maintenance check-files /data/public
```

切換工具在舊站運行時先檢查新卷，取得續期鎖才替換三個容器。兩個 API 就緒及 Nginx 檢查失敗會嘗試切回原卷。以前啟用了 `watch` 的站點會恢復監測。用外部檢查腳本及瀏覽器核對登入、照片、成員和論文後才開放編輯。管理員帳密回到備份當時狀態。

4. **手動退回**。若新卷內容不符合預期，原卷保留在主機上。切換後新增的編輯分屬新卷，退回前先備份。啟用卷名稱存在專案私有的 `secrets/active-volume`，上次使用的卷記在 `secrets/previous-volume`。恢復卷透過 Compose 的 external 設定掛載，卷遺失時會報錯。[Docker 資料卷說明](https://docs.docker.com/reference/compose-file/volumes/#external) 如果主機在切換中途重啟，先核對兩者及實際掛載；確認指標已指向正確卷時，直接用 `lab.sh up -d --force-recreate public-api admin-api gateway` 重新掛載。

```sh
cd -- "$(cat /var/lib/lab-tls/project)"
PREVIOUS=$(cat secrets/previous-volume)
/usr/local/sbin/lab-backup activate "$PREVIOUS"
```

**原主機已遺失**。新 SAS 先按下節安裝 Docker、取回相容程式版本、建映像和配置憑證；取回完整版本並安裝兩個主機工具。只有舊加密版本才需取回原備份密碼。在新主機做第 1、2 步。首次啟動前，將已驗證卷名寫進 `secrets/active-volume`（root、0600），再 `sh deploy/lab.sh up -d --wait public-api admin-api gateway`。此時沒有舊站可退回，跳過首次 `init` 和 `seed`。最後重設兩條 root cron、核對外部連線，並作一次新備份。TLS／ACME 材料（`/var/lib/lab-tls/`、兩個 TLS 目錄）與程式發布版本另行私密備份；若新機沿用同一 IP 且兩處已安裝有效 PEM，可在資料卷備妥後啟動 gateway，再執行 `lab-tls renew`。若公網 IP 已變，先更新 DNS 及私有 `ip` 設定；將舊 PEM 留在私人備份，讓新機的兩個 TLS 安裝目錄為空，於 gateway 啟動前執行 `lab-tls issue`。

### 只有內容 JSON，或只剩舊版配對備份

完整備份優先使用上述步驟。若缺少資料庫壓縮檔，或遺失舊加密版本的備份密碼，仍可用整站 `site.json` 加上對應的 `assets/`（從可信的同版靜態副本取得）重建業務資料。這條路徑需要重新建立具名管理員，審計亦會從頭開始。只有 JSON 時，可以取回文字、ID 與關聯，缺少已引用的照片或 PDF 會阻止啟用。工具不接受任意 JSON 上傳。

以下兩種情況都在全新的資料卷工作。先將輸入放入只准管理者存取的主機目錄，該目錄及檔案須允許 UID 10001 讀取。以下在同一個 root shell 執行，`INPUT` 換成實際絕對路徑。

```sh
cd -- "$(cat /var/lib/lab-tls/project)"
INPUT="$PWD/versions/.recovery-input"
TARGET=lab-restore-$(cat /proc/sys/kernel/random/uuid)
docker volume create "$TARGET"
recover_tool() {
    docker run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
      --user 10001:10001 -e LAB_STORAGE=/data --mount "type=volume,src=$TARGET,dst=/data" \
      --mount "type=bind,src=$INPUT,dst=/input,readonly" lab-api:demo python scripts/maintenance.py "$@"
}
```

- **整站 JSON**。準備 `$INPUT/site.json` 和 `$INPUT/assets/`。先預覽再套用，在新卷建立管理員後，才可按上述第 3 步切換。

```sh
recover_tool rebuild-site /input/site.json /input
recover_tool rebuild-site /input/site.json /input --apply
docker run --rm -it --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user 10001:10001 -e LAB_STORAGE=/data -v "$TARGET:/data" lab-api:demo python -m lab_backend.cli create-admin
```

- **舊版 LABSQL10 配對**。把同次備份的加密資料庫和靜態 ZIP 分別命名為 `database.enc`、`public.zip`。用當時的密碼檔作 stdin；兩步都會重查配對。成功後按第 3 步切換。

```sh
recover_tool rebuild /input/public.zip /input/database.enc < /var/lib/lab-tls/backup-password
recover_tool rebuild /input/public.zip /input/database.enc --apply < /var/lib/lab-tls/backup-password
```

<a id="site-cutover"></a>
<a id="tls-cron"></a>
<a id="tls-install"></a>
<a id="site-check"></a>

## 4. HTTPS 與自動續期

帳號 A 維護 DNS，帳號 B 在 SAS 部署。A 將根域名 `ye.wang` 的 A 記錄設為 SAS 公網 IPv4；`casino`、`crab` 可各用 CNAME 指向 `ye.wang`，或各用 A 記錄指向同一 IPv4。這套設定使用 IPv4 直連。B 的 lego 容器經公網 443 完成 Let’s Encrypt 驗證，無需使用 A 的 DNS 金鑰。憑證包括三個域名和一個 IP，適用於 443 和 27182。

已上線的主機在更新 `deploy/tls.sh` 後重新安裝至 `/usr/local/sbin/lab-tls`。手動執行 `lab-tls renew` 可立即核對，腳本會在約剩 72 小時時續簽。gateway 在 443 按握手協定將驗證連線分流給臨時 lego；換證成功後對 Nginx 做平滑 reload 並核對兩個端口的四種識別。日常續期可繼續服務，無須替換 gateway。

在 `crontab -e` 中保留一條續期工作，由 cron 依續期程式的退出碼記錄 OK 或 FAILED。已使用下列設定時可直接沿用。

```cron
0 */6 * * * if /usr/local/sbin/lab-tls renew >/dev/null 2>&1; then /usr/bin/logger -t lab-tls OK; else /usr/bin/logger -p daemon.err -t lab-tls FAILED; exit 1; fi
```

```sh
systemctl is-active cron
journalctl -t lab-tls --since yesterday --no-pager
openssl x509 -in secrets/public-tls/cert.pem -noout -enddate -ext subjectAltName
```

若記錄 `FAILED`，在私人終端執行 `/usr/local/sbin/lab-tls renew` 查看當次失敗原因。請勿公開原始 ACME 輸出、私鑰或密碼。

<a id="new-host"></a>

## 5. 全新 Ubuntu 主機

1. 帳號 B 購買中國（香港）SAS，選 Ubuntu 24.04 LTS x86-64 系統鏡像。確認維護控制台／救援登入可用。按 Docker 官方 Ubuntu 說明安裝 Engine 和 Compose 外掛，切換恢復卷和 HTTP 測試均需 Compose 2.24.4 或以上。Python、SQLite、Nginx 都隨映像安裝。[Docker Ubuntu 安裝](https://docs.docker.com/engine/install/ubuntu/)
2. 在這台實例的雲端防火牆放行公網 TCP 443。80 用於轉址，確認入口後放行。27182 只允許管理員的出口 IP；SSH 如有需要也限維護者來源，27181 只在隔離的 HTTP 測試使用。不要發布兩個 API 的端口。A 完成 DNS 後，從外部核對三個名稱指向正確 IP。
3. 在受信任的固定目錄取得已審閱版本，執行 `sh deploy/lab.sh check-web`、`sh deploy/lab.sh config --quiet` 和 `sh deploy/lab.sh build --pull public-api gateway`。Docker 權限等同主機管理權。
4. 首次簽發前確認 443 可由 lego 使用，讀取 Let’s Encrypt 訂戶協議（腳本傳入 `--accept-tos`）。以下在專案根目錄做一次，替換 IP 和信箱。[訂戶協議](https://letsencrypt.org/repository/)

```sh
SAS_IPV4='請填寫實際公網IPv4'
ACME_EMAIL='請填寫維護者信箱'
install -d -o root -g root -m 0700 /var/lib/lab-tls
umask 077
printf '%s\n' "$PWD" > /var/lib/lab-tls/project
printf '%s\n' "$SAS_IPV4" > /var/lib/lab-tls/ip
printf '%s\n' "$ACME_EMAIL" > /var/lib/lab-tls/email
install -m 0700 deploy/tls.sh /usr/local/sbin/lab-tls
install -m 0700 deploy/backup.sh /usr/local/sbin/lab-backup
docker pull goacme/lego:v5.6.0
/usr/local/sbin/lab-tls issue
```

5. **只有全新且沒有要還原的資料時**才執行下面的初始化。首次來源以私人方式放到 `public_frontend/dist/data/lab.json` 與 `public_frontend/dist/assets/`，UID 10001 要能進入掛載的 `dist` 和子目錄、讀取檔案。預覽確認筆數與檔案數後才套用。預設只複製已引用的公開檔案；額外核准的檔案可在兩次 seed 指令都加 `--include-asset assets/檔名.pdf`。

```sh
sh deploy/lab.sh init
sh deploy/lab.sh seed
sh deploy/lab.sh seed --apply
sh deploy/lab.sh cli import-csv /data/private/initial/content
sh deploy/lab.sh cli import-csv /data/private/initial/content --apply
sh deploy/lab.sh maintenance install-public /data/private/initial/public
sh deploy/lab.sh maintenance install-public /data/private/initial/public --apply
sh deploy/lab.sh maintenance check-files /data/public
sh deploy/lab.sh up -d --wait public-api admin-api gateway
```

`init` 會要求建立第一個具名管理員。若資料庫已成功建立而建帳中斷，接著用 `sh deploy/lab.sh cli create-admin`。上線後執行外部核對、開啟兩條排程、備份及演練。

<a id="http-test"></a>

## 6. 受控 HTTP 測試

在另一台測試主機上執行。測試和正式在同一主機會使用同一 Compose 專案及預設資料卷。測試機只允許測試者 IP 存取 80 和 27181，使用虛構內容與獨立管理密碼。HTTP 會以明文傳輸 Cookie 和帳密。

```sh
sh deploy/lab.sh http check-web
sh deploy/lab.sh http config --quiet
sh deploy/lab.sh http build public-api gateway
sh deploy/lab.sh http init
sh deploy/lab.sh http seed
sh deploy/lab.sh http seed --apply
sh deploy/lab.sh http cli import-csv /data/private/initial/content
sh deploy/lab.sh http cli import-csv /data/private/initial/content --apply
sh deploy/lab.sh http maintenance install-public /data/private/initial/public
sh deploy/lab.sh http maintenance install-public /data/private/initial/public --apply
sh deploy/lab.sh http maintenance check-files /data/public
sh deploy/lab.sh http up -d --wait public-api admin-api gateway
sh deploy/lab.sh http admin-info
```

用 `http://測試IP/` 和 `http://測試IP:27181/` 訪問。HTTP 測試直接顯示內容，毋須憑證。結束時 `sh deploy/lab.sh http down` 保留資料卷。從同一測試實例轉正式時先關測試入口，按第 4、5 節取得正式憑證、調整防火牆，再以正式命令啟動。勿將測試帳密或虛構資料用於正式站。

## 注意事項與排錯

<a id="日常內容與檔案維護"></a>

- **日常編輯**在管理後台進行；作者關聯只改論文中的成員 ID。批次 CSV 在停止 `admin-api` 後以唯讀目錄掛入 tools，先執行 `python -m lab_backend.cli import-csv /imports` 預覽，再加 `--apply`。只新增、既有 ID 跳過。完成後 `sh deploy/lab.sh maintenance check-files /data/public` 並啟動管理服務。
- **更新照片或 PDF**。將受控來源目錄唯讀掛入 tools，以新檔名複製到 `/data/public/assets/`，再在管理頁更新網址。例如 `/assets/papers/example.pdf` 對應 `/data/public/assets/papers/example.pdf`。固定頁面文字存於 `/data/public/site.json`；修改前先作備份，修改後執行缺檔檢查。若替換整個 `public/` 目錄，需要重新建立 gateway 以掛載新目錄。
- **忘記管理密碼**。停止管理服務後，執行 `sh deploy/lab.sh cli recover-admin` 為既有具名帳戶設新密碼，再執行 `sh deploy/lab.sh up -d admin-api`。日常新增帳戶可從已登入的管理頁操作。
- **健康與告警**用 `sh deploy/lab.sh maintenance monitor` 作一次檢查，或用 `sh deploy/lab.sh watch` 啟用第四個監測容器。healthz 看程序、readyz 看資料。監測會報服務不可用、持續無法讀取、資料磁碟不足、備份失敗、距上次成功備份超過 32 天或配對不符。異地副本、新備份磁碟空間及對外通知由維護者另行監測。
- **端口衝突**先執行 `ss -ltnp`、`docker ps` 確認目前擁有者。HTTP 和 HTTPS 模式的管理端口不同。明文 GET／HEAD 到正式 27182 會跳回同端口 HTTPS；管理寫入須直接用 HTTPS。啟用 80 的這次入口更新需要重建 gateway。
- **503 或 SQLite journal**先檢查磁碟空間、`sh deploy/lab.sh logs --tail=50 admin-api public-api`、資料卷選擇及權限，讓管理服務在原路徑恢復交易。保留 journal 和卷，不以刪檔或 `down -v` 排錯。
- **備份還原權限**：從異地取回後，主機上層私有目錄可由 root 獨佔，實際掛進容器的版本目錄須讓 UID 10001 可讀及進入。機密、業務 JSON、圖片、匯出與備份只保存在私人位置。
