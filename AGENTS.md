# 專案接手指引

修改前讀[架構](ARCHITECTURE.md)與[實作狀態](docs/IMPLEMENTATION_STATUS.md)；正式驗收延後，不能把未執行項目寫成通過。

- Python3.14／FastAPI／Pydantic／sqlite3；維持八個功能模組、admin_frontend/admin三個原生靜態檔，函式及規則保留易懂中文註釋，不新增重複多層架構。
- 契約7.1.0：public4個GET、admin/auth14模板／展開22操作。模型或接口變更須同步docs/API.md、PUBLIC_API.md、DATABASE.md、兩份OpenAPI、管理頁及生成型別；不任意增刪接口或改包裝。
- public匿名唯讀／admin帳密可寫。保留Argon2、基本Session、Origin/CSRF、每次帳戶狀態檢查及私有欄位白名單；不加回MD5、TOTP、MFA、近期驗證、UMPASS、預設帳密或匿名註冊。
- SQLite為業務內容唯一執行來源：content/content.db三張內容表、private/admin.db的users/audit，共五表。public只掛內容唯讀，每次請求讀新快照；CSV只交換，init只建空庫。內容以id識別，無slug；people無sourceUrl／affiliation。Store.read只讀明確指定的表，反查須同次指定people與publications。
- 根目錄lab.json是真實私人參考：不讀取、不複製、不提交Git、不進Docker。使用者指定public_frontend/dist/data/lab.json及dist/assets為首次來源，僅seed明確掛載唯讀轉成CSV與公開檔案，原始JSON不入Git，兩者不入映像、不自動匯入；檢查只用合成資料。憑證、Cookie、執行庫／journal、匯出與備份不提交或記日誌。
- 所有內容儲存即公開；無草稿／發布／封存。30人／300篇是估算，不設人數／論文硬上限。每表解碼及CSV各32MiB、單筆業務JSON252KiB，寫入與離線預覽／恢復均須驗證。
- people.alumniSince為YYYY-MM或null，只有memberships非空且全部isCurrent=false的校友可填，其餘必須null；成員列表／個人詳情均回傳，不自動改身份或靜默清除日期。尚未正式部署，本版僅建新SQLite10空庫，不提供升級指令或操作既有資料檔。
- people按姓／名／ID排序，照片直接存portrait；英文名englishName可留空；name/publicationIds在管理回應唯讀。唯一可寫關聯為authors[].personId，不依同名配對、不雙向存清單。論文按入庫倒序，首頁前三篇；編輯不移位，內部／備份保持rowid升序，不自行VACUUM／重建改順序。
- 寫交易維持ATTACH＋BEGIN IMMEDIATE＋DELETE journal＋FULL synchronous；內容及審計一起提交，鎖內重查權限／版本／關聯，密碼雜湊不持寫鎖。SQL值參數綁定、表名白名單。
- 公開固定快照後有內容庫journal即503，由管理程序在原環境／原路徑恢復，不刪日誌。只支援單台Linux本機檔案系統、單一admin worker；離線寫入須停管理服務。
- CSV先預覽再--apply，只新增、既有ID跳過、錯誤整批拒絕；不提供瀏覽器上傳或一般JSON整庫匯入。SQLite10／LABSQL10不兼容舊版，不隱式遷移、覆寫或刪除資料。
- 全Docker部署，VPS只需既有Docker／Compose及可用維護控制台，SSH可不公開，關閉前須驗證救援入口；不安裝主機Python／venv／systemd排程。SQLite用同一local named volume，管理／維護掛/data，public-api只掛content子目錄唯讀，gateway只掛public子目錄唯讀；init顯式建立空庫，勿刪volume或journal。
- 三個常駐容器：gateway共用Nginx、public-api、admin-api；兩API無主機端口且分別只加入frontend_net／admin_net。gateway連兩內網但只掛public子目錄唯讀，公開listener只代理四個GET、不得轉送管理路由／Cookie，管理listener保留帳密／Session／CSRF。tools按需或watch常駐，不開listener、不掛Docker socket。共用Nginx是共用信任及故障邊界，不宣稱兩個入口仍有程序隔離。
- 不依賴.env、ADMIN_HOST或LAB_ADMIN_ORIGIN；lab.sh固定lab-backend專案及--env-file /dev/null，保留lab-backend_lab_data與lab-content。正式443→8443、管理27182→27182；顯式http模式80→8080、管理27181→27181。舊.env不讀取或自動刪除，不因精簡改用新volume。管理無固定Host白名單，Origin仍比對部署協定＋實際Host含端口，非登入寫入另需CSRF；不信任轉發Host／IP／Proto。
- HTTP測試LAB_HTTP_TEST=true，使用lab_session_http_test且無Secure；正式false、HTTPS與__Host-lab_session Secure。保留帳密、HttpOnly／SameSite、帳戶狀態及限流；LAB_DEV僅loopback、不與HTTP測試同開。HTTP無憑證掛載、不發HSTS／不跳轉HTTPS，Cloud仍限制管理來源；不加回程式IP清單，換端口不代替權限。兩份正式TLS由gateway唯讀掛載；healthz／readyz僅內部，備份以版本與SHA256配對。
- Git追蹤（2026-11-19修正）：public_frontend舊gitlink已移除，必要程式／建置設定以普通檔案納入索引；不恢復子模組。dist/data與dist/assets只留初始化來源，不納入Git。索引暫存不代表已提交或推送；部署前用lab.sh check-web核對程式完整性。
- 全專案排除規則只放根.gitignore／.dockerignore，不新增子目錄或Dockerfile專用排除檔。兩個映像均以專案根目錄為build context，Docker先預設排除全部、逐檔放行必要程式，再由各Dockerfile明確COPY；新增建置依賴須同步根白名單。
- public_frontend是本專案的普通資料夾，由主Git倉庫管理，不再連接CASINO-web子模組；前後端程式及契約可在同一次提交更新，gateway一併打包官網五個頁面程式檔及管理三檔。固定設定存/data/public/site.json、實體檔存/data/public/assets；兩者由維護工具管理並配對備份，原始快照不能對外提供。型別由scripts/sync_contracts.py生成，不手改；不聲稱工作區修改已提交／部署。正式後端只在根目錄lab_backend，不恢復已刪除的內附backend副本。
- README只介紹專案，不放測試內容。安全與程式閱讀集中ARCHITECTURE，部署與維護集中OPERATIONS；完整欄位只在DATABASE維護。
- 允許語法／匯入及暫存目錄／程序內ASGI回歸；不啟動Docker、HTTP listener或部署。不要為精簡刪除保護機制或既有回歸覆蓋。
