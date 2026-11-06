# Lab Website Backend Demo

小型研究團隊的內容管理後端，採用Python 3.14、FastAPI、Pydantic與SQLite，配備原生HTML／CSS／JavaScript管理介面。

## 功能

- 管理成員、校友、論文與資產連結，儲存成功即公開。
- 成員按姓氏排序；論文作者以成員ID關聯，個人頁自動取得相關論文。
- 論文按新增順序倒序，首頁顯示前三篇；支援同源PDF下載路徑。
- 管理員帳密登入、帳戶啟停、版本衝突檢查及操作審計。
- 離線CSV批量新增、缺檔檢查、資料庫與靜態檔配對備份、可用性告警。

每次內容請求均讀SQLite。30位成員／300篇論文是性能規劃基準，並非筆數上限。資產只管理網址與描述，實體檔案由官網提供。

## 結構

`lab_backend/`包含八個Python功能模組；`admin_frontend/admin/`為管理頁；`deploy/`提供gateway、public-api、admin-api三容器設定。兩API只走Docker內網，管理入口預設127.0.0.1:9443。

內容庫content.db保存assets、people、publications；管理庫admin.db保存users、audit，共5表46欄。公開API為4個GET，管理／登入為14個操作模板，按資源展開22個操作。

初始化只建立空庫，首位管理員由CLI建立，內容由管理介面或CSV匯入。`lab.json`是本地真實資料參考，不進Git、Docker或自動初始化流程。登入採Argon2與基本Session保護，完整正式登入安全另階段完善。

[CASINO-web](https://github.com/Axel1102/CASINO-web)以`public_frontend/`子模組引用，獨立部署；官網固定文字與靜態檔由該專案維護。

## 文件

- [架構、安全及閱讀程式](ARCHITECTURE.md)
- [API清單與管理對接](docs/API.md) · [公開前端對接](docs/PUBLIC_API.md)
- [SQLite欄位與關聯](docs/DATABASE.md)
- [安裝、部署與維護](docs/OPERATIONS.md)
- [Public OpenAPI](docs/public-openapi.json) · [Admin OpenAPI](docs/admin-openapi.json)
