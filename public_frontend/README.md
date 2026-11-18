# CASINO Lab 公開網站

原生 HTML／CSS／JavaScript 官網，提供首頁相簿、近期論文、成員列表、個人詳情、論文篩選及聯絡頁。此目錄由主專案 Git 管理，可直接修改，沒有外部子模組連接。

| 位置 | 用途 |
|---|---|
| `dist/lab.html` | 官網入口；映像內以 `index.html` 提供 |
| `dist/lab.js`、`dist/api.js` | 頁面與四個公開 API 的展示轉換 |
| `dist/lab.css`、`dist/gold.css` | 現有版面與樣式 |
| `../deploy/Dockerfile.gateway`、`../deploy/nginx.conf.template` | 官網 HTTPS、同源 API 代理及公開檔案服務 |
| `contracts/public-api.d.ts` | 由後端 OpenAPI 產生的型別 |
| `dist/data/`、`dist/assets/` | 僅供首次初始化的本地來源，不加入映像 |

執行時的成員、論文及首頁合照均由 SQLite API 提供；固定頁面設定讀 `/data/site.json`，圖片與 PDF 讀 `/assets/`。兩者來自後端管理的 Docker volume，更新程式不會重新載入初始內容。原始 `lab.json` 不公開、不加入 Git。

Compose 的共用 `gateway` 同時提供官網及管理頁；官網只打包上述五個頁面檔案，不包含設計方案、預覽、資料或圖片。`dist/index.html` 是保留的設計展示頁，不是部署入口。

Git與Docker排除規則統一在專案根目錄的`.gitignore`與`.dockerignore`。共用gateway的Dockerfile以整個專案根目錄為建置來源，由根白名單放行所需程式；使用根目錄的`sh deploy/lab.sh`建置。

[API 對接](BACKEND-INTEGRATION.md) · [部署與資料維護](../docs/OPERATIONS.md) · [架構](../ARCHITECTURE.md)
