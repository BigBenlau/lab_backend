# SQLite資料字典（格式9）

**2個檔案、5張表、46個欄位**；根目錄由LAB_STORAGE指定。public-api只掛內容目錄唯讀，admin-api可寫兩庫；無獨立DB容器。

| 檔案 | 表 | 欄位數 |
|---|---|---:|
| content/content.db | assets／people／publications | 6／14／11 |
| private/admin.db | users／audit | 9／6 |

全表STRICT；id為TEXT主鍵，users.login唯一，無SQL外鍵、中介表或Session表。布林為INTEGER 0/1，物件／陣列為JSON TEXT，可空值為SQL NULL；預設值由Python提供，無SQL DEFAULT。其餘型別／關聯由Pydantic與交易內驗證。

Id為1–200字元，英數開頭，其餘英數或_-；Name為1–500字元，Short最多500，一般字串最多50000。URL最多2048，允許無帳密的http/https，名為url的欄位另允許./assets/、/assets/；禁止控制字元、反斜線及相對路徑穿越。時間需含時區；API布林只接受true/false。

本版移除people的slug／sourceUrl／affiliation及publications.slug。ID已足夠識別與連結；論文sourceUrl仍用於前端來源連結。管理API／CSV不再接受被移除欄位。

## 完整欄位

created／updated／_version由系統維護，不屬於可寫data；公開不輸出歷史時間。people.name與publicationIds也不存SQL／CSV。PUT完整替換，漏填可選欄位會重設預設。

### assets

| column | SQL 型別／約束 | 可空 | API／Python型別與限制 | 預設／來源 | 用途 |
|---|---|---|---|---|---|
| `created` | TEXT（JSON） | 否 | ChangeStamp {at:string,by:string} | `系統產生` | 建立時間及操作者；由伺服器維護，管理回應外層同名欄位 |
| `updated` | TEXT（JSON） | 否 | ChangeStamp {at:string,by:string} | `系統產生` | 最後更新時間及操作者；由伺服器維護，管理回應外層同名欄位 |
| `id` | TEXT PK | 否 | string（字元≥1,字元≤200） | `必填` | 穩定識別鍵；同表唯一，建立後不可改 |
| `url` | TEXT | 否 | string（字元≥1,字元≤2048） | `必填` | 資產網址／資產相對路徑；不代管實體檔案 |
| `metadata` | TEXT（JSON） | 否 | AssetMetadata {mimeType,alt,caption,credit,purpose} | `圖片預設描述物件` | 集中保存資產描述，不含width／height |
| `_version` | INTEGER | 否 | integer ≥1 | `1` | 內容編輯版本；初始1，每次成功 PUT 加一；回應為外層 version |

### people

| column | SQL 型別／約束 | 可空 | API／Python型別與限制 | 預設／來源 | 用途 |
|---|---|---|---|---|---|
| `created` | TEXT（JSON） | 否 | ChangeStamp {at:string,by:string} | `系統產生` | 建立時間及操作者；由伺服器維護，管理回應外層同名欄位 |
| `updated` | TEXT（JSON） | 否 | ChangeStamp {at:string,by:string} | `系統產生` | 最後更新時間及操作者；由伺服器維護，管理回應外層同名欄位 |
| `id` | TEXT PK | 否 | string（字元≥1,字元≤200） | `必填` | 穩定識別鍵；同表唯一，建立後不可改 |
| `familyName` | TEXT | 否 | string（字元≥1,字元≤500） | `必填` | 姓氏，必填；排序的主要鍵 |
| `givenName` | TEXT | 否 | string（字元≥1,字元≤500） | `必填` | 名字，必填；同姓時用於排序 |
| `englishName` | TEXT | 是 | string（字元≤500） 或 null | `null` | 英文名；null或空字串表示不顯示 |
| `title` | TEXT | 否 | string（字元≤500） | `""` | 成員職銜（可空字串） |
| `portrait` | TEXT（JSON） | 是 | Photo 或 null | `null` | 個人照片物件，完整參數存於本列JSON；null表示未設定，不引用assets |
| `bioMarkdown` | TEXT | 否 | string（字元≤50000） | `""` | 成員介紹 Markdown |
| `interests` | TEXT（JSON） | 否 | string（字元≤500）[]（0–100項） | `[]` | 研究興趣文字陣列 |
| `memberships` | TEXT（JSON） | 否 | Membership[]（0–30項） | `[]` | 身份物件陣列，含在任標記；不是帳戶權限 |
| `publicEmail` | TEXT | 是 | string（字元≤500） 或 null | `null` | 公開 Email；空值不生成聯絡連結 |
| `links` | TEXT（JSON） | 否 | Link[]（0–30項） | `[]` | 連結物件陣列，保留原順序 |
| `_version` | INTEGER | 否 | integer ≥1 | `1` | 內容編輯版本；初始1，每次成功 PUT 加一；回應為外層 version |

