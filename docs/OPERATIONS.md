# 阿里雲香港 SAS 部署與維護

首次正式部署依序完成下列六步。逐條執行命令，任何一步失敗先處理原因，再繼續。所有 `sh deploy/lab.sh` 命令都在專案根目錄執行，預設使用 HTTPS。已有資料的主機請先看[既有環境切換](#既有環境切換)。下文域名、IP、倉庫與版本均為示例，實際值由管理員私下保管。

## 正式部署主線

### 1. 選購香港區 Ubuntu 主機

在阿里雲「輕量應用伺服器」控制台建立實例。地域選 **中國（香港）**，鏡像選 **系統鏡像 → Ubuntu 24.04 LTS，64 位元 x86**（以購買頁可選版本為準）。使用乾淨系統即可，Python、Nginx 及 SQLite 隨本專案在 Docker 內執行。[阿里雲系統鏡像建立流程](https://help.aliyun.com/zh/simple-application-server/getting-started/quickly-create-a-server-using-a-system-image)

初次選型可由 **2 vCPU、4 GiB 記憶體** 起步。這是按約 30 位成員、300 篇論文及三個常駐容器估算，未做負載驗收。磁碟須容納映像、照片、PDF、SQLite 及備份暫存，方案的對外流量則依檔案下載量選擇。確認續費設定，在實例詳情記下公網 IPv4。透過控制台設定首次登入憑證，若用 SSH 可綁定維護者的公鑰。連線使用該鏡像指定的登入帳戶；進入主機後需能執行 sudo。[阿里雲 Linux 連線方式](https://help.aliyun.com/zh/simple-application-server/user-guide/connect-to-linux-server-remotely)

### 2. 先設 SAS 防火牆和維護入口

在**該實例 → 防火牆**調整入站規則，檢查並移除與下表衝突的預設廣泛放行規則。收窄 SSH 時可先新增允許的來源，測試第二個連線並確認救援入口後再撤銷全網規則；只新增窄範圍規則仍會留下原有的全網放行。阿里雲 Linux 實例的預設 TCP 規則包括 22、80、443。[阿里雲 SAS 防火牆](https://help.aliyun.com/zh/simple-application-server/user-guide/manage-the-firewall-of-a-server)

| TCP 端口 | 允許來源 | 用途 |
|---|---|---|
| 443 | 所有 IPv4，`0.0.0.0/0` | 官網 HTTPS |
| 27182 | 管理員固定公網出口，例如 `203.0.113.24/32`，或已核准的校內出口 CIDR | 管理頁及登入 API |
| 22 | 如需 SSH，僅限維護者公網出口 | 首次設定與維護 |
| 80、27181、其他未使用的 TCP 端口 | 移除入站放行規則 | 正式部署只發布上述兩個 HTTPS 網站入口 |

管理入口的來源限制完全由 SAS 防火牆執行。Docker 內部的 8000、8080、8443 無需雲端放行。先用允許的來源連入主機，再確認能用自己的帳戶實際登入控制台的 **VNC 救援連接**，才關閉不再使用的 SSH 規則。一般 SSH 或 Workbench 與救援登入的條件不同。[阿里雲救援連接](https://help.aliyun.com/zh/simple-application-server/user-guide/connect-to-a-server-by-using-the-rescue-feature)

### 3. 設定官網與管理域名

在域名的**權威 DNS 服務商**新增兩筆 A 記錄，均指向這台 SAS 的公網 IPv4。已有域名可繼續使用原 DNS 服務商。以 `example.org` 為例，`www` 對應 `www.example.org`，`admin` 對應 `admin.example.org`；若官網使用根域名，就用 `@` 取代 `www`。若同名已有指向其他服務的記錄，先確認切換影響。本部署採 IPv4 直連，不加 CDN 或 AAAA。[阿里雲 DNS 記錄設定](https://help.aliyun.com/zh/dns/pubz-add-parsing-record)

在工作電腦設定兩個**實際**域名，再從外部查詢。兩筆 A 結果應是該實例的 IPv4，AAAA 應無結果。這兩個變數只供手動核對，專案無需 `.env` 或 Host 設定。

```sh
PUBLIC_FQDN=www.example.org
ADMIN_FQDN=admin.example.org
dig +short A "$PUBLIC_FQDN"
dig +short A "$ADMIN_FQDN"
dig +short AAAA "$PUBLIC_FQDN"
dig +short AAAA "$ADMIN_FQDN"
```

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

### 5. 安裝兩組 TLS 憑證並初始化內容

為兩個實際域名各申請受瀏覽器信任的憑證。若用阿里雲數字憑證管理服務，提交域名申請，依畫面在權威 DNS 加入指定驗證記錄，簽發後下載 **Nginx** 格式。保留完整憑證鏈及配對私鑰；若自行產生 CSR，私鑰取自原申請端。使用 DNS 驗證可讓 80 維持關閉。[阿里雲憑證下載說明](https://help.aliyun.com/zh/ssl-certificate/download-an-ssl-certificate) · [DNS-01 驗證](https://letsencrypt.org/docs/challenge-types/#dns-01-challenge)

以安全通道將檔案放入操作者可讀的私有暫存目錄。下例假設來源在 `$HOME/tls-staging`，並已按用途命名。從專案根目錄執行，只複製普通檔案，目標目錄 0700、檔案 0600，容器 UID 10001 必須可讀。私鑰、DNS 機密及暫存副本均須由管理員保管。

```sh
sudo install -d -o 10001 -g 10001 -m 0700 secrets/public-tls secrets/tls
sudo install -o 10001 -g 10001 -m 0600 "$HOME/tls-staging/public-fullchain.pem" secrets/public-tls/cert.pem
sudo install -o 10001 -g 10001 -m 0600 "$HOME/tls-staging/public-key.pem" secrets/public-tls/key.pem
sudo install -o 10001 -g 10001 -m 0600 "$HOME/tls-staging/admin-fullchain.pem" secrets/tls/cert.pem
sudo install -o 10001 -g 10001 -m 0600 "$HOME/tls-staging/admin-key.pem" secrets/tls/key.pem
```

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

預期三個常駐服務。只有 `gateway` 發布 IPv4 主機端口，官網 `443 → 8443`、管理 `27182 → 27182`。兩個 Python API 留在各自 Docker 內網。管理傳輸模式應為 `https`、Cookie 名稱 `__Host-lab_session`。正式設定沒有 HTTP 80 的監聽或跳轉。

在已獲准的**外部工作電腦**沿用步驟 3 設定的兩個變數，執行以下命令並保留憑證驗證。三次預期結果依序為 200、200、401。最後一項驗證未登入的請求確實到達帳密保護的 API。

```sh
curl -sS -o /dev/null -w '%{http_code}\n' "https://$PUBLIC_FQDN/api/home"
curl -sS -o /dev/null -w '%{http_code}\n' "https://$ADMIN_FQDN:27182/"
curl -sS -o /dev/null -w '%{http_code}\n' "https://$ADMIN_FQDN:27182/api/v1/auth/session"
```

用瀏覽器開啟官網，核對合照、近期論文、成員列表、個人頁與 PDF。從允許來源到管理入口登入並登出，從另一個未獲准的網路確認官網可用、管理端口無法建立連線。首次上線後製作[配對備份](#配對備份與異地保存)，在私人維護紀錄記下部署提交、備份版本及兩張憑證到期日。憑證自動續期及外部告警需另行配置。

## 其他部署情境

### 既有環境切換

- **同一主機，已有 HTTP 測試資料**。先在原模式配對備份（該次備份命令使用 `sh deploy/lab.sh http …`），用 `sh deploy/lab.sh http stop tools` 停止原有監測，完成上述防火牆、域名、憑證及映像準備。以 `sh deploy/lab.sh up -d --force-recreate public-api admin-api gateway` 切換，接步驟 6 驗證；確認雲端 80、27181 的測試放行已撤銷。兩模式共用 `lab-backend_lab_data`，保留原帳戶與內容，跳過 init、seed、匯入及安裝。管理員需重新登入。
- **從另一台主機遷移既有資料**。先取得來源主機同版本的資料庫及靜態檔案配對備份。新 SAS 在備好程式、憑證及雲端規則後走[恢復](#恢復)，再按步驟 6 驗證。不同主機上的同名 Docker volume 彼此獨立。

### HTTP 內容測試

僅在受控來源開放測試端口，使用 `sh deploy/lab.sh http …`。全新測試環境依步驟 4 建映像，將步驟 5 的初始化及步驟 6 的啟動命令加上 `http`；可跳過安裝憑證。官網為 `http://測試主機IPv4/`，管理為 `http://測試主機IPv4:27181/`。HTTP 以明文傳送密碼和 Cookie，使用獨立測試帳密。日常部署預設執行 HTTPS。舊四容器測試環境若仍有已取代的 public-web，先確認其用途並停止它以釋放 80，保留資料 volume。

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

以實際域名連線，HTTP 測試也可用主機 IPv4。`0.0.0.0` 是監聽位址；在主機上診斷 HTTP 測試入口可用以下命令。

```sh
sh deploy/lab.sh http admin-info
curl -i http://127.0.0.1:27181/
curl -i http://127.0.0.1:27181/api/v1/auth/session
```

HTTP的admin-info應同時顯示：主機27181→容器27181、listen 27181、gateway_http_test=true、api_http_test=True、origin_scheme=http、session_cookie=lab_session_http_test。它只讀運行中容器的端口／模式，不列密碼或整份環境。第一個curl應回登入頁HTML；未登入的session應回401，表示API可達且權限檢查生效。

HTTPS用不帶http的admin-info，端口應為27182、listen帶ssl、兩端HTTP測試模式均關閉、origin_scheme=https、Cookie為__Host-lab_session。瀏覽器須使用憑證所對應的域名；取消Host白名單不會取消TLS憑證核對。

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

### HTTPS憑證與自動續期

兩張憑證分別保護各自瀏覽器到共用 Nginx 的連線，管理登入、Cookie 及傳輸內容在這一段使用 TLS。憑證的到期日以實際簽發結果為準。為兩個域名分別設到期通知，管理入口的握手檢查要從允許來源執行。

目前 Compose 沒有自動簽發、續期或載入憑證。使用託管服務時，確認它能將續簽的完整鏈和私鑰部署到**這台 SAS 的 Docker 掛載目錄**。自行自動化可用獨立 ACME 容器與權威 DNS 服務商的 DNS-01 外掛，按客戶端續期時程檢查。專用 DNS 身份只授予驗證所需記錄的權限，憑證帳戶狀態和 DNS 密鑰留在私有儲存。ACME 不需要存取 SQLite、Docker socket 或管理網路，驗證也不需要對公網開 80。[Let’s Encrypt 驗證方式](https://letsencrypt.org/docs/challenge-types/#dns-01-challenge)

人工續期時，先在私有暫存目錄驗證新鏈、SAN、到期日及公私鑰配對，保留上一套有效檔案。在已掛載的兩個 TLS 目錄內完整裝妥新檔，沿用步驟 5 的權限；避免更換 bind 掛載的根目錄或指向目錄外的符號連結。兩組檔案都準備好後才檢查並載入。

```sh
sh deploy/lab.sh exec -T gateway sh -c 'nginx -t -c /tmp/nginx.conf && nginx -s reload -c /tmp/nginx.conf'
```

從允許的外部網路重新握手，分別核對官網 443 與管理 27182 的有效鏈、序號及新到期日。檢查失敗時保留原服務並恢復上一套有效檔案，再檢查及載入。無人值守續期還需讓簽發、成套發布、載入、外部握手與失敗通知串接完成；現有 `maintenance monitor` 只檢查內部服務、磁碟與備份。

### 公開倉庫與私人部署資料

公開手冊只使用示例域名和保留 IP。管理員出口位址、雲端帳號與實例資料、登入資料、憑證私鑰、DNS 金鑰、真實來源、備份及具名校對筆記放在私人管道。排錯只摘錄狀態碼與必要的錯誤分類。發布前也要檢查程式中的樣本和截圖；加入忽略規則不會移除已追蹤檔案或既有提交。

若已公開有效憑證密鑰或 token，先撤銷或輪替。若要移除已提交的個資，還須評估舊提交、標籤、PR、fork 與既有副本；由倉庫擁有者決定協調清理範圍。[GitHub 敏感資料清理指引](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)

### 部署確認範圍

部署者須實際核對四頁內容、圖片／PDF、管理登入／修改／登出，以及允許與不允許來源的Cloud限制；只看UFW不足以驗證Docker公開端口。healthz／readyz正常也不代表完整網站、TLS或備份可恢復。

本次未啟動Docker、HTTP listener、修改DNS或部署；實際Linux建置、掛載權限、Nginx解析、瀏覽器、TLS續期、安全、負載與故障恢復仍待確認。只記錄實際執行的檢查，詳見[實作狀態](IMPLEMENTATION_STATUS.md)，不將本文件命令視為已完成驗收。
