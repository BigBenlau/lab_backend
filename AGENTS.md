# 專案接手指引

修改前讀[架構](ARCHITECTURE.md)與[實作狀態](docs/IMPLEMENTATION_STATUS.md)；正式驗收延後，不能把未執行項目寫成通過。

- Python3.14／FastAPI／Pydantic／sqlite3；維持八個功能模組、admin_frontend/admin三個原生靜態檔，函式及規則保留易懂中文註釋，不新增重複多層架構。
- 契約7.0.0：public4個GET、admin/auth14模板／展開22操作。模型或接口變更須同步docs/API.md、PUBLIC_API.md、DATABASE.md、兩份OpenAPI、管理頁及生成型別；不任意增刪接口或改包裝。
- public匿名唯讀／admin帳密可寫。保留Argon2、基本Session、Origin/CSRF、每次帳戶狀態檢查及私有欄位白名單；不加回MD5、TOTP、MFA、近期驗證、UMPASS、預設帳密或匿名註冊。
- SQLite唯一執行來源：content/content.db三張內容表、private/admin.db的users/audit，共五表。public只掛內容唯讀，每次請求讀新快照；CSV只交換，init只建空庫。內容以id識別，無slug；people無sourceUrl／affiliation。Store.read只讀明確指定的表，反查須同次指定people與publications。
- lab.json是真實私人參考：不讀取、不複製、不提交Git、不進Docker；檢查只用合成資料。憑證、Cookie、執行庫／journal、匯出與備份不提交或記日誌。
- 所有內容儲存即公開；無草稿／發布／封存。30人／300篇是估算，不設人數／論文硬上限。每表解碼及CSV各32MiB、單筆業務JSON252KiB，寫入與離線預覽／恢復均須驗證。
- people按姓／名／ID排序，照片直接存portrait；英文名englishName可留空；name/publicationIds在管理回應唯讀。唯一可寫關聯為authors[].personId，不依同名配對、不雙向存清單。論文按入庫倒序，首頁前三篇；編輯不移位，內部／備份保持rowid升序，不自行VACUUM／重建改順序。
- 寫交易維持ATTACH＋BEGIN IMMEDIATE＋DELETE journal＋FULL synchronous；內容及審計一起提交，鎖內重查權限／版本／關聯，密碼雜湊不持寫鎖。SQL值參數綁定、表名白名單。
- 公開固定快照後有內容庫journal即503，由管理程序在原環境／原路徑恢復，不刪日誌。只支援單台Linux本機檔案系統、單一admin worker；離線寫入須停管理服務。
- CSV先預覽再--apply，只新增、既有ID跳過、錯誤整批拒絕；不提供瀏覽器上傳或一般JSON整庫匯入。SQLite9／LABSQL9不兼容舊版，不隱式遷移、覆寫或刪除資料。
- 兩API無主機端口；gateway預設127.0.0.1:9443，無VPN前提，不加入lab-content；公開前端不加入管理網路。healthz/readyz僅內部探測，DB／靜態備份以版本與SHA256配對。
- public_frontend是CASINO-web的Git子模組，固定提交、追蹤main、獨立部署。修改先讀其指引，不當普通目錄提交或覆寫。型別由scripts/sync_contracts.py生成，不手改；不聲稱工作區修改已提交／部署。
- README只介紹專案，不放測試內容。安全與程式閱讀集中ARCHITECTURE，部署與維護集中OPERATIONS；完整欄位只在DATABASE維護。
- 允許語法／匯入及暫存目錄／程序內ASGI回歸；不啟動Docker、HTTP listener或部署。不要為精簡刪除保護機制或既有回歸覆蓋。
