# 架構與核心要求

Python 3.14／FastAPI／Pydantic／sqlite3，原生HTML／CSS／JavaScript管理頁。維持八個功能模組與三個管理靜態檔，不增加重複多層架構。契約7.1.0、URL前綴/api/v1、SQLite格式10、LABSQL10備份。

本文只記設計與不可破壞的規則。完整[API](docs/API.md)、[欄位字典](docs/DATABASE.md)、[操作步驟](docs/OPERATIONS.md)及[實際檢查狀態](docs/IMPLEMENTATION_STATUS.md)各自維護，不在多份文件重複欄位定義。

以前端現有版面與展示需求決定內容欄位，後端提供所需資料，前端對接API；不為沿用舊資料模型而修改頁面樣式或保留沒有用途的內容欄位。帳戶、審計、編輯版本及備份屬於運作需求，仍需保留。

## 模組與閱讀順序

| 模組 | 職責／主要入口 |
|---|---|
| lab_backend/models.py | Pydantic模型、姓名排序、作者關聯及公開欄位；先讀此檔 |
| lab_backend/store.py | SQLite交易、CSV與容量驗證；Store.read/change/replace |
| lab_backend/public.py | 首頁、成員列表、單人詳情、論文列表四個GET |
| lab_backend/content.py | 三類共用CRUD、版本與表單schema |
| lab_backend/auth.py | Argon2、Cookie Session、帳戶及審計；build_user供網頁／CLI共用 |
| lab_backend/application.py | 兩個API入口、請求邊界、錯誤與健康檢查 |
| lab_backend/config.py | 環境設定，不在匯入時初始化資料 |
| lab_backend/cli.py | 空庫初始化、帳戶恢復、CSV、加密備份／恢復 |
| scripts/maintenance.py | 公開檔案安裝、固定設定白名單、缺檔、靜態備份／恢復及監測 |
| scripts/prepare_initial_data.py | 明確將前端首次來源轉成CSV＋公開檔案bundle，不開資料庫 |
| scripts/sync_contracts.py | 真實路由→OpenAPI→公開前端型別 |
| admin_frontend/admin/ | index.html、app.js、style.css；七個功能頁共用API與表單 |

閱讀一次請求：`public.py → Store.read → 公開白名單 → JSON`；寫入則是`content.py → Store.change(operation) → 交易內授權／版本／關聯／容量檢查 → 內容與審計一起提交`。回呼operation執行時已持寫鎖。

Pydantic的`str | None`表示可空值，`Literal`限制選項，`Annotated/Field`限制長度與格式。業務模型、儲存歷史及公開投影分開，避免輸入操作者或洩漏私有欄位。管理頁的field/formFields依schema產生表單；accountForm共用帳戶表單，api統一Cookie／CSRF／錯誤。epoch/view阻止舊請求覆蓋新頁面；儲存期間鎖定欄位，失敗保留輸入。

### 契約同步

開發環境使用Python3.14及requirements-dev.lock；以下在專案根目錄執行，不是VPS部署步驟：

```sh
python scripts/sync_contracts.py --check  # 僅比對兩份OpenAPI及前端型別
python scripts/sync_contracts.py          # 模型／路由變更後，明確重新生成
python scripts/check_basics.py            # 僅檢查匯入、契約及設定，不改檔
```

這些工具不啟動服務或讀取業務資料。生成會更新public_frontend/contracts/public-api.d.ts；該檔與前後端程式由同一Git倉庫管理，可一起提交，不能手改生成型別。

## 資料與交易

SQLite是業務內容唯一執行來源，每次API請求開新快照，只讀呼叫端明確要求的表，無靜態JSON回退或回應快取。content/content.db保存assets、people、publications；private/admin.db保存users、audit，共5表47欄。users為登入身份，people為網站成員，兩者不自動綁定。

