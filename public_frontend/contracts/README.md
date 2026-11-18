# 公開 API 契約

執行中的契約為 **7.1.0**，以主專案 [public-openapi.json](../../docs/public-openapi.json) 為準。`public-api.d.ts` 由 `scripts/sync_contracts.py` 生成，請勿手改。

[公開 API 文件](../../docs/PUBLIC_API.md) 列出四個 GET 的輸入、輸出與合成範例。頁面程式 `dist/api.js` 使用這些回應，不載入初始化 JSON。

本目錄保留的 `models.ts`、`example.ts`、`example.json`、`check.mjs` 是設計階段的舊來源格式參考，**不代表目前 API 或 SQLite 契約，也不加入映像**。首次來源轉換統一由根目錄 `scripts/prepare_initial_data.py` 處理。
