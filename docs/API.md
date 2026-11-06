# API v1：清單與管理對接

契約修訂7.0.0，前綴`/api/v1`。資料形狀以[公開OpenAPI](public-openapi.json)、[管理OpenAPI](admin-openapi.json)為準；業務欄位與限制見[DATABASE](DATABASE.md)，公開前端用法見[PUBLIC_API](PUBLIC_API.md)。

## 數量與共通規則

| 入口 | 方法＋路由模板 | 路徑模板 | 按資源展開操作 |
|---|---:|---:|---:|
| public | 4 | 4 | 4 |
| admin/auth | 14 | 11 | 22 |
| 合計 | 18 | 15 | 26 |

內部healthz／readyz不計入業務API。每次內容請求讀SQLite，`Cache-Control: no-store`；儲存即公開。JSON嚴格型別、欄位區分大小寫，拒絕未知欄位、重複鍵、NaN／Infinity；保留null、空字串、空陣列的區別。body上限256KiB，有body的管理請求須`Content-Type: application/json`。

Id為1–200字元，英數開頭，其餘英數／底線／連字號。成員按姓／名／ID排序，論文按新增入庫倒序，資產依入庫順序，作者保留署名順序。只有audit分頁；未知query目前忽略。容量超限在提交前拒絕，具體限制見資料字典。

## Public：4個GET

基址`http://lab-public-api:8000`只供Docker內網；瀏覽器使用官網同源代理。無帳戶、Cookie、token或CSRF，直接回物件／陣列，無data/items包裝。

| 方法與路徑 | 輸入 | 200輸出 |
|---|---|---|
| GET /api/v1/public/home | 無 | {assets:Asset[],recentPublications:PublicationSummary[],counts:{publications:number}} |
| GET /api/v1/public/people | 無 | PersonSummary[] |
| GET /api/v1/public/people/{person_id} | path person_id:Id | {person:PublicPerson,publications:PublicPublication[]} |
| GET /api/v1/public/publications | 無 | PublicPublication[] |

四者無body／query參數。首頁assets只含metadata.purpose=group_photo的合照，取入庫倒序前三篇摘要；詳情按ID匹配，不按slug，找不到404，非法ID422。本版公開回應與6.0.0相同；精簡的是管理輸入與SQLite欄位。完整輸出欄位及PDF對接見[PUBLIC_API](PUBLIC_API.md)。不提供/lab、單篇論文、獨立assets或已取消功能接口。

## Admin/auth：14個操作模板

管理頁與gateway同源，上游admin-api不開主機port。resource限assets、people、publications：CRUD各4操作，共12；另有schema/references 2、auth 4、users 3、audit 1，合計22。

| 方法與路徑 | 輸入 | 成功輸出 | 管理頁入口 |
|---|---|---|---|---|
| POST /api/v1/auth/login | {login,password} | 200 {user,csrf}＋Cookie | 登入 |
| GET /api/v1/auth/session | 無 | 200 {user,csrf} | 恢復登入 |
| POST /api/v1/auth/logout | 無body | 200 {ok:true} | 登出 |
| POST /api/v1/auth/password | {currentPassword,newPassword} | 200 {ok:true,signInRequired:true} | 改密碼 |
| GET /api/v1/admin/schema | 無 | 200 {assets,people,publications}，各值為JSON Schema | 內容表單 |
| GET /api/v1/admin/references | 無 | 200 {assets,people,publications}，各值為[{id,label}] | 關聯選項／總覽 |
| GET /api/v1/admin/content/{resource} | path resource | 200 {items:ContentRecord[]} | 內容列表 |
| POST /api/v1/admin/content/{resource} | resource、{data,version:null} | 201 ContentRecord | 新增 |
| PUT /api/v1/admin/content/{resource}/{record_id} | resource、record_id、{data,version} | 200 ContentRecord | 編輯 |
| DELETE /api/v1/admin/content/{resource}/{record_id} | resource、record_id、query version | 200 {ok:true} | 刪除 |
| GET /api/v1/admin/users | 無 | 200 {items:UserInfo[]} | 帳戶列表 |
| POST /api/v1/admin/users | {displayName,login,password} | 201 UserInfo | 新增管理員 |
| PATCH /api/v1/admin/users/{user_id} | user_id、{active,version} | 200 UserInfo | 啟用／停用 |
| GET /api/v1/admin/audit | offset預設0、limit預設100（1–200） | 200 {items,total,offset,warnings} | 審計翻頁 |

### 登入、Header與帳戶模型

除登入外皆需Cookie。所有非GET請求（含登入）帶精確`Origin=LAB_ADMIN_ORIGIN`；除登入外另帶`X-CSRF-Token`，取自login/session回應。正式Cookie為`__Host-lab_session`，HttpOnly／Secure／SameSite=Strict／Path=/；loopback開發模式改用`lab_session_dev`、不設Secure。fetch使用同源credentials。

Session固定八小時、重啟失效、不滑動延期。登出撤銷本次登入；改密碼或啟停帳戶撤銷該帳戶所有Session；無refresh token。首位管理員由離線CLI建立，無匿名註冊／使用者刪除API，最後一名啟用管理員不可停用。