- init只建空庫；內容經管理頁或明確CSV匯入。根目錄lab.json是真實私人參考，不讀取、複製、提交Git或放進Docker。使用者指定public_frontend/dist/data/lab.json及dist/assets為首次來源，seed僅在明確執行時掛唯讀來源，轉成CSV與公開檔案bundle；原始JSON不入Git，兩者不入映像、不在啟動時讀取。檢查只用合成資料。
- seed診斷只輸出例外類型、固定錯誤代碼及白名單提示；JSON語法錯誤可附行／欄數，不輸出原始例外、姓名、ID、來源中的實際檔名／路徑或Pydantic輸入。未知錯誤維持泛化；診斷改善不放寬來源驗證、檔案型別或既有輸出不得覆寫的規則。
- 靜態引用檔以lstat檢查型別及符號連結，讓權限錯誤保留為permission_denied；避免Python3.14的is_file回傳False而將目錄不可進入誤認為缺檔。seed掛載的dist本身及其子目錄須讓UID10001可進入，檔案可讀；不自動更改主機權限。
- 全部內容儲存即公開，無草稿／發布／封存。固定品牌、介紹及聯絡頁文字由後端維護工具管理public/site.json，公開官網只讀展示；不放people/publications、不作業務內容回退。沒有研究方向、招募、新聞、網站設定API或相關資料表，也沒有UMPASS、瀏覽器上傳或媒體worker。
- 畢業年月存people.alumniSince，為YYYY-MM或null；僅有至少一項身份且所有isCurrent=false的校友可填，其他必須null。管理API、CSV、恢復及讀取共用模型驗證；不補造日期、自動切換身份或靜默清除日期。成員列表與個人詳情均回傳此欄位。尚未正式部署，SQLite10只初始化新庫，不提供舊庫升級工具。
- 姓名分姓／名，照片存people.portrait。唯一可寫關聯是authors[].personId；publicationIds在同一快照反查，不雙向重複保存或依同名配對。公開不輸出created/updated或其時間，不暴露操作者。
- englishName為可留空的英文名；公開個人頁直接回相關publications，publicationIds只留在管理回應。首頁只回metadata.purpose=group_photo的全部合照、前三篇摘要及論文總數；已移除people.slug/sourceUrl/affiliation及publications.slug；管理歷史與版本仍保留。
- 論文內部按rowid升序讀取，API／管理列表／反查倒序，首頁取前三篇。UPSERT保留位置，CSV依列序新增，備份／恢復保持內部順序；不得自行VACUUM、重排或重建表。
- 30人／300篇是估算而非筆數上限。每表解碼及CSV各32MiB、內容單筆JSON252KiB；提交與離線預覽共用限制。審計分頁、串流封存，累積量不阻擋登入。

管理以admin.db為main，ATTACH content.db，同連線BEGIN IMMEDIATE＋DELETE journal＋FULL synchronous，內容與成功審計同次提交；SQL值參數綁定，表名取模型白名單。密碼雜湊在寫交易外執行，授權與版本在交易內重查。

公開mode=ro/query_only，固定快照後若見內容庫journal即503。恢復由管理程序在原環境／原掛載路徑處理，不刪日誌。僅支援單台Linux本機POSIX檔案系統、單一admin worker。admin.lock讓服務與離線維護互斥；備份／監測沿用同一非阻塞鎖實作。

## 部署與安全

```mermaid
flowchart LR
  B[公開瀏覽器] -->|HTTPS 443| F[public-web Nginx]
  F -->|lab-content| P[public-api]
  F -->|唯讀public子目錄| S[assets及site.json]
  T[tools離線維護] -->|寫入／備份| S
  P -->|唯讀| C[(content.db)]
  A[管理瀏覽器] -->|HTTPS 27182 預設| W[Cloud防火牆 來源IP限制]
  W --> G[gateway HTTPS／管理頁]
  G -->|admin_net| M[admin-api]
  M -->|讀寫| C
  M -->|讀寫| D[(admin.db)]
  subgraph V[Docker named volume：lab_data]
    C
    D
    S
  end
```

兩API無主機端口。public-api只掛volume的content子目錄唯讀；admin-api掛完整volume到/data、可寫兩庫；gateway不掛DB、不加入公開內容網路，對外IPv4端口由ADMIN_HTTPS_PORT指定（預設27182），容器內固定27182。public-web以獨立Nginx容器使用443（內部8443），僅連public_edge及lab-content，唯讀掛public子目錄，不接觸SQLite或private。四個常駐容器保留非root、唯讀rootfs、cap_drop、no-new-privileges及資源限制；無VPN前提或獨立DB容器。

測試可明確選用deploy/compose.http.yml：官網HTTP為PUBLIC_HTTP_PORT預設80→8080，管理HTTP為ADMIN_HTTP_PORT預設27181→27182，兩入口都取代正式端口及TLS掛載，不需憑證，四個常駐服務均可啟動。兩模式共用頁面、路由、資料volume及API，不複製管理業務。Nginx只切換transport.conf中的監聽／TLS指令，HTTP不發HSTS、不跳轉HTTPS；gateway測試片段由entrypoint生成固定指令，正式仍為原TLS設定。端口衝突由部署者明確處理，不自動停止其他服務。

