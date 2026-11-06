# 實作與檢查紀錄

2026-11-06。依前端展示需求再精簡後端；契約7.0.0、SQLite9／LABSQL9。公開4個GET、管理14模板／22操作不變；2庫5表由50欄減至46欄。正式驗收延後。

## 本輪調整

- people移除slug／sourceUrl／affiliation，publications移除slug；同步管理模型、表單、CSV模板及資料字典。保留論文sourceUrl，因站內論文詳情仍使用。
- 移除只為上述欄位存在的Content基底、slug唯一驗證與索引設定。不同ID可同名，作者關聯始終使用personId。
- Store.read只讀明確指定的表，不自動附帶論文及反查清單。公開成員列表只讀people；個人詳情與管理成員列表明確在同一快照讀people／publications，沿用共用作者索引。
- 保留englishName可空、合照用途、姓名及論文排序、管理歷史／版本、身份與審計。沒有新增核心模組、接口、資料表或依賴。
- 公開回應形狀與6.0.0相同；只更新OpenAPI修訂號與生成型別標記。前端頁面程式／樣式未改，不要求重新設計或改接線。

## 實際執行

Python3.14.2／SQLite3.51.2；資料檢查只在TemporaryDirectory，HTTP使用程序內ASGI，沒有listener。未讀取私人lab.json。

| 檢查 | 結果 |
|---|---|
| compileall（lab_backend、scripts、tests） | 通過 |
| scripts/check_basics.py | 匯入、OpenAPI／型別、五表46欄文件、Compose邊界及JS／shell語法通過 |
| Python unittest全套，ResourceWarning視為錯誤 | 54項通過 |
| node --test tests/admin_frontend.test.cjs tests/public_frontend.test.cjs | 21項通過，DOM／fetch替身 |
| 實際建庫PRAGMA／資料字典 | assets 6、people 14、publications 11、users 9、audit 6欄；唯一約束核對通過 |
| 暫存SQLite重新產生四個完整sample | 實際ASGI回應與原sample逐位元相同，Pydantic／排序／合照／關聯核對通過 |
| 公開OpenAPI及前端執行程式指紋 | OpenAPI除版本號外相同；public_frontend/dist/*.js均未變 |
| Markdown相對連結 | 有效 |

新增回歸確認服務啟動後論文資料損壞不會拖垮只讀people的成員列表；需要論文的頁面仍回503。既有回歸補上移除欄位的422拒絕、管理表單欄位、不同ID可同名，以及關聯修改後管理反查一致。CSV匯入、備份／恢復及交易／權限回歸沿用全套檢查。

## 未執行與相容性

未啟動Docker、HTTP listener或部署，未提交／推送子模組，未建立或修改專案執行資料庫。前輪子模組待提交改動保留；本輪只同步其文件與生成型別，不能聲稱GitHub或正式網站已更新。

SQLite9／LABSQL9不相容舊庫／備份，不自動遷移、覆寫或刪除；舊CSV需要依新模板移除欄位。對既有資料的處理界線見OPERATIONS，不能直接修改user_version冒充新版。

所有sample仍為合成資料，不附實體照片／PDF。真實瀏覽器、檔案可用性、Linux／TLS／Nginx／網路、正式登入安全、負載、斷電及異機恢復未驗收；本輪未做漏洞掃描、排程或外部告警部署。