| 請求模型 | 欄位與限制 |
|---|---|
| Credentials | login必填3–80字元，小寫英數開頭，其餘小寫英數或._-；password必填1–128字元 |
| NewLocalUser | 同login規則；displayName必填1–500字元；password必填15–128字元；不可指定id／role／active |
| ChangePassword | currentPassword必填1–128字元；newPassword必填15–128字元；確認密碼只在前端處理 |
| Access | active必填boolean；version必填integer≥1；不可改login／displayName／role／密碼 |

UserInfo固定七欄：`{id,displayName,login,role,active,version,createdAt}`；前三者字串、role固定admin、active布林、version正整數、createdAt含時區時間。無passwordHash／authVersion。新增id由系統生成，active=true、version=1；users列表包含停用帳戶。

登入例子：`{"login":"demo-admin","password":"example-password-123"}`（非預設帳密），成功回：

```json
{"user":{"id":"admin-001","displayName":"Lab Admin","login":"demo-admin","role":"admin","active":true,"version":1,"createdAt":"2026-11-06T00:00:00Z"},"csrf":"opaque-session-csrf-value"}
```

改密碼成功須重新登入，舊密碼錯誤400；帳戶PATCH成功版本加一，過期409、不存在404、最後管理員停用422。重複登出或使用已撤銷Session回401。

### 內容模型與版本

ContentRecord為`{data,version,created,updated}`，列表包裝為`{items:[...]}`。data是對應Asset／Person／Publication的完整業務欄位；**people回應額外含唯讀name、publicationIds，寫回必須移除**。created/updated各為`{at,by}`，只讀展示，不可提交於data或外層。people不再接受slug／sourceUrl／affiliation，publications不再接受slug；舊欄位回422。欄位及預設值逐項見[DATABASE](DATABASE.md)。

POST的version可省略或null，成功初始1。PUT是完整替換，省略可選欄位會重設預設，須帶正整數version；缺版本422、過期409，成功加一並更新updated、保留created。DELETE須query version、無body。id建立後不可改。

```json
{"data":{"id":"amy","familyName":"Chen","givenName":"Amy","portrait":null},"version":null}
```

```json
{"data":{"id":"paper-1","title":"Example Paper","year":2026,"authors":[{"personId":"amy","name":"A. Chen"},{"personId":null,"name":"External Author"}],"pdfPath":"/assets/papers/paper-1.pdf"},"version":null}
```

資產最小data為`{"id":"logo","url":"/assets/logo.svg"}`。建立／修改回完整預設欄位。重複id409、缺失personId關聯或刪除被引用成員422；失敗不提交內容或成功審計。publicationIds即時反查，變動不提升people本身的version。

/schema只含可寫欄位的properties、required、$defs、default；/references只作選項輔助，儲存時重驗。people標籤為完整姓名並按姓排序，publications用title，assets用metadata.alt或id。pdfPath只記同源靜態路徑，後端不提供上傳／下載代理。

### 審計

`GET /api/v1/admin/audit?offset=0&limit=100`：offset整數≥0，limit為1–200。最新插入在前，無filter；回`{items:Audit[],total:number,offset:number,warnings:string[]}`。Audit完整欄位為`{id,at,actor,action,target,result}`，型別見資料字典。

超出尾頁items=[]；total為當次總數，不固定跨請求快照，新增事件可能使相鄰頁重複。warnings為可顯示維護警告，無警告時[]。沒有HTTP修改／刪除審計接口。

## 錯誤處理

應用錯誤通常為`{"detail":"訊息"}`；422可帶`fields`位置陣列，如`[["body","version"]]`，每段為字串或索引數字。聯集驗證可能有多組位置，不依賴訊息文字作業務判斷。入口可能回純文字／HTML，先檢查Content-Type。

| 狀態 | 語義 |
|---|---|
| 400 | JSON無效／重複鍵、修改ID、目前密碼錯誤 |
| 401 | 未登入、過期、錯誤帳密、帳戶停用或憑證版本失效 |
| 403 | Origin／CSRF／權限拒絕 |
| 404／405 | 資料或路徑不存在／方法不支援 |
| 409 | 版本、ID或登入名稱衝突；改密碼期間帳戶變更 |
| 413／415 | body超過256KiB／管理body非application/json |
| 422 | 欄位、型別、範圍、關聯、容量或最後管理員規則違反 |
| 429 | 密碼運算忙碌或入口限流 |
| 503 | 儲存／服務不可用、Session滿額或提交結果待確認 |

身分／Origin／CSRF先於body驗證。409保留輸入並重讀版本；503或網路中斷後先查結果，不盲目重送，沒有Idempotency-Key。應用錯誤帶no-store及X-Request-ID，不回密碼、Cookie、原始輸入或traceback；代理亦可能回502／504或逾時。

線上/docs、/redoc、/openapi.json關閉。契約生成與核對指令見[OPERATIONS](OPERATIONS.md#契約同步)，不需要啟動服務。