### publications

| column | SQL 型別／約束 | 可空 | API／Python型別與限制 | 預設／來源 | 用途 |
|---|---|---|---|---|---|
| `created` | TEXT（JSON） | 否 | ChangeStamp {at:string,by:string} | `系統產生` | 建立時間及操作者；由伺服器維護，管理回應外層同名欄位 |
| `updated` | TEXT（JSON） | 否 | ChangeStamp {at:string,by:string} | `系統產生` | 最後更新時間及操作者；由伺服器維護，管理回應外層同名欄位 |
| `id` | TEXT PK | 否 | string（字元≥1,字元≤200） | `必填` | 穩定識別鍵；同表唯一，建立後不可改 |
| `sourceUrl` | TEXT | 否 | string（字元≤50000） | `""` | 內容來源網址；空字串表示未填 |
| `title` | TEXT | 否 | string（字元≥1,字元≤500） | `必填` | 標題（必填） |
| `authors` | TEXT（JSON） | 否 | Author[]（1–100項） | `必填` | 作者物件陣列，保留署名順序；personId 可關聯 people.id |
| `year` | INTEGER | 否 | integer（≥1800,≤2200） | `必填` | 發表年份 |
| `type` | TEXT | 否 | conference/journal/workshop | `"conference"` | 論文類別 |
| `venue` | TEXT（JSON） | 否 | Venue | `{"name": "", "shortName": ""}` | 會議／期刊物件 |
| `pdfPath` | TEXT | 是 | string（1–2048）或null | `null` | 官網同源/assets/下的PDF路徑，供前端下載，不是主機檔案路徑 |
| `_version` | INTEGER | 否 | integer ≥1 | `1` | 內容編輯版本；初始1，每次成功 PUT 加一；回應為外層 version |

### users

| column | SQL 型別／約束 | 可空 | API／Python型別與限制 | 預設／來源 | 用途 |
|---|---|---|---|---|---|
| `id` | TEXT PK | 否 | string（字元≥1,字元≤200） | `系統UUID` | 穩定識別鍵；同表唯一，建立後不可改 |
| `displayName` | TEXT | 否 | string（字元≥1,字元≤500） | `必填` | 管理員顯示名稱；不同於研究成員姓名 |
| `login` | TEXT UNIQUE | 否 | string（字元≥3,字元≤80） | `必填` | 唯一登入名稱；小寫英數開頭，3–80字元，其餘可 ._- |
| `role` | TEXT CHECK admin | 否 | "admin" | `"admin"` | 固定 admin；public 不是 users 的一筆帳戶 |
| `active` | INTEGER CHECK 0/1 | 否 | boolean | `true` | 是否允許登入及操作 |
| `passwordHash` | TEXT | 否 | string（字元≤50000） | `必填` | Argon2 編碼雜湊，包含演算法參數及鹽；不回傳前端 |
| `authVersion` | INTEGER | 否 | integer（≥1） | `1` | 授權版本；憑證／狀態改動加一，使舊 Session 失效；不回傳前端 |
| `version` | INTEGER | 否 | integer（≥1） | `1` | 帳戶編輯版本；供 PATCH 防止覆寫 |
| `createdAt` | TEXT | 否 | string（字元≤50000） | `系統UTC時間` | 建立時間；API 新增時由伺服器產生，後續保留 |

### audit

