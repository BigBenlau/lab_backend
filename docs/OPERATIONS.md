# 阿里雲香港 SAS 部署與維護

**部署順序是先用公網 IPv4 提供 HTTPS，取得域名控制者配合後再切換至 `ye.wang`。** 帳號 A 管域名及 DNS，帳號 B 管香港 SAS、Docker 與網站。第一階段由 B 獨立完成。兩個階段都只用一台 SAS，官網 443 對外，後台 27182 只允許管理員來源。lego 是 B 按需執行的 Docker 工具，Let’s Encrypt 是驗證公網入口並簽發憑證的機構。

| 時機 | 執行者與入口 |
|---|---|
| 首次 IP 上線 | B 依步驟 1–6 申請 IP 憑證、安裝、啟動並測試。 |
| 持續用 IP | B 啟用下方的雲端排程，每 6 小時檢查。IP 憑證有效 160 小時。 |
| 切換域名 | A 調整根域名 A 記錄。B 確認解析後簽發域名憑證，原排程接手域名續期。 |

部署指令均在 SAS 專案根目錄執行，另有標明的控制台與外部測試除外。下文 `203.0.113.10` 及聯絡信箱是佔位值。已有資料的主機先看[既有環境切換](#既有環境切換)。[Let’s Encrypt IP 憑證](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)

## 第一階段　B 以公網 IPv4 上線

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

### 3. B 備好申請材料

記下 SAS 可由外網直連的公網 IPv4、一個維護者收信的信箱，以及管理員電腦的公網出口 IP。前者是憑證要驗證的地址，後者是 27182 防火牆的允許來源。本階段不用操作 A 的 DNS，也不用任何 DNS API 金鑰。

本方案使用 **TLS-ALPN-01**。驗證時 Let’s Encrypt 會從外網連入 **443**，由臨時 lego 容器回答專用的 TLS 握手。首次簽發安排在 gateway 啟動前。日後實際重簽時，同一個 443 需短暫交給 lego，期間兩個網頁入口一起暫停。一般排程檢查維持服務在線。正式模式可持續關閉 80。[Let’s Encrypt 驗證方式](https://letsencrypt.org/docs/challenge-types/#tls-alpn-01)

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

在 B 的 SAS、已審核的專案根目錄，先確認 443 的使用者並拉取指定映像。若已有其他正式服務佔用 443，先協調維護窗口。腳本只管理本專案的 gateway。

```sh
sudo ss -ltnp 'sport = :443'
sudo docker ps --format 'table {{.Names}}\t{{.Ports}}'
sudo docker pull goacme/lego:v5.6.0
```

**以下在帳號 B 的 SAS 執行。先進入含有 `deploy/tls.sh` 的專案根目錄，將範例 IP 和信箱換成實際值。** 最後一行會代你執行下方折疊區內的 Docker 命令，整個流程執行一次即可。它會以 `--accept-tos` 表示同意 Let’s Encrypt 的訂戶協議，請先閱讀。[訂戶協議](https://letsencrypt.org/repository/)

**為什麼用 `/var/lib/lab-tls`？** 這是本專案在 SAS 主機上存放續期資料的固定位置，讓 Docker 容器結束、主機重啟或專案更新後都找得到原有帳戶和私鑰。Let’s Encrypt 沒有指定主機目錄，lego 使用的容器內路徑是 `/state`。現行腳本寫了 `state=/var/lib/lab-tls`，照本手冊部署就沿用它。若自行改用另一個持久且私有的目錄，需同步修改腳本的 `state`、重新安裝腳本，並將下列設定與維護命令指向新位置。已簽發過的站點還須完整遷移原目錄並保留擁有者及權限。[續期腳本](../deploy/tls.sh)

```sh
# 1. 這兩個值先存在目前的終端機。IP 填 SAS 的公網 IPv4，信箱填維護者的聯絡信箱。
SAS_IPV4=203.0.113.10
ACME_EMAIL=ops@example.org

# 2. 建立主機上的私人目錄。0700 表示只有擁有者 root 可以進入及讀寫。
sudo install -d -m 0700 /var/lib/lab-tls

# 3. 複製專案裡的維護腳本，讓管理員和雲端排程都能使用同一個固定命令。
sudo install -o root -g root -m 0700 deploy/tls.sh /usr/local/sbin/lab-tls

# 4. 各寫一個文字檔。project 記住目前專案路徑，ip 記住申請目標，email 記住聯絡信箱。
printf '%s\n' "$PWD" | sudo tee /var/lib/lab-tls/project >/dev/null
printf '%s\n' "$SAS_IPV4" | sudo tee /var/lib/lab-tls/ip >/dev/null
printf '%s\n' "$ACME_EMAIL" | sudo tee /var/lib/lab-tls/email >/dev/null

# 5. 啟動 IP 憑證流程。首次由 lego 向 Let’s Encrypt 申請，腳本核對後安裝到兩個入口。
sudo /usr/local/sbin/lab-tls ip
```

`$PWD` 是目前所在目錄的完整路徑。`printf` 輸出一行，`sudo tee` 以 root 身份把該行寫入指定檔案，`>/dev/null` 省略畫面回顯。這樣日後開新終端機或執行排程，腳本都能讀回這三個值。專案和其父目錄只允許受信任的部署者寫入。若之後搬動專案，從新根目錄更新 `project` 檔。若更新了 `deploy/tls.sh`，重做第 3 步的腳本複製。

<details>
<summary>查看 lab-tls ip 已代為執行的 Docker 命令</summary>

上方 `lab-tls ip` 已包含這一步，以下供核對 Docker 參數。執行完整部署時使用上方入口，讓它管理 443、檢查憑證、安裝並處理失敗復原。

```sh
sudo install -d -o 10001 -g 10001 -m 0700 /var/lib/lab-tls/lego
sudo timeout -k 15s 5m docker run --rm --name lab-tls-validation --label lab.tls=validation \
  --pull=never --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user 10001:10001 --tmpfs /tmp:rw,nosuid,nodev,size=16m \
  -p 0.0.0.0:443:8443 --mount type=bind,src=/var/lib/lab-tls/lego,dst=/state \
  goacme/lego:v5.6.0 run --accept-tos --email "$ACME_EMAIL" --path /state \
  --domains "$SAS_IPV4" --profile shortlived --tls --tls.address :8443 \
  --ari-disable --renew-days 4 --no-random-sleep
```

| 參數 | 具體用途 |
|---|---|
| `install -d -o 10001 -g 10001 -m 0700 …/lego`、`--user 10001:10001` | 建立專給 lego 寫入的子目錄，擁有者與容器程序的使用者及群組編號相同。主機父目錄仍由 root 管理。 |
| `timeout -k 15s 5m`、`docker run --rm` | 臨時啟動容器。到 5 分鐘仍未完成就要求終止，再過 15 秒仍未退出則強制終止。容器退出時自動移除，保留掛載到主機的資料。 |
| `--name`、`--label`、`--pull=never` | 給驗證容器固定名稱與標記，方便腳本於失敗時辨認及清理。使用已下載的 v5.6.0 映像。 |
| `--read-only`、`--cap-drop ALL`、`--security-opt no-new-privileges`、`--tmpfs /tmp…` | 容器根檔案系統唯讀，移除額外系統權限並限制提權。`/tmp` 提供最多 16 MiB 的臨時可寫空間。 |
| `-p 0.0.0.0:443:8443`、`--tls --tls.address :8443` | Let’s Encrypt 從外網連到 SAS 的 443。Docker 將連線轉給容器內在 8443 等候的 lego，由它完成 TLS 驗證。 |
| `--mount …src=/var/lib/lab-tls/lego,dst=/state`、`--path /state` | 將 SAS 的 `lego/` 子目錄映射為容器的 `/state`。lego 寫入的帳戶、憑證和私鑰會留在主機，可供下次續期。 |
| `goacme/lego:v5.6.0 run`、`--accept-tos`、`--email`、`--domains`、`--profile shortlived` | 用指定版本的 lego 辦理申請或續期，接受條款，帶入聯絡信箱、SAS 公網 IP 和短效憑證類型。這裡 `--domains` 的值就是公網 IP。 |
| `--ari-disable --renew-days 4 --no-random-sleep` | lego 略過簽發機構建議的續期時間，採用剩餘天數判斷，並略過隨機等待。本專案的外層腳本先按 72 小時門檻決定何時讓出 443，內層 4 天門檻讓 lego 在該窗口內可立即續簽。 |

完整命令與參數可對照[續期腳本](../deploy/tls.sh)及 [lego 指令參考](https://go-acme.github.io/lego/references/ref-flags/)。

</details>

**看到「已安裝 ip 憑證」才繼續。** 腳本會以系統信任庫及 `openssl verify -verify_ip` 核對憑證鏈、IP 身份，並核對私鑰。兩個入口各有同一份 `cert.pem` 和 `key.pem`，位於 `secrets/public-tls/` 和 `secrets/tls/`。目錄 0700、檔案 0600，擁有者為 10001:10001。以下命令只顯示公開的身份、到期時間與指紋，IP 應出現在 `IP Address` 欄位。

```sh
sudo openssl x509 -in secrets/public-tls/cert.pem -noout -ext subjectAltName -enddate -fingerprint -sha256
sudo openssl x509 -in secrets/tls/cert.pem -noout -enddate -fingerprint -sha256
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

從**防火牆允許的外部工作電腦**用實際 IP 測試，保留 curl 的預設憑證驗證。預期官網 200、管理登入頁 200、未登入的管理 Session 401。在瀏覽器打開 `https://公網IP/` 和 `https://公網IP:27182/`，核對官網內容、PDF 以及管理登入／登出。從未獲准來源也要確認官網可用、27182 無法連線。

```sh
SAS_IPV4=203.0.113.10
curl -sS -o /dev/null -w '%{http_code}\n' "https://$SAS_IPV4/api/home"
curl -sS -o /dev/null -w '%{http_code}\n' "https://$SAS_IPV4:27182/"
curl -sS -o /dev/null -w '%{http_code}\n' "https://$SAS_IPV4:27182/api/v1/auth/session"
```

接著設定下方的**自動續期**，並製作[資料配對備份](#配對備份與異地保存)。憑證及 ACME 帳戶狀態另行加密備份到私人儲存。

### HTTPS憑證與自動續期

**B 只需啟用一個 `renew` 任務。** 本機工具會讀取 `/var/lib/lab-tls/active`，第一次安裝成功後是 `ip`，成功切換域名後才變為 `domain`。以檔案鎖序列化首次簽發、續期及切換。先在 SAS 手動驗證排程即將執行的同一條命令。

```sh
sudo cat /var/lib/lab-tls/active
sudo /usr/local/sbin/lab-tls renew
```

IP 憑證有效 160 小時。排程每 6 小時檢查，剩餘 72 小時內開始續簽，失敗仍有重試時間。域名模式的門檻是 14 天。每次檢查都核對本地憑證及兩端口實際送出的指紋。尚未到期且版本一致時會輸出「兩個端口已使用有效版本」，不會重啟 gateway。已簽發但尚未交付的憑證會優先安裝，避免反覆申請。[Let’s Encrypt 短效憑證](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)

**實際續簽時會短暫停站。** 腳本備份兩個掛載目錄的 PEM 與目前模式，僅停止共用 gateway，兩個 API 和 SQLite 繼續運作。lego 完成 443 驗證後，腳本檢查信任鏈、IP 或域名及配對私鑰，更新兩個掛載，啟動 gateway、執行 `nginx -t` 和 reload，並從主機對 443、27182 做 TLS 握手。Nginx 共用程序，因此官網及後台都會受到這個窗口影響。`--renew-days` 配合本機剩餘時間門檻，讓排程掌握讓出 443 的時機。[lego 選項](https://go-acme.github.io/lego/references/ref-flags/) · [Nginx 重載](https://nginx.org/en/docs/control.html)

#### B 啟用每 6 小時的雲端排程

1. 在 **B 的香港區 SAS → 實例 → 命令助手** 確認內建 Agent 在線。先手動送出 Shell 命令 `/usr/local/sbin/lab-tls renew`，工作使用者 `root`，逾時 600 秒。查看執行結果，確認退出碼 0，以及兩個入口驗證成功。這條維護通道可在不對公網開放 SSH 的情況下運作。[SAS 命令助手](https://help.aliyun.com/zh/simple-application-server/user-guide/use-command-assistant)
2. 在 **B 的雲維運編排 OOS（CloudOps Orchestration）** 選香港地域，建立供 OOS 扮演的專用 RAM 角色，信任服務 `oos.aliyuncs.com`。授予 `swas-open:RunCommand` 及 `swas-open:DescribeInvocationResult`，依各操作支援的資源範圍收窄至 B 的香港資源。`RunCommand` 具有在主機執行命令的權限，只讓受信任的維護者編輯範本、啟動任務及傳遞該角色。這個角色用於 B 的主機維護，無須授予 A 的 DNS 權限。[發送命令的授權](https://help.aliyun.com/zh/simple-application-server/developer-reference/api-swas-open-2020-06-01-runcommand) · [結果查詢授權](https://www.alibabacloud.com/help/en/simple-application-server/developer-reference/api-swas-open-2020-06-01-describeinvocationresult)
3. 在 **OOS → 自訂範本 → 建立範本** 貼入下方 YAML，名稱可用 `Lab-TLS-Renew`。儲存後選「建立執行」，輸入 SAS 實例 ID、角色名、未來的排程結束時間（UTC，例如 `2028-12-31T00:00:00Z`，在結束前延長）。啟動一次並保留週期執行。每次任務先取得命令執行 ID，再等候 SAS 回報成功；送出命令本身不足以證明續期完成。[OOS 定時觸發](https://www.alibabacloud.com/help/en/oos/user-guide/acs-timertrigger) · [等待實際結果](https://www.alibabacloud.com/help/en/oos/user-guide/acs-waitfor)

```yaml
FormatVersion: OOS-2019-06-01
Description: Check and deploy the active lab certificate on the Hong Kong SAS
Parameters:
  instanceId:
    Type: String
  roleName:
    Type: String
  endDate:
    Type: String
RamRole: '{{ roleName }}'
Tasks:
  - Name: timer
    Action: ACS::TimerTrigger
    Properties:
      Type: rate
      Expression: 360 minutes
      EndDate: '{{ endDate }}'
  - Name: send
    Action: ACS::ExecuteAPI
    Properties:
      Service: SWAS-OPEN
      API: RunCommand
      Parameters:
        RegionId: cn-hongkong
        InstanceId: '{{ instanceId }}'
        Name: lab-tls-renew
        Type: RunShellScript
        CommandContent: /usr/local/sbin/lab-tls renew
        WorkingUser: root
        Timeout: 600
    Outputs:
      invocation:
        Type: String
        ValueSelector: .InvokeId
  - Name: result
    Action: ACS::WaitFor
    Properties:
      Service: SWAS-OPEN
      API: DescribeInvocationResult
      Parameters:
        RegionId: cn-hongkong
        InstanceId: '{{ instanceId }}'
        InvokeId: '{{ send.invocation }}'
      PropertySelector: .InvocationResult.InvocationStatus
      DesiredValues: [Success]
      StopRetryValues: [Invalid, Aborted, Failed, Error, Timeout, Cancelled, Terminated]
    Retries: 72
    DelayType: Constant
    Delay: 10
```

4. 在 OOS 執行詳情核對首輪的 `send` 和 `result`，之後確認下一輪也有啟動。為任務失敗設定維護者通知，並由獨立監測從管理來源檢查兩端可用性及到期時間，可設 IP 到期前 48 小時、域名到期前 7 天告警。若 OOS 執行停止或到達結束日期，需重新啟用。只要尚未啟動這項雲端任務，手動 `renew` 或 `docker compose up` 都不會自行排程。

## 第二階段　A 設定 ye.wang，B 切換憑證

選一個 A、B 都能配合的上線窗口。切換前，B 先執行 `sudo /usr/local/sbin/lab-tls renew` 確認 IP 入口仍有可用的憑證。以下順序要求域名已能到達 B 才申請域名憑證，從 DNS 切換到新憑證生效之間，以域名連線可能暫時顯示憑證名稱不符。

1. **A 在實際代管 `ye.wang` 的權威 DNS 面板操作。** 記下現有記錄及 TTL。新增或更新 `A` 記錄，主機記錄 `@`，值填 B 提供的 SAS 公網 IPv4。`@` 代表 `ye.wang` 本身。請先確認舊 A、AAAA、別名或 CDN 的使用情況，本部署需要所有公開查詢指向同一台 IPv4 SAS。管理端沿用同一域名及 `:27182`。A 完成 DNS 設定即可，無須交出帳密、API 金鑰，或為每次續期新增 TXT。[阿里雲 DNS 記錄設定](https://help.aliyun.com/zh/dns/pubz-add-parsing-record)
2. **B 在外部工作電腦查詢。** 以下兩個解析器的 A 結果均應只有 SAS 公網 IP，AAAA 應為空。待權威記錄及可見的公共解析一致，再開始 443 驗證。若域名有 CAA 限制，請 A 確認允許 Let’s Encrypt。[Let’s Encrypt CAA 說明](https://letsencrypt.org/docs/caa/)

```sh
dig @1.1.1.1 +short A ye.wang
dig @8.8.8.8 +short A ye.wang
dig @1.1.1.1 +short AAAA ye.wang
dig @8.8.8.8 +short AAAA ye.wang
```

3. **B 在 SAS 申請一張域名憑證並切換兩個入口。** 下列命令執行相同的 `docker run` 流程，但申請對象改為 `--domains ye.wang` 並使用域名憑證的預設 profile。原 IP 憑證和 ACME 狀態仍留在私人目錄。工具以 `-verify_hostname ye.wang` 驗證新憑證及兩個入口，成功後才提交 `active=domain`。同一個 OOS 任務此後只更新域名，兩階段共用同一資料 volume，略過 `init`、`seed`。

```sh
printf '%s\n' 'ye.wang' | sudo tee /var/lib/lab-tls/domain >/dev/null
sudo /usr/local/sbin/lab-tls domain
sudo cat /var/lib/lab-tls/active
sudo /usr/local/sbin/lab-tls renew
```

4. **B 從允許來源透過一般 DNS 驗證。** 下列命令依序應得 200、200、401。瀏覽器分別開啟 `https://ye.wang/` 及 `https://ye.wang:27182/`。域名憑證只申請 `ye.wang`，切換後以域名存取；用裸 IP 連線會出現名稱不符。IP 階段的登入 Cookie 屬於原主機，管理員須以新網址重新登入。

```sh
curl -sS -o /dev/null -w '%{http_code}\n' https://ye.wang/api/home
curl -sS -o /dev/null -w '%{http_code}\n' https://ye.wang:27182/
curl -sS -o /dev/null -w '%{http_code}\n' https://ye.wang:27182/api/v1/auth/session
```

再從同一台外部電腦讀取兩個入口實際送出的域名憑證。兩個 SHA-256 指紋都應等於 B 在 SAS 對 `secrets/public-tls/cert.pem` 執行步驟 5 的 OpenSSL 指令所得的指紋。

```sh
for port in 443 27182; do
  openssl s_client -connect "ye.wang:$port" -servername ye.wang </dev/null 2>/dev/null |
    openssl x509 -noout -enddate -fingerprint -sha256
done
```

#### 換證失敗或需要退回 IP

簽發、安裝或本機握手失敗時，腳本以非零代碼結束，還原兩個入口的舊 PEM 及原本的續期模式，並嘗試重新啟動原先運行的 gateway。若主機斷電或執行被強制終止，下次執行先處理 `/var/lib/lab-tls/pending`。在私人維護終端查看 SAS 命令結果及 `sh deploy/lab.sh ps`。若有 443 衝突先辨識佔用者，排除原因後手動重試。切勿刪除 ACME 狀態或 SQLite volume 來排錯。

```sh
sudo ss -ltnp 'sport = :443'
sudo sh deploy/lab.sh ps
sudo /usr/local/sbin/lab-tls renew
```

完成域名切換後若要主動退回，以 `sudo /usr/local/sbin/lab-tls ip` 還原 IP 服務與 `active=ip`。若快取 IP 憑證已到續期門檻，腳本會先重簽。由允許來源以兩個 IP 網址核對並通知管理員改用 IP。若 DNS 也須退回舊站，由 A 依先前記錄還原，並等待 TTL。保管加密的 ACME 帳戶、私鑰、`previous` 中的上一套檔案、IP／域名資料；這些都不屬於 Git、映像或 SQLite 內容備份。

## 其他部署情境

### 既有環境切換

- **同一主機，已有 HTTP 測試資料**。先在原模式配對備份（該次備份命令使用 `sh deploy/lab.sh http …`），用 `sh deploy/lab.sh http stop tools` 停止原有監測，準備防火牆及映像後，用 `sh deploy/lab.sh http stop gateway` 停止舊測試入口，再執行第 5 步首次申請 IP 憑證。以 `sh deploy/lab.sh up -d --force-recreate public-api admin-api gateway` 切換，接步驟 6 驗證；確認雲端 80、27181 的測試放行已撤銷。兩模式共用 `lab-backend_lab_data`，保留原帳戶與內容，跳過 init、seed、CSV 匯入及公開內容安裝。管理員需重新登入。
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

IP 階段以公網 IPv4 連線，切換後改用實際域名。`0.0.0.0` 是監聽位址；在主機上診斷 HTTP 測試入口可用以下命令。

```sh
sh deploy/lab.sh http admin-info
curl -i http://127.0.0.1:27181/
curl -i http://127.0.0.1:27181/api/v1/auth/session
```

HTTP的admin-info應同時顯示：主機27181→容器27181、listen 27181、gateway_http_test=true、api_http_test=True、origin_scheme=http、session_cookie=lab_session_http_test。它只讀運行中容器的端口／模式，不列密碼或整份環境。第一個curl應回登入頁HTML；未登入的session應回401，表示API可達且權限檢查生效。

HTTPS用不帶http的admin-info，端口應為27182、listen帶ssl、兩端HTTP測試模式均關閉、origin_scheme=https、Cookie為__Host-lab_session。瀏覽器使用的 IP 或域名必須符合憑證身份。

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
