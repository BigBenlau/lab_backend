# 官網頁面與資料來源

| 頁面 | 顯示內容 | 瀏覽器資料來源 |
|---|---|---|
| 首頁及相簿 | 首頁合照、按入庫倒序的前三篇論文 | `GET /api/home` |
| 成員列表 | 現任成員與校友、姓名、照片、可填的校友年月 | `GET /api/people` |
| 成員詳情 | 個人介紹、公開聯絡內容及以作者 ID 關聯的論文 | `GET /api/people/{id}` |
| 論文 | 完整清單、作者、來源連結及可下載的 PDF | `GET /api/publications` |
| 聯絡頁與共用品牌 | 經內容管理員確認的固定文字及圖像 | `GET /data/site.json` 及指定資產網址 |

前端從同一網站入口取得資料。修改頁面程式可從 `dist/lab.js` 和 `dist/api.js` 開始；資料契約及範例見[對接說明](BACKEND-INTEGRATION.md)與[公開 API](../docs/PUBLIC_API.md)。
