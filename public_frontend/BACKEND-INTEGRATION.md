# 公開網站與後端對接

公開頁固定使用 API，沒有靜態快照模式。正式後端位於根目錄 `lab_backend/`；前後端由同一倉庫維護，Compose 分成獨立容器。

| 頁面 | 瀏覽器同源 GET | 後端路由 |
|---|---|---|
| 首頁／相簿 | `/api/home` | `/api/v1/public/home` |
| 成員列表 | `/api/people` | `/api/v1/public/people` |
| 個人詳情 | `/api/people/{id}` | `/api/v1/public/people/{id}` |
| 論文列表／站內詳情 | `/api/publications` | `/api/v1/public/publications` |

`dist/api.js` 只調整展示形狀，保留 API 的人名排序、作者署名和論文順序。API 失敗顯示重試；不改讀 `lab.json`，也不以空列表冒充成功。PDF 使用同源 `download` 連結。

啟動先讀 `/data/site.json`：固定品牌、介紹與聯絡頁內容，不包含 people/publications。首頁全部合照仍從 home API 取得。此檔由初始化工具的白名單生成，正式來源是 volume 的 `/data/public/site.json`；與圖片一起備份。沒有新增設定／新聞 API。

共用`gateway`由[nginx.conf.template](../deploy/nginx.conf.template)的公開listener提供HTTPS443，代理四個GET至Docker內網lab-public-api:8000。只掛volume的public子目錄唯讀，不掛SQLite或private；公開端口不能取得管理頁或管理API。兩API仍分別連獨立內網，gateway是共用入口；原始JSON、CSV及備份不會公開。

內容測試可使用 `sh deploy/lab.sh http ...`，官網入口改為 HTTP 80，無需憑證。此模式共用全部頁面、同源 API 與檔案路徑，前端不需要切換 API 網址或增加 HTTP 專用程式碼；啟動步驟見部署手冊。

`contracts/public-api.d.ts` 由 `scripts/sync_contracts.py` 生成，不手改。完整格式與合成範例見 [PUBLIC_API](../docs/PUBLIC_API.md)；初始化、資產位置與部署命令集中 [OPERATIONS](../docs/OPERATIONS.md)。