HTTP管理模式須由覆蓋檔明確設定LAB_HTTP_TEST=true及精確http Origin；ADMIN_HOST可為實際測試IPv4或域名。使用獨立lab_session_http_test Cookie、不設Secure，保留HttpOnly、SameSite=Strict、帳密、Origin／CSRF、帳戶狀態與登入限流；LAB_DEV仍僅限loopback且不可與HTTP測試同開。正式Compose明確設LAB_HTTP_TEST=false，使用HTTPS Origin與__Host-lab_session Secure Cookie，設定或切換後重建服務、重新登入。HTTP不加密密碼及Cookie，只供受Cloud來源限制的受控測試；兩API仍不發布主機端口。啟動及切回步驟集中OPERATIONS。

全專案只維護根目錄.gitignore與.dockerignore，不使用子目錄或Dockerfile專用排除檔。Git排除機密、初始化來源、執行庫／journal、交換檔、備份與開發快取，保留根.env.example及程式／契約。三個映像的build context統一為專案根目錄；[Docker建置白名單](https://docs.docker.com/build/concepts/context/#dockerignore-files)預設排除全部，只放行Python模組、兩支維護腳本、管理三檔、官網五檔及所需建置／Nginx設定，各Dockerfile再只COPY自己的程式。新增依賴檔案須同步根白名單；初始化來源只在seed時唯讀掛載，不經builder。

public-web只複製lab.html、lab.js、api.js、lab.css、gold.css與兩份Nginx設定，首頁映射index.html。Nginx只代理四個GET，清除Cookie／Authorization，其他API、原始JSON及未知靜態路徑回404。網站程式每次換頁重新讀API，失敗顯示重試；不保留靜態模式或快照回退。兩個Nginx的暫存路徑都設在/tmp，符合[非root容器設定](https://github.com/nginx/docker-nginx-unprivileged/blob/main/README.md#troubleshooting-tips)；不落地快取上游回應。實體圖檔／PDF由volume的public/assets提供；固定設定由public/site.json提供，兩者都不進前端映像。

gateway就是Nginx管理入口，提供管理頁靜態檔、HTTPS、合法Host檢查、API反向代理及基本登入限流，不保存內容或實作登入業務。來源IP存取僅由Cloud防火牆／安全組控制，程式不重複管理allow／deny清單；Python仍驗證帳密、Session與權限。

2026-11-17確認全Docker部署、首次空庫；管理員直接經受來源IP限制的HTTPS端口登入，取代SSH通道前提。VPS只需既有Docker／Compose及可用維護控制台，不安裝主機Python或修改systemd；SSH可以不公開，關閉前須確認雲端控制台／救援入口可用。唯一資料volume預設名lab-backend_lab_data，使用local driver；兩庫與journal共用檔案系統，管理和維護一律掛/data。Dockerfile準備空/data權限，顯式init建立資料，不在啟動時自動匯入／重建；刪容器保留volume，刪volume會失去資料。

映像中的自有程式／設定由root擁有，建置時明確設為目錄0755、檔案0644，避免COPY沿用主機0600／0700模式而使UID10001無法讀取；執行時仍非root、rootfs唯讀。這項處理不觸及/data、TLS私鑰或主機bind來源；初始化來源及HTTP設定片段等掛載檔案須另外確保UID10001可讀，不以root執行或chmod 777排錯。

public-web在Dockerfile切換USER後檢查Nginx設定及頁面程式的可讀性，使映像內權限問題在建置時失敗；此檢查不涵蓋運行時的HTTP／TLS／volume掛載。更換設定權限的映像需重建public-web並重建其容器，不能以重建public-api或restart舊容器替代。

tools沿用同一Python映像與非root限制，提供初始化、CSV、備份／恢復及監測。平時為臨時容器，只有明確watch才成為第五個常駐容器；可連兩內網以探測服務，不提供listener、不掛Docker socket。deploy/lab.sh統一Docker命令，沒有主機Python依賴。

Cloud DNS負責網域指向VPS，ADMIN_HOST則指定應用接受的管理主機名，不能從任意請求Host推導可信Origin。ADMIN_HOST與ADMIN_HTTPS_PORT共同生成管理Origin；對外端口限定1024–65535，預設27182，443保留給官網。HTTPS憑證以目錄唯讀掛載，SAN須符合主機名；網域／端口先檢查合法性，再nginx -t後啟動。

正式憑證方案採獨立ACME容器透過DNS API驗證，排程留在Docker內；DNS密鑰及ACME帳戶不掛給API或Nginx，不使用Docker socket或主機cron。續期需包含簽發、配對驗證及成組發布、Nginx容器內重新載入與線上憑證核對，失敗保留有效版本並告警。這套自動化尚未實作到Compose，不能把手動申請成功當成自動續期已啟用；example.org的AliDNS操作與待完成項目集中在[部署手冊](docs/OPERATIONS.md#https憑證與自動續期)。HTTPS保護瀏覽器至Nginx的傳輸，不替代權限或資料庫加密。

啟動gateway前須在Cloud安全組限制管理端口來源，且確認規則綁定實際VPS／網卡。程式已取消ADMIN_ALLOW_CIDRS，不偵測／代設雲端規則，也沒有本機IP篩選作為補救；Cloud若放行所有來源，登入頁就會對其可達。非標準端口不能阻止完整端口掃描，也不代替來源限制或登入保護。

目前只支援IPv4直接連VPS，不經CDN／額外代理，gateway以TCP來源限流，不信任客戶端轉發標頭。部署須核對真實來源，並從允許／不允許的外部網路驗證Cloud限制，不能只看UFW設定。來源IP不是個人身份，亦不取代帳密、Secure Cookie、Origin／CSRF。首次部署與設定更新步驟集中OPERATIONS，本次準備不等於已在伺服器部署。

public匿名唯讀、admin登入可寫，使用Argon2，無MD5、預設帳密、匿名註冊或恢復後門。保留HttpOnly／SameSite／HTTPS Secure Cookie、八小時記憶體Session、Origin／CSRF、每次帳戶狀態／角色檢查、停用／改密碼撤銷Session、兩個密碼運算併發額度及公開欄位白名單。身分檢查先於body解析，JSON拒絕未知欄位、重複鍵與非有限數字；body上限256KiB。UI隱藏不能取代後端授權。

憑證、Cookie、執行庫／journal、私人參考、匯出與備份不得進版控、映像或日誌。DB備份加密；靜態備份包含assets與白名單site.json，以版本、DB指紋及逐檔SHA256配對，拒絕穿越／符號連結並限制大小，恢復只建立新目錄。這是完整性核對，不是數位簽章。外部資產只列未驗證，不連外下載。公開檔案只接受SVG／PNG／JPEG／WebP／PDF，固定設定上限1MiB；首次安裝以新目錄切換且拒絕覆寫。CSV提交與檔案安裝是分開步驟，須離線完成全部檢查才啟動官網；不能宣稱跨DB與檔案的原子提交。目錄更換後重建public-web，使volume.subpath重新掛載。

/healthz只看程序，/readyz讀內容，兩者不經官網代理。tools經固定內網端點監測磁碟、備份失敗／過期／錯配及持續不可用，不使用外部HTTP代理。單次執行以JSON、stderr及退出碼告警；watch在容器內每60秒採樣，無主機排程。volume內備份仍須匯出異地保存；外部通知、自動備份與整機停止監測需另行配置。完整防爆破及正式安全驗收尚未完成。

## 交付邊界

public_frontend作為本專案普通資料夾，與後端一起提交；Compose使用獨立public-web容器。2026-11-19修正殘留gitlink，必要程式、Dockerfile、Nginx設定與目前契約已以普通檔案納入主Git索引；索引暫存不代表已提交或推送。dist/data及dist/assets不加入Git，初始化來源另行受控提供。lab.sh在建置官網前檢查必要檔案，check_basics亦檢查Git索引，避免只有本機有檔案而部署副本缺失。已刪除的內附backend副本不恢復，正式後端只在根目錄lab_backend。設計預覽及舊來源契約只供本機參考，部署白名單不包含它們。路由數量、包裝、五表及欄位沒有改動；契約若變更，仍須同步文件、OpenAPI、管理頁與生成型別。舊庫／備份不隱式遷移、覆寫或刪除。

本階段只允許語法／匯入、暫存目錄及程序內ASGI局部回歸；不啟動Docker、HTTP listener或部署。正式瀏覽器、安全、負載與故障恢復驗收延後，狀態只記實際執行結果。
