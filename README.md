# Lab Website Backend Demo

小型研究團隊的內容管理後端，採用Python 3.14、FastAPI、Pydantic與SQLite，配備原生HTML／CSS／JavaScript管理介面。

## 功能

- 管理成員、校友、論文與資產連結，儲存成功即公開。
- 成員按姓氏排序；論文作者以成員ID關聯，個人頁自動取得相關論文。
- 論文按新增順序倒序，首頁顯示前三篇；支援同源PDF下載路徑。
- 管理員帳密登入、帳戶啟停、版本衝突檢查及操作審計。
- 離線CSV批量新增、缺檔檢查、資料庫與靜態檔配對備份、可用性告警。

每次內容請求均讀SQLite。30位成員／300篇論文是性能規劃基準，並非筆數上限。資產只管理網址與描述，實體檔案保存在後端管理的 volume，由官網 Nginx 唯讀提供。

## 結構

`lab_backend/`包含八個Python功能模組；`admin_frontend/admin/`為管理頁，`public_frontend/`為官網程式。`deploy/`提供gateway、public-api、admin-api三個常駐容器：共用Nginx提供官網443、管理頁27182及各自API代理，兩API保留獨立內網與資料權限，無主機端口。管理來源由Cloud防火牆限制，後台以帳密登入。部署採固定預設值，不需.env或指定ADMIN_HOST。

SQLite保存在一個Docker named volume中：內容庫content.db保存assets、people、publications；管理庫admin.db保存users、audit，共5表47欄。公開API為4個GET，管理／登入為14個操作模板，按資源展開22個操作。

Python與維護工具全部在容器內，VPS只需已有Docker、Compose及可用維護控制台，SSH不必公開。`sh deploy/lab.sh`統一建置、初始化與啟動；tools維護容器按需執行，亦可選擇常駐監測，不需主機Python或systemd。

初始化只建立空庫，首位管理員由CLI建立，內容由管理介面或CSV匯入。可明確執行seed，將`public_frontend/dist/data`轉成CSV、將`dist/assets`複製為首次資產，再預覽匯入。根目錄`lab.json`只作私人參考；兩份原始JSON均不進Git或映像，也不在服務啟動時讀取。登入採Argon2與基本Session保護，完整正式登入安全另階段完善。

公開網站程式碼位於`public_frontend/`，是本倉庫的普通資料夾，可直接修改並與後端一起提交。gateway映像只加入官網五個頁面程式檔及管理頁程式；固定設定放volume的`public/site.json`，圖片／PDF放`public/assets/`，由後端維護工具管理。重建前端不會覆寫資料，gateway僅掛公開資料子目錄唯讀。

## 文件

- [架構、安全及閱讀程式](ARCHITECTURE.md)
- [API清單與管理對接](docs/API.md) · [公開前端對接](docs/PUBLIC_API.md)
- [SQLite欄位與關聯](docs/DATABASE.md)
- [安裝、部署與維護](docs/OPERATIONS.md)
- [Public OpenAPI](docs/public-openapi.json) · [Admin OpenAPI](docs/admin-openapi.json)
