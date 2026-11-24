# 阿里雲香港 SAS 部署與維護

**正式官網使用 443。** 訪客以 HTTP 打開 `casino.ye.wang`、`crab.ye.wang`、`ye.wang` 或 SAS 公網 IPv4 時，80 會將瀏覽器轉到同一網址的 HTTPS。這四個地址共用一張憑證。管理後台使用 `:27182`，由 SAS 防火牆限制管理員來源。

帳號 A 管理 DNS，帳號 B 管理香港 SAS、Docker 及憑證。已有 HTTPS 且憑證涵蓋上述四個地址，直接按[已上線網站開通 80](#http-redirect-cutover)操作。仍在使用舊憑證的網站先看[既有 HTTPS 換證](#site-cutover)。首次部署從下方步驟 1 開始。

## 首次部署

### 1. 選購香港區 Ubuntu 主機

在阿里雲「輕量應用伺服器」控制台建立實例。地域選 **中國（香港）**，鏡像選 **系統鏡像 → Ubuntu 24.04 LTS，64 位元 x86**（以購買頁可選版本為準）。使用乾淨系統即可，Python、Nginx 及 SQLite 隨本專案在 Docker 內執行。[阿里雲系統鏡像建立流程](https://help.aliyun.com/zh/simple-application-server/getting-started/quickly-create-a-server-using-a-system-image)

初次選型可由 **2 vCPU、4 GiB 記憶體** 起步。這是按約 30 位成員、300 篇論文及三個常駐容器估算，未做負載驗收。磁碟須容納映像、照片、PDF、SQLite 及備份暫存，方案的對外流量則依檔案下載量選擇。確認續費設定，在實例詳情記下公網 IPv4。透過控制台設定首次登入憑證，若用 SSH 可綁定維護者的公鑰。連線使用該鏡像指定的登入帳戶；進入主機後需能執行 sudo。[阿里雲 Linux 連線方式](https://help.aliyun.com/zh/simple-application-server/user-guide/connect-to-linux-server-remotely)

### 2. 先設 SAS 防火牆和維護入口

在**該實例 → 防火牆**調整入站規則，檢查並移除與下表衝突的預設廣泛放行規則。收窄 SSH 時可先新增允許的來源，測試第二個連線並確認救援入口後再撤銷全網規則；只新增窄範圍規則仍會留下原有的全網放行。阿里雲 Linux 實例的預設 TCP 規則包括 22、80、443。[阿里雲 SAS 防火牆](https://help.aliyun.com/zh/simple-application-server/user-guide/manage-the-firewall-of-a-server)

| TCP 端口 | 允許來源 | 用途 |
|---|---|---|
| 80 | 驗證本機轉址後，開放所有 IPv4，`0.0.0.0/0` | 官網 HTTP 導航轉至同一主機的 HTTPS 443 |
| 443 | 所有 IPv4，`0.0.0.0/0` | 官網 HTTPS |
| 27182 | 管理員固定公網出口，例如 `203.0.113.24/32`，或已核准的校內出口 CIDR | 管理頁及登入 API |
| 22 | 如需 SSH，僅限維護者公網出口 | 首次設定與維護 |
| 27181 及其他未使用的 TCP 端口 | 移除入站放行規則 | 27181 只供受控 HTTP 測試環境使用 |

管理入口的來源限制完全由 SAS 防火牆執行。Docker 內部的 8000、8080、8443 無需雲端放行。先用允許的來源連入主機，再確認能用自己的帳戶實際登入控制台的 **VNC 救援連接**，才關閉不再使用的 SSH 規則。一般 SSH 或 Workbench 與救援登入的條件不同。[阿里雲救援連接](https://help.aliyun.com/zh/simple-application-server/user-guide/connect-to-a-server-by-using-the-rescue-feature)

<a id="site-dns"></a>

### 3. A 核對三個域名的 DNS，B 確認公網 IP

B 提供 SAS 的實際公網 IPv4。A 在阿里雲「雲解析 DNS」選 `ye.wang`，在「解析設定」新增或更新下表記錄。解析線路與 TTL 可用預設值。[阿里雲新增解析記錄](https://help.aliyun.com/zh/dns/pubz-add-parsing-record)

| 主機記錄 | 記錄類型 | 記錄值 |
|---|---|---|
| `@`（ye.wang） | `A` | SAS 的實際公網 IPv4 |
| `casino` | `A` | 同一個公網 IPv4 |
| `crab` | `CNAME` | `casino.ye.wang` |

沿用已指向此 SAS 的記錄即可。按上表配置時，公網 IP 若改變，需同步更新兩筆 A 記錄及主機上的 `/var/lib/lab-tls/ip`，然後執行 `lab-tls renew`。若 `crab` 已經服務另一個網站，先確認切換時間、記下原記錄及 TTL，再替換同名的衝突記錄。設定 CNAME 後，瀏覽器的網址仍為 `crab.ye.wang`，所以 Nginx 送出的憑證也須涵蓋這個名稱。[TLS 服務名稱驗證](https://www.rfc-editor.org/rfc/rfc9525.html#section-6.1)

從外部電腦查詢。三個名稱的 A 查詢都應最終得到同一個 SAS IPv4，AAAA 查詢應無 IPv6 位址。CNAME 別名可能額外顯示目標名稱。若已有 CAA 限制，確認允許 Let’s Encrypt 簽發。[Let’s Encrypt CAA 說明](https://letsencrypt.org/docs/caa/)

```sh
for host in casino.ye.wang crab.ye.wang ye.wang; do
  dig +short A "$host"
  dig +short AAAA "$host"
done
```

B 準備可收信的 ACME 聯絡信箱。由 lego 向 Let’s Encrypt 申請，驗證方式為 TLS-ALPN-01。三個域名和裸 IP 均須可由公網直達此 SAS 的 TCP 443。DNS 就緒後，簽發與續期都可由 B 完成，無需帳號 A 的 DNS 金鑰。[Let’s Encrypt 驗證方式](https://letsencrypt.org/docs/challenge-types/#tls-alpn-01)

### 4. 安裝 Docker 並取得部署版本

在 SAS 的 Ubuntu 執行 `sudo docker version` 與 `sudo docker compose version`。已有相容版本可直接取程式。全新 Ubuntu 24.04 amd64 主機可用以下官方套件庫安裝 Engine、Buildx 和 Compose 外掛。需使用支援 `volume.subpath` 的 Engine／Compose，HTTP 覆蓋設定另需支援 `!override`。其他 Ubuntu 版本依官方指引選用其發行版代號。[Docker 官方 Ubuntu 安裝步驟](https://docs.docker.com/engine/install/ubuntu/)

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<'REPO'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
REPO
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker version
sudo docker compose version
```

用部署帳戶下載已審核的 tag 或 commit。以下 `OWNER/REPOSITORY` 及 `REVIEWED_TAG_OR_COMMIT` 需替換。下文部署命令假設操作者已有 Docker 權限，若一般帳戶需要 sudo，請以 `sudo sh deploy/lab.sh …` 執行涉及 Docker 的命令。

```sh
git clone https://github.com/OWNER/REPOSITORY.git lab-website
cd lab-website
git checkout --detach REVIEWED_TAG_OR_COMMIT
sh deploy/lab.sh check-web
sh deploy/lab.sh config --quiet
sh deploy/lab.sh build --pull public-api gateway
```

<a id="tls-install"></a>

### 5. 將同一張憑證安裝到兩個入口，再初始化內容

在 B 的 SAS、已審核的專案根目錄，先確認 80、443 和 27182 的使用者並拉取指定映像。若其他服務佔用所需端口，先與其維護者協調。腳本只管理本專案的 gateway。

```sh
sudo ss -ltnp '( sport = :80 or sport = :443 or sport = :27182 )'
sudo docker ps --format 'table {{.Names}}\t{{.Ports}}'
sudo docker pull goacme/lego:v5.6.0
```

以下是**首次部署**步驟。B 在 SAS 專案根目錄執行，替換聯絡信箱。腳本會傳入 `--accept-tos`，請先閱讀 Let’s Encrypt 的訂戶協議。[訂戶協議](https://letsencrypt.org/repository/)

```sh
ACME_EMAIL=ops@example.org
SAS_IPV4='請換成 SAS 控制台顯示的公網 IPv4'
sudo install -d -o root -g root -m 0700 /var/lib/lab-tls
sudo install -o root -g root -m 0700 deploy/tls.sh /usr/local/sbin/lab-tls
printf '%s\n' "$PWD" | sudo tee /var/lib/lab-tls/project >/dev/null
printf '%s\n' "$ACME_EMAIL" | sudo tee /var/lib/lab-tls/email >/dev/null
printf '%s\n' "$SAS_IPV4" | sudo tee /var/lib/lab-tls/ip >/dev/null
sudo /usr/local/sbin/lab-tls issue
sudo openssl x509 -in secrets/public-tls/cert.pem -noout -ext subjectAltName -enddate
```

`issue` 用於首次安裝。尚無憑證且 gateway 未啟動時，lego 臨時綁定 443 以完成驗證，安裝後接步驟 6 核對線上服務。已上線的主機使用下方的[換證步驟](#site-cutover)。同一對憑證鏈及私鑰分別放在 `secrets/public-tls/` 與 `secrets/tls/`，擁有者為 10001:10001，目錄 0700、檔案 0600。`/var/lib/lab-tls` 保存 ACME 帳戶、快取、專案路徑、信箱及實際公網 IP，入口目錄僅 root 可讀。專案及其父目錄只允許受信任的部署者寫入。

**僅新建空庫**使用以下命令。透過私人通道將已核准公開的首次來源放到 `public_frontend/dist/data/lab.json` 和 `public_frontend/dist/assets/`。Git 與映像均不包含它們。容器 UID 10001 需能進入掛載的 `dist` 和相關子目錄並讀取檔案。資料轉換時來源唯讀，根目錄的私人參考檔不參與部署。

```sh
sh deploy/lab.sh init
sh deploy/lab.sh seed
sh deploy/lab.sh seed --apply
sh deploy/lab.sh cli import-csv /data/private/initial/content
sh deploy/lab.sh cli import-csv /data/private/initial/content --apply
sh deploy/lab.sh maintenance install-public /data/private/initial/public
sh deploy/lab.sh maintenance install-public /data/private/initial/public --apply
sh deploy/lab.sh maintenance check-files /data/public
```

`init` 建新庫並互動建立第一個管理員，請私下輸入 15–128 字元的獨立密碼。若僅建帳中斷，補跑 `sh deploy/lab.sh cli create-admin`。每個 `--apply` 前停下核對上一步預覽。依次完成轉換、CSV 入庫、公開檔案安裝及缺檔檢查後才啟動網站。

### 6. 啟動與核對 HTTPS

```sh
sh deploy/lab.sh up -d public-api admin-api gateway
sh deploy/lab.sh ps
sh deploy/lab.sh admin-info
sh deploy/lab.sh exec -T gateway nginx -t -c /tmp/nginx.conf
```

預期三個常駐服務。只有 `gateway` 發布 IPv4 主機端口，轉址 `80 → 8080`、官網 `443 → 8443`、管理 `27182 → 27182`。兩個 Python API 留在各自 Docker 內網。管理傳輸模式應為 `https`、Cookie 名稱 `__Host-lab_session`。先在 SAS 執行以下指令，確認收到 `301` 和 `Location: https://casino.ye.wang/`，再按步驟 2 對外放行 TCP 80。

```sh
curl -sSI -H 'Host: casino.ye.wang' http://127.0.0.1/
```

<a id="site-check"></a>

從**允許存取後台的外部工作電腦**輸入 SAS 公網 IPv4，執行下列核對。每個地址的前兩項應回應 `301`（官網）及 `302`（後台），兩份 `Location` 均保留原主機，後台另保留 `:27182`。接著跟隨轉址的兩次 GET 應分別到達官網 API 與管理登入頁並回應 `200`，最後的未登入查詢應回應 `401`。curl 預設會核對信任鏈、有效期及網址的域名或 IP。最後用瀏覽器核對登入、頁面、PDF，並從未獲准來源確認只有官網能連入。

```sh
SAS_IPV4='請換成 SAS 的實際公網 IPv4'
for host in casino.ye.wang crab.ye.wang ye.wang "$SAS_IPV4"; do
  curl -sSI "http://$host/api/home?view=recent"
  curl -sSI "http://$host:27182/"
  curl -fsSL --max-redirs 1 -o /dev/null -w '%{url_effective} %{http_code}\n' "http://$host/api/home?view=recent"
  curl -fsSL --max-redirs 1 -o /dev/null -w '%{url_effective} %{http_code}\n' "http://$host:27182/"
  curl -sS -o /dev/null -w '%{http_code}\n' "https://$host:27182/api/v1/auth/session"
done
```

接著設定 [root 的續期 cron](#tls-cron)，並製作[資料配對備份](#配對備份與異地保存)。憑證及 ACME 帳戶狀態另行加密備份到私人儲存。

<a id="http-redirect-cutover"></a>

### 已上線網站開通 80（一次性更新）

適用於正在運行正式 HTTPS gateway 的 SAS。先將本次完整部署版本同步到原專案目錄，沿用現有憑證和資料卷。以下步驟只需替換 gateway 一次，替換期間官網與後台會短暫中斷。請避開 root cron 的續期時間。

1. **在舊 gateway 繼續服務時準備映像。** 確認主機 80 可用，若有其他 Nginx 或容器佔用，先協調釋放。最後一行會啟動一次性容器，用現有憑證執行 `nginx -t`，不綁定主機端口。檢查成功才繼續。

```sh
cd -- "$(sudo cat /var/lib/lab-tls/project)"
sudo ss -ltnp 'sport = :80'
sudo docker ps --format 'table {{.Names}}\t{{.Ports}}'
sudo sh deploy/lab.sh check-web
sudo sh deploy/lab.sh config --quiet
sudo sh deploy/lab.sh build gateway
sudo sh deploy/lab.sh run --rm --no-deps -T gateway check-config
```

2. **在維護窗口替換 gateway。** 安裝新版續期腳本，使用同一把維護鎖避免與正在進行的換證重疊。兩個 API 繼續運行。

```sh
sudo flock -w 60 /var/lib/lab-tls/lock sh -ec '
  install -o root -g root -m 0700 deploy/tls.sh /usr/local/sbin/lab-tls
  sh deploy/lab.sh up -d --no-deps --force-recreate gateway
'
sudo sh deploy/lab.sh ps gateway
sudo sh deploy/lab.sh exec -T gateway nginx -t -c /tmp/nginx.conf
sudo /usr/local/sbin/lab-tls renew
curl -sSI -H 'Host: casino.ye.wang' 'http://127.0.0.1/api/home?view=recent'
```

3. **開放公網轉址。** 上一行應回應 `301` 和 `Location: https://casino.ye.wang/api/home?view=recent`。若啟動檢查失敗，先以 `sudo sh deploy/lab.sh logs --tail=50 gateway` 查看原因。確認轉址後，在阿里雲 **該 SAS 實例 → 防火牆** 將 TCP 80 開放給 `0.0.0.0/0`，443 繼續公開，27182 只允許管理員出口 IP。從站外完成[四個地址的檢查](#site-check)。

此後沿用既有的單一 root cron。日常續期只在需要時換證並平滑重載 Nginx，官網和後台繼續接收連線。每次續期無需再執行上述建置或替換步驟。

### HTTPS憑證與自動續期

**由 SAS 的 root cron 每 6 小時執行一次 `/usr/local/sbin/lab-tls renew`。** Let’s Encrypt 的 `shortlived` 憑證有效期為 160 小時，腳本在剩餘約 72 小時時開始續簽。已上線的舊站先完成[切換步驟](#site-cutover)。[Let’s Encrypt 短效憑證規格](https://letsencrypt.org/docs/profiles/#shortlived)

續期時 gateway 持續接收一般 HTTPS 流量，只將 443 上的 `acme-tls/1` 驗證握手轉給 Docker 內網的臨時 lego。安裝前核對憑證識別清單（SAN）恰好含三個 DNS 名稱及一個 IP、信任鏈有效、私鑰配對。腳本再用 `nginx -t` 及平滑 reload 載入新憑證，進行中的請求由舊 worker 完成。最後在 443 與 27182 各驗四次握手：域名送 SNI，裸 IP 核對預設憑證，八次指紋均須相符。有效期充足且兩端都已生效時只做檢查。[Nginx 握手分流](https://nginx.org/en/docs/stream/ngx_stream_ssl_preread_module.html) · [Nginx 平滑重載](https://nginx.org/en/docs/control.html)

<a id="site-cutover"></a>

#### 已上線的 HTTPS 網站換證

1. 確認[三個域名的 DNS](#site-dns) 均指向 SAS，443 可從公網連入，27182 僅允許管理來源。憑證驗證使用 443，80 用於官網 HTTP 轉址。將新版部署檔同步至 SAS 原專案目錄。選在舊憑證仍有效且沒有正在執行的換證任務時操作，保留原 ACME 帳戶、掛載目錄和資料卷。
2. 在 SAS 終端機檢查 gateway 和現有的 IP 設定。將顯示的 IP 與 SAS 控制台核對；曾只用域名的主機可能尚無 `ip` 檔。

```sh
cd -- "$(sudo cat /var/lib/lab-tls/project)"
sudo sh deploy/lab.sh ps gateway
sudo sh deploy/lab.sh exec -T gateway grep -Fx 'include /etc/lab/acme-stream.conf;' /tmp/nginx.conf
sudo sh deploy/lab.sh exec -T gateway grep -F 'include /etc/lab/https-redirect.conf;' /tmp/nginx.conf
sudo sh deploy/lab.sh port gateway 8080
sudo test ! -f /var/lib/lab-tls/ip || sudo cat /var/lib/lab-tls/ip
```

若 `ip` 缺少或與 SAS 控制台不同，在 SAS 執行以下指令，輸入實際公網 IPv4。

```sh
SAS_IPV4='請換成 SAS 控制台顯示的公網 IPv4'
printf '%s\n' "$SAS_IPV4" | sudo tee /var/lib/lab-tls/ip >/dev/null
```

3. 若上方顯示兩個 `include` 且端口映射為 `0.0.0.0:80`，直接進行第 4 步。若缺少任一項，先完成[一次性更新](#http-redirect-cutover)，該流程已包含安裝與執行 `renew`，可直接接第 5 步核對憑證。

4. 安裝新版主機腳本並執行與排程相同的命令。它會沿用 `project`、`email`、`ip` 及原 ACME 帳戶，用獨立的 lego 快取取得共用憑證。舊 `active`、`domain` 檔和舊憑證快取可原樣保留，新腳本只辦理這一組識別。

```sh
sudo install -o root -g root -m 0700 deploy/tls.sh /usr/local/sbin/lab-tls
sudo /usr/local/sbin/lab-tls renew
sudo openssl x509 -in secrets/public-tls/cert.pem -noout -ext subjectAltName -enddate -fingerprint -sha256
sudo openssl x509 -in secrets/tls/cert.pem -noout -enddate -fingerprint -sha256
```

5. 核對 SAN 顯示 `DNS:casino.ye.wang`、`DNS:crab.ye.wang`、`DNS:ye.wang` 和 `IP Address:實際公網IPv4`，兩處指紋應相同。從獲准來源完成[四個網址的外部檢查](#site-check)。然後依下節設定續期 cron。

簽發或憑證驗證失敗時，原有 PEM 繼續服務。若已開始安裝而重載或握手失敗，腳本會嘗試還原兩處 PEM 並平滑重載。中斷的安裝由下次執行先復原。排除 DNS、IP 或 443 可達性問題後，重跑 `sudo /usr/local/sbin/lab-tls renew`。修復期間留意舊憑證的到期日。

<a id="tls-cron"></a>

#### 在 Ubuntu 啟用續期 cron

在 SAS 上確認手動執行 `sudo /usr/local/sbin/lab-tls renew` 成功。若剛才已看到「兩個端口已使用有效版本」，直接設定排程即可。

```sh
sudo systemctl enable --now cron
sudo crontab -e
```

在 root 的 crontab 加入以下一行。已有 `lab-tls` 續期條目時，直接取代該條目，保留其他工作。儲存後，cron 會在主機當地時間每日 00:17、06:17、12:17、18:17 執行。[Ubuntu crontab 說明](https://manpages.ubuntu.com/manpages/noble/man5/crontab.5.html)

```cron
17 */6 * * * if /usr/local/sbin/lab-tls renew >/dev/null 2>&1; then /usr/bin/logger -t lab-tls OK; else /usr/bin/logger -p daemon.err -t lab-tls FAILED; exit 1; fi
```

用 `sudo crontab -l` 確認只有一條憑證續期工作。首次觸發後可用下列命令查看結果。

```sh
sudo journalctl -t lab-tls --since today --no-pager
```

`OK` 表示該次檢查或續期成功，可停用原先專為這張憑證建立的其他排程。`FAILED` 時，在 SAS 手動執行 `sudo /usr/local/sbin/lab-tls renew` 查看原因。站外監測可在憑證剩餘 48 小時仍未更新時通知維護者。

## 其他部署情境

### 既有環境切換

- **同一主機，已有 HTTP 測試資料**。先在原模式配對備份（使用 `sh deploy/lab.sh http …`），80 暫時只允許測試來源。用 `sh deploy/lab.sh http stop tools` 停止原有監測，再用 `sh deploy/lab.sh http stop gateway` 停止舊入口，撤銷 27181 的測試放行。確認三個域名及 IP 已就緒，按第 5 步申請憑證。以 `sh deploy/lab.sh up -d --force-recreate public-api admin-api gateway` 切換。接步驟 6 核對正式轉址，再將 80 開放給訪客。兩模式共用 `lab-backend_lab_data`，沿用原帳戶與內容，跳過 init、seed、CSV 匯入及公開內容安裝。管理員需重新登入。
- **從另一台主機遷移既有資料**。先取得來源主機同版本的資料庫及靜態檔案配對備份。新 SAS 在備好程式、憑證及雲端規則後走[恢復](#恢復)，再按步驟 6 驗證。不同主機上的同名 Docker volume 彼此獨立。

### HTTP 內容測試

先在防火牆將 80 和 27181 限制為受控測試來源，再使用 `sh deploy/lab.sh http …`。全新測試環境依步驟 4 建映像，將步驟 5 的初始化及步驟 6 的啟動命令加上 `http`，可跳過安裝憑證。此模式直接以 HTTP 提供官網 `http://測試主機IPv4/` 和管理登入頁 `http://測試主機IPv4:27181/`。使用獨立測試帳密，兩個模式輪流佔用同一主機的 80。

## 日常更新、停止與監測

以下命令使用正式 HTTPS 模式。

```sh
sh deploy/lab.sh build --pull public-api gateway
sh deploy/lab.sh up -d public-api admin-api gateway
sh deploy/lab.sh ps
sh deploy/lab.sh logs --tail=50 gateway public-api admin-api
# 可選第四個常駐容器，每60秒採樣，不開listener。
sh deploy/lab.sh watch
```

兩個前端或Nginx更新只需build／up gateway；Python更新build public-api，再up public-api admin-api。tools常駐時重新watch以套用新映像。不重跑init／seed／install-public；新設定用up重建，不能只restart。

停止用 stop gateway public-api admin-api tools，恢復用上述up／watch；不使用down -v或volume prune。更新前記錄提交、映像ID／digest及備份版本，回退只用相容SQLite10映像，資料回退另走恢復。

healthz表示程序可回應，readyz表示內容可讀。一次性 maintenance monitor與watch不要同時運行。程序不可用、內容連續3次非200、磁碟低於256MiB、備份失敗／缺失／超過48小時／版本錯配均告警。首次沒有備份會報備份告警；monitor不代表瀏覽器、實體檔案及TLS正常，外部通知與自動備份仍未配置。

### 配對備份與異地保存

先停管理寫入、watch及其他靜態檔案更新。每次使用新的版本和檔名，以下release-001只是示例：

```sh
sh deploy/lab.sh stop admin-api tools
sh deploy/lab.sh run --rm --no-deps tools mkdir -p /data/private/backups
sh deploy/lab.sh maintenance check-files /data/public
sh deploy/lab.sh cli backup /data/private/backups/release-001.enc --version release-001
sh deploy/lab.sh maintenance backup-static /data/public /data/private/backups/release-001-static.zip --version release-001 --database-backup /data/private/backups/release-001.enc
(umask 077; set -C; sh deploy/lab.sh run --rm --no-deps -T tools tar -C /data/private/backups -cf - release-001.enc release-001-static.zip > release-001-backups.tar)
sh deploy/lab.sh up -d admin-api
```

DB備份含五表並互動加密；靜態ZIP含assets、site.json及manifest，以版本、DB SHA256與逐檔SHA256配對，ZIP本身不加密。把匯出檔放異地，密碼分開保管；同volume備份不能防磁碟損毀。若先前啟用watch，完成後再啟動。備份目前需人工執行。

## 注意事項與排錯

### 瀏覽器顯示「The plain HTTP request was sent to HTTPS port」

先確認使用憑證涵蓋的網址。從管理防火牆允許的電腦，用以下命令檢查訪客的第一個回應。

```sh
SITE=casino.ye.wang
curl -sSI "http://$SITE/api/home?view=recent"
curl -sSI "http://$SITE:443/api/home?view=recent"
curl -sSI "http://$SITE:27182/"
```

應依次回應 `301`、`302`、`302`。前兩個目的地為 `https://casino.ye.wang/api/home?view=recent`，第三個為 `https://casino.ye.wang:27182/`。明文轉址只接受 GET／HEAD，未列入該入口憑證的主機和明文寫入均回應 400。請先打開 HTTPS 登入頁，再輸入帳密。若仍顯示舊的 Nginx 錯誤頁，核對[一次性更新](#http-redirect-cutover)中的實際端口映射及執行中的 gateway。

### 資料與容器位置

三個常駐容器使用兩個映像；tools沿用Python映像，按需啟動。SQLite沒有獨立容器。唯一local named volume預設lab-backend_lab_data，admin-api／tools掛到/data：

```text
/data/
  content/content.db       # assets、people、publications
  private/admin.db         # users、audit
  private/initial/         # seed輸出的CSV與公開檔案bundle
  private/backups/         # 配對備份，仍須匯出異地
  public/site.json         # 固定品牌、介紹與聯絡文字
  public/assets/           # 執行中的圖片、Logo、PDF
```

public-api只掛content子目錄唯讀，每次請求讀SQLite；gateway只掛public子目錄唯讀到/srv/public。gateway不掛SQLite或private。兩個API分別使用frontend_net／admin_net，只有共用gateway和維護tools連兩邊，兩API均無主機端口，tools不掛Docker socket。不使用NFS／SMB或多主機共寫。

兩模式共用API與頁面，官網瀏覽器使用/api/home、/api/people、/api/people/{id}、/api/publications；Nginx代理到Docker內網的/api/v1/public/路徑。/data/site.json與/assets/來自volume；/data/lab.json及未允許路徑回404。

### 來源與權限

| 操作 | 來源／結果 |
|---|---|
| init | 只建空庫；lab.sh init再接互動create-admin |
| seed | 掛public_frontend/dist唯讀到/seed，讀data/lab.json與assets；預覽回counts、files、applied=false，不寫庫 |
| seed --apply | 產生/data/private/initial/content及public；已有輸出拒絕覆寫，仍不寫庫 |
| import-csv … --apply | CSV新增入庫，不複製實體檔案 |
| install-public … --apply | 安裝公開檔案到/data/public，不寫業務庫 |

根目錄lab.json是私人參考，永不作初始化來源。首次來源JSON與assets不進Git或任何映像，git pull不會下載；由操作者另行提供。運行後不再從dist讀內容，修改dist/assets不會更新volume。CSV入庫與檔案安裝分開，須全部完成及缺檔檢查通過才開官網。

全專案只有根.gitignore與根.dockerignore，兩個映像以專案根目錄為context、逐檔白名單建置。同步程式時一起同步Compose、Dockerfile、排除規則及lab.sh，不在public_frontend內單獨docker build；官網Dockerfile與Nginx設定已併入deploy。check-web缺檔時先同步完整程式；不能靠關閉.dockerignore解決。

容器UID10001須能進入掛載的dist及子目錄，並讀取普通檔案；目錄可設0755、檔案0644，不含符號連結。若只有掛載根 dist 缺少搜尋權限，修正該目錄後可重新預覽。

```sh
chmod 755 public_frontend/dist
sh deploy/lab.sh seed
```

Nginx設定已打包到gateway，不再掛主機HTTP設定片段。映像內chmod不會修復初始化來源或TLS掛載權限；TLS仍保持700／600。不要chmod 777、遞迴放寬整個專案或改用root。

### 管理端口核對與空回應排錯

正式管理入口可用憑證中的任一域名或 SAS 公網 IPv4 加 `:27182`。`0.0.0.0` 是監聽位址。在主機上診斷 HTTP 測試入口可用以下命令。

```sh
sh deploy/lab.sh http admin-info
curl -i http://127.0.0.1:27181/
curl -i http://127.0.0.1:27181/api/v1/auth/session
```

HTTP的admin-info應同時顯示：主機27181→容器27181、listen 27181、gateway_http_test=true、api_http_test=True、origin_scheme=http、session_cookie=lab_session_http_test。它只讀運行中容器的端口／模式，不列密碼或整份環境。第一個curl應回登入頁HTML；未登入的session應回401，表示API可達且權限檢查生效。

HTTPS用不帶http的admin-info，端口應為27182、listen帶ssl、兩端HTTP測試模式均關閉、origin_scheme=https、Cookie為__Host-lab_session。正式瀏覽器網址使用憑證列出的三個域名或公網 IPv4。

| 現象 | 檢查／處理 |
|---|---|
| 本機curl成功，外部連不上 | 查 SAS 實例防火牆是否允許目前管理員出口IP；只看UFW不足以驗證Docker端口 |
| 登入403 Origin驗證失敗 | 協定與模式不符，或請求Origin與Host／端口不同；用admin-info核對，不關閉CSRF |
| 登入401 | 核對帳密；無預設帳戶，首次用create-admin，忘記密碼用recover-admin |
| 登入後仍顯示未登入 | 不混用HTTP／HTTPS；同步重建gateway與admin-api，重新登入取得對應Cookie |
| 瀏覽器強制跳HTTPS | HSTS按主機而非端口生效；HTTP測試改用VPS IPv4，不用已記住HSTS的正式域名 |
| 429 | 登入限流或密碼運算額度，稍後重試 |

沒有固定Host白名單後，任何指向VPS的域名都可開啟登入頁；仍需Cloud來源限制及有效帳密。所有非GET請求比對Origin與實際Host（含端口），協定由HTTP／HTTPS模式決定；除登入外另需CSRF token。保留Argon2、HttpOnly、SameSite=Strict、8小時Session及帳戶狀態檢查，不信任X-Forwarded-Host／Proto。HTTP仍為明文測試，不把密碼、Cookie或來源JSON貼入排錯紀錄。

### 容器啟動與首次資料排錯

| 現象 | 檢查／處理 |
|---|---|
| gateway建置缺檔 | 執行check-web；同步deploy/Dockerfile.gateway、入口設定及兩個前端程式，不用submodule update |
| volume subpath不存在 | content須先init，public須先install-public；核對lab-backend_lab_data，不以掛整個volume繞過隔離 |
| 圖片／PDF 404 | 查volume的/data/public/assets及API路徑，不查dist/assets作運行來源 |
| 官網載入失敗 | 查gateway／public-api日誌、四個API及/data/site.json；不啟用lab.json回退 |
| 503且存在DB journal | 由管理程序在原volume與/data路徑恢復；不刪journal或重建資料庫 |
| admin.lock忙碌 | 離線操作前stop admin-api tools並停止其他維護容器；不刪鎖檔 |
| seed／install輸出已存在 | 核對哪些步驟已完成，繼續後續步驟；不反覆套用、不刪volume重來 |

**端口佔用**：先確認佔用者，勿直接停止未知服務：

```sh
sudo ss -ltnp 'sport = :80'
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

若80顯示Welcome to nginx，且ss確認是主機Nginx，只有確認它沒有服務其他網站後才用sudo systemctl stop nginx；本網站正常後可disable該舊服務。HTTP 測試官網固定 80；須先確認並讓出此端口，再http up -d gateway。不要為排錯修改Python API端口。端口問題不需要重新匯入資料。

**正式 Nginx 設定讀取失敗**。先確認 UID 10001 可讀取 TLS 檔案。若錯誤指向映像內的程式或 Nginx 設定，從已審核版本重新建置並重建 gateway。

```sh
sh deploy/lab.sh build --no-cache gateway
sh deploy/lab.sh up -d --force-recreate gateway
sh deploy/lab.sh exec -T gateway nginx -t -c /tmp/nginx.conf
```

**seed錯誤**：若Python本身無法讀/app/scripts/prepare_initial_data.py，先重建public-api映像（tools共用），跑下列--help確認程式可讀；這時尚未執行資料驗證：

```sh
sh deploy/lab.sh build public-api
sh deploy/lab.sh run --rm --no-deps tools python scripts/prepare_initial_data.py --help
sh deploy/lab.sh seed
```

腳本內錯誤只輸出initial_data_failed的type、code及安全hint；排錯提供這兩行即可，不貼資料。常見code如下：

| code／類別 | 處理 |
|---|---|
| output_exists | 保留已產生的bundle，核對後續CSV匯入與檔案安裝是否完成 |
| source_json_unavailable／source_directory_invalid／permission_denied | 檢查指定dist、data/lab.json存在、可進入／可讀且無symlink；舊版可能把權限錯誤誤報缺檔 |
| asset_file_unavailable／assets_directory_invalid | 檢查來源引用的圖片／PDF是否完整 |
| asset_type_unsupported／symlink_not_allowed／asset_path_invalid | 僅svg/png/jpg/jpeg/webp/pdf普通檔案；移除來源副本中的雜檔，勿放寬白名單 |
| person_name_mismatch／person_membership_mismatch／author_reference_invalid | 核對姓氏、身份、作者ID；程式不猜姓名或自動改身份 |
| json_*／source_encoding_invalid／content_validation_failed | 核對JSON語法、UTF-8、重複鍵、欄位型別、校友日期及ID唯一性 |
| *_too_large／disk_full／filesystem_error／source_changed | 檢查容量、磁碟與掛載；套用期間不要修改來源 |

其他code依hint核對來源格式、發布時間及引用；新API輸出不是舊來源JSON格式。預覽成功才--apply，不用空內容跳過錯誤。

### 日常內容與檔案維護

管理頁處理內容及資產網址，不提供瀏覽器檔案上傳。實體檔案需經tools將受控來源目錄唯讀掛入後複製到/data/public/assets；例如API的/assets/papers/paper-1.pdf對應該目錄下papers/paper-1.pdf。只掛指定來源，不掛整個專案；先放新檔名再改網址，完成後check-files及配對備份。

固定文字維護/data/public/site.json，須符合maintenance.py的Site白名單，不放成員或論文。修改前備份，停止gateway、admin-api及其他維護程序，驗證後才啟動。整個public目錄若被更換，需force-recreate gateway以重新掛載subpath，不能只restart。

CSV僅新增、既有ID跳過、錯誤整批拒絕；帳戶、審計與衍生欄位不從內容CSV匯入。UTF-8固定欄位，未加引號的空值為null、雙引號空值為空字串，陣列／物件用JSON儲存格。imports須只含一至三種內容CSV且UID10001可讀：

```sh
sh deploy/lab.sh stop admin-api tools
sh deploy/lab.sh cli csv-templates /data/private/templates
sh deploy/lab.sh run --rm --no-deps -T tools cat /data/private/templates/people.csv
sh deploy/lab.sh run --rm --no-deps -v "$PWD/imports:/imports:ro" tools python -m lab_backend.cli import-csv /imports
sh deploy/lab.sh run --rm --no-deps -v "$PWD/imports:/imports:ro" tools python -m lab_backend.cli import-csv /imports --apply
sh deploy/lab.sh maintenance check-files /data/public
sh deploy/lab.sh up -d admin-api
```

忘記帳密：停admin-api與tools後執行 cli recover-admin，依提示設定新密碼，再up admin-api。新增首位帳戶用create-admin；後續帳戶也可在管理頁建立。審計匯出／清理使用cli export-audit／prune-audit的--help查看參數，先停管理寫入；不要將匯出字串當試算表公式執行。

### 恢復

先保留有效資料並停服務。異機先建置並用cli init建立空庫；恢復含帳戶，不另建管理員。將配對備份放private/backups或受控唯讀掛入，兩份預覽成功才分別套用：

```sh
sh deploy/lab.sh stop gateway public-api admin-api tools
sh deploy/lab.sh cli restore /data/private/backups/release-001.enc
sh deploy/lab.sh maintenance restore-static /data/private/backups/release-001-static.zip --database-backup /data/private/backups/release-001.enc --target /data/private/restored-public
sh deploy/lab.sh cli restore /data/private/backups/release-001.enc --apply
sh deploy/lab.sh maintenance restore-static /data/private/backups/release-001-static.zip --database-backup /data/private/backups/release-001.enc --target /data/private/restored-public --apply
sh deploy/lab.sh maintenance check-files /data/private/restored-public
```

restored-public必須不存在。原volume已有public時才執行下面保留步驟，異機新volume跳過；目標名稱必須未被使用：

```sh
sh deploy/lab.sh run --rm --no-deps tools sh -c 'test ! -e /data/private/public-before-restore-001 && mv /data/public /data/private/public-before-restore-001'
```

將驗證過的內容裝到新的public路徑，重建gateway讓subpath掛新目錄：

```sh
sh deploy/lab.sh maintenance install-public /data/private/restored-public
sh deploy/lab.sh maintenance install-public /data/private/restored-public --apply
sh deploy/lab.sh maintenance check-files /data/public
sh deploy/lab.sh up -d public-api admin-api
sh deploy/lab.sh up -d --force-recreate gateway
sh deploy/lab.sh maintenance monitor
```

只接受LABSQL10，不升級舊庫、不直接覆寫DB或刪journal；恢復保持論文順序，舊Session失效。異常斷電需先讓管理程序在原路徑恢復journal後正常停服。成功恢復後重新配對備份，舊監測狀態不代表新環境備份成功。

### 公開倉庫與私人部署資料

公開手冊列出本次指定的網站域名，IP 與信箱均使用示例值。管理員出口位址、雲端帳號與實例資料、登入資料、憑證私鑰、DNS 金鑰、真實來源、備份及具名校對筆記放在私人管道。排錯只摘錄狀態碼與必要的錯誤分類。發布前也要檢查程式中的樣本和截圖；加入忽略規則不會移除已追蹤檔案或既有提交。

若已公開有效憑證密鑰或 token，先撤銷或輪替。若要移除已提交的個資，還須評估舊提交、標籤、PR、fork 與既有副本；由倉庫擁有者決定協調清理範圍。[GitHub 敏感資料清理指引](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)

### 部署確認範圍

部署者須實際核對四頁內容、圖片／PDF、管理登入／修改／登出，以及允許與不允許來源的Cloud限制；只看UFW不足以驗證Docker公開端口。healthz／readyz正常也不代表完整網站、TLS或備份可恢復。

本次未啟動Docker、HTTP listener、修改DNS或部署；實際Linux建置、掛載權限、Nginx解析、瀏覽器、TLS續期、安全、負載與故障恢復仍待確認。只記錄實際執行的檢查，詳見[實作狀態](IMPLEMENTATION_STATUS.md)，不將本文件命令視為已完成驗收。