| column | SQL 型別／約束 | 可空 | API／Python型別與限制 | 預設／來源 | 用途 |
|---|---|---|---|---|---|
| `id` | TEXT PK | 否 | string（字元≥1,字元≤200） | `系統UUID` | 穩定識別鍵；同表唯一，建立後不可改 |
| `at` | TEXT | 否 | string（字元≤50000） | `必填` | 審計時間，含時區 |
| `actor` | TEXT | 否 | string（字元≤50000） | `必填` | 操作者識別；帳戶 ID 或 CLI 標記，不是 SQL 外鍵 |
| `action` | TEXT | 否 | string（字元≤500） | `必填` | 事件名稱，如 content.updated |
| `target` | TEXT | 否 | string（字元≤500） | `""` | 資源／ID 或維護摘要，可空字串，不是 SQL 外鍵 |
| `result` | TEXT | 否 | success/denied | `必填` | 操作結果；目前線上流程主要記錄 success |

## 歷史與JSON物件

created、updated各為單一JSON物件，例如`{"at":"2026-11-06T10:00:00Z","by":"admin-001"}`。at需含時區，by為1–500字元，不接受缺欄或未知欄位。新增時兩者相同，修改只換updated；管理回應在外層展示，公開不輸出歷史。普通CSV不接受歷史，系統按當次時間／CLI操作者產生；備份保留完整值。users.createdAt與audit事件欄位獨立保留。

| 物件 | 欄位與規則 |
|---|---|
| Photo（people.portrait） | url必填1–2048；metadata為ImageMetadata，省略則使用預設描述物件；沒有width／height |
| ImageMetadata | mimeType預設image/png，允許image/svg+xml、image/png、image/jpeg、image/webp；alt/credit預設空字串、最多500；caption字串或null，預設null |
| AssetMetadata（assets.metadata） | 同ImageMetadata，mimeType另允許application/pdf；purpose為group_photo／other（預設other），group_photo限圖片，首頁只回合照；未知欄位（包括width／height）拒絕 |
| Author | personId為已存在people.id或null（預設null）；name必填1–500；不保留貢獻占位欄位 |
| Venue | name字串預設空字串；shortName最多500，預設空字串 |
| Membership | role必填1–500；isCurrent預設true。這是成員身份，不是系統權限 |
| Link | label必填1–500；url必填1–2048，遵守上述URL規則 |

照片直接存people.portrait，無id、不引用assets；null表示未設定，提供物件則必須有合法url。API已解析JSON，不需前端再次JSON.parse。

## 姓名、關聯與順序

name固定為givenName＋空格＋familyName；englishName獨立保存英文名，可為null或空字串；不參與排序、組合name或論文署名。公開頁面可顯示為name (englishName)。people列表及管理選項按NFKC正規化、casefold、去首尾空白後的姓、名，再按原始ID排序；中文不猜拼音。

唯一可寫關聯是`publications.authors[].personId → people.id`。內部作者指定ID，外部作者為null；署名name獨立保存，成員改名不改論文署名，不按同名自動匹配。缺失ID或刪除被引用成員整筆拒絕，無級聯刪除。

publicationIds只在管理回應展示，公開詳情直接回publications而不重複回ID陣列。兩者在同一快照反查，約O(成員＋作者總數)，同篇去重，不快取或另存清單。users與people不自動綁定；created.by／updated.by／audit.actor為操作者文字，非SQL外鍵。

論文內部rowid升序，公開／管理列表、個人論文及publicationIds按入庫倒序；首頁取前三篇。UPSERT保留位置，改標題／年份不移位。CSV依列序新增，最後新增列排最前；既有ID跳過。備份同序保存／恢復，不自行VACUUM、重建或重排表。

## PDF與容量

pdfPath為null或同源`/assets/papers/paper-1.pdf`。長度1–2048，目錄僅英數／底線／連字號，檔名以這些字元開頭、其餘可加點，PDF副檔名不分大小寫。拒絕外站、./assets/、空字串、空格、百分號、query、fragment、反斜線及..目錄。官網提供實體檔案；API不讀主機路徑、不上傳或代理下載。

30人／300篇是估算，不限兩者總筆數；assets/users各1000筆保護。每表解碼JSON與備份CSV各≤32MiB；單筆業務JSON≤252KiB，預留4KiB給HTTP包裝。API、CSV預覽／套用、初始化、恢復共用容量驗證，超限不提交。audit可分頁及串流封存，完整備份超限時先封存歷史。

SQLite9／LABSQL9不隱式遷移舊資料。init只建空庫，lab.json僅為私人參考；一般CSV只新增三類內容，帳戶／審計只在完整備份內。編碼、缺檔檢查及備份步驟見[OPERATIONS](OPERATIONS.md)。
