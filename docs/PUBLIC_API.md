# 公開前端對接

契約7.1.0，來源為[public-openapi.json](public-openapi.json)。四個GET路由與包裝不變；本版在成員列表與單人詳情新增alumniSince畢業年月。後端只在Docker的lab-content網路提供http://lab-public-api:8000；瀏覽器經官網同源代理，不帶Cookie／token，無跨站CORS。

## 四個頁面API

| GET後端路徑 | 官網同源代理 | 輸入 | 200輸出 |
|---|---|---|---|
| /api/v1/public/home | /api/home | 無 | Home |
| /api/v1/public/people | /api/people | 無 | PersonSummary[] |
| /api/v1/public/people/{person_id} | /api/people/{person_id} | path person_id | PersonDetail |
| /api/v1/public/publications | /api/publications | 無 | PublicPublication[] |

無body、query、伺服器分頁或搜尋；未知query目前忽略。person_id是1–200字元的穩定ID，英數開頭，其餘英數／底線／連字號，不按slug或姓名查找。直接回物件／陣列，沒有data/items包裝。

每次請求讀SQLite，回no-store；成員列表只讀people，不為未展示的反查資料載入全部論文；首頁各部分、個人與其論文各自使用同一快照。不同請求可能看到不同提交，不以靜態JSON／Service Worker／CDN回應快取代替API。30人／300篇是估算，非筆數上限。

## 完整輸出欄位

下列欄位全部會回傳；null、空字串、空陣列不同。JSON物件已解析，前端不用再次JSON.parse。通用限制及管理／儲存欄位見[DATABASE](DATABASE.md)。

| 型別 | 完整欄位 |
|---|---|
| Home | assets:Asset[]（僅合照）；recentPublications:PublicationSummary[]（最多3篇）；counts:{publications:number} |
| PersonSummary | id、familyName、givenName、name、title、bioMarkdown:string；englishName:string或null；portrait:Photo或null；memberships:Membership[]；alumniSince:YYYY-MM字串或null |
| PublicPerson | PersonSummary全部欄位，加interests:string[]、publicEmail:string或null、links:Link[] |
| PersonDetail | person:PublicPerson；publications:PublicPublication[] |
| PublicationSummary | id、title:string；year:integer；type:conference/journal/workshop；venue:Venue |
| PublicPublication | PublicationSummary全部欄位，加authors:Author[]、pdfPath:string或null、sourceUrl:string |
| Asset | id、url:string；metadata:AssetMetadata |
| Photo | url:string；metadata:ImageMetadata（沒有id） |
| ImageMetadata | mimeType:image/svg+xml、image/png、image/jpeg或image/webp；alt/credit:string；caption:string或null |
| AssetMetadata | 同ImageMetadata，mimeType另允許application/pdf；purpose:group_photo/other；group_photo必須是圖片 |
| Author | personId:string或null；name:string |
| Membership | role:string；isCurrent:boolean |
| Venue | name、shortName:string |
| Link | label、url:string |

空首頁為`{"assets":[],"recentPublications":[],"counts":{"publications":0}}`；空列表為[]。沒有相關論文的成員詳情仍回person、publications=[]。

## 頁面與資料規則

| 頁面 | 對應資料與顯示 |
|---|---|
| 首頁 | assets回全部metadata.purpose=group_photo的合照，保留入庫順序；前端不再維護固定ID清單。recentPublications回入庫倒序前三篇的標題、年份、類型與會議資訊；counts.publications為全站論文數。 |
| 所有成員 | 全部成員／校友的姓名、英文名、職銜、照片、簡介及身份分類。API按姓／名／ID排序，前端篩選保留順序。 |
| 個人頁 | person提供姓名、英文名、職銜、照片、簡介、興趣、公開Email與連結；publications提供該人成員ID對應的全部論文。 |
| 論文頁 | 回全部論文，前端每頁12篇，年份／類型／作者篩選在本機進行，不重排API入庫順序。站內論文詳情沿用這份資料，不另查單篇接口。 |

- englishName為英文名，可省略輸入（預設null）或填null／空字串；不限制成ASCII。前端空值不加括號，有值顯示name (englishName)，兩者相同時不重複。欄位已取代preferredName，舊名稱會被管理API拒絕。
- alumniSince固定回傳，例如"2026-06"或null；列表讀people項目的欄位，詳情讀person.alumniSince。null時隱藏畢業年月，不補成1日或轉成帶時區的日期。只有至少一項身份且全部isCurrent=false的校友可有非null日期；空身份或任何現任身份只能null。欄位本身不代表已改成校友，也不要求所有校友填寫。
- familyName／givenName分開保存，name由givenName＋空格＋familyName產生；英文名不參與排序、不改論文署名。排序使用NFKC、casefold及去首尾空白，中文姓氏不猜拼音。
- 論文列表及個人論文按新增入庫倒序；首頁取前三篇，不是三頁。修改年份／標題不移位，CSV最後新增的一列排最前。
- authors保留署名順序；personId連people.id，外部作者為null；不以同名自動配對。個人頁publications由此關聯取得，沒有相關論文就回[]。
- 固定介紹、品牌、招募、Contact及早期論文封存連結存於後端管理的public/site.json，由前端唯讀展示，不新增設定／新聞API。通用圖片描述保留相同metadata格式，避免照片與相簿兩套形狀。

## 欄位取捨

| 調整 | 原因／保留位置 |
|---|---|
| preferredName → englishName | 明確表示可留空的英文名；同步SQLite欄位、CSV、管理表單及兩個成員頁 |
| assets.metadata新增purpose | 可明確選取全部合照；其他圖片、logo與PDF不送首頁，不必另建表 |
| 首頁不回非合照資產、counts.people、論文authors/pdfPath/sourceUrl等詳情 | 首頁只使用合照、論文摘要及論文總數 |
| 成員移除slug、affiliation、sourceUrl | 頁面及關聯未使用，已從SQLite、管理API、CSV與表單移除 |
| 公開成員不回createdAt／updatedAt | 頁面未使用；管理歷史保留 |
| 公開個人資料不回publicationIds | 外層publications已包含完整相關論文及id；管理頁仍保留反查ID清單 |
| 論文移除slug，公開不回createdAt／updatedAt | 站內連結用id，slug已從SQLite／管理API／CSV移除；歷史與sourceUrl仍保留 |

沒有/lab、獨立/assets、單人成員論文子接口或單篇論文API，也沒有新聞／設定／schemaVersion等舊接口。未知欄位不自動回填。兩份OpenAPI修訂為7.1.0；URL仍/api/v1。公開前端沿用原四路由，需要展示畢業年月時讀取新增欄位；管理端使用新版schema表單。

## PDF、圖片與錯誤

pdfPath為null或`/assets/papers/paper-1.pdf`等同源PDF路徑；前端用`<a href="..." download>`下載。目錄只用英數／底線／連字號，檔名可加點，不接受外站、空白、百分號、穿越、query或fragment。實體檔存volume的public/assets，由官網Nginx提供application/pdf；null隱藏按鈕，缺檔由靜態服務回404。

圖片url契約允許http/https及./assets/；正式HTTPS官網僅載入同源或HTTPS圖片，請將外部HTTP圖片改成HTTPS或放入volume。本機前端已支援中文相對檔名；仍拒絕穿越、控制字元及帶帳密URL。metadata無width／height。文字跳脫後顯示，Markdown不執行原始HTML，SVG以圖片載入。[離線缺檔檢查](OPERATIONS.md#日常內容與檔案維護)不會抓取外站資產。

```js
const response = await fetch(`/api/people/${encodeURIComponent(personId)}`, {
  cache: 'no-store', credentials: 'omit', headers: {Accept: 'application/json'},
  signal: AbortSignal.timeout(15000)
});
if (!response.ok) throw new Error(`HTTP ${response.status}`);
if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Invalid response');
const {person, publications} = await response.json();
```

找不到成員404、非法ID422、內容不可用503；代理可能回HTML、502／504或逾時。應顯示錯誤及重試，不能以空陣列冒充成功。完整[錯誤契約](API.md#錯誤處理)共用。[代理設定](../public_frontend/nginx.conf)只放行四個GET，不轉送管理路由或Cookie／Authorization。[型別同步](../ARCHITECTURE.md#契約同步)從OpenAPI生成前端型別，不手改生成檔。

## 完整合成範例與版本範圍

[public-api.samples.json](public-api.samples.json)以四個GET後端路徑為鍵，值是各接口完整200 body；實際回應不包這層路徑物件。這是文件範例，不是執行時種子或JSON回退。

- home：兩張合照（一般logo不輸出），前三篇paper-4、paper-3、paper-2，counts.publications=4。
- people：兩人成員／校友，英文名及照片分別示範有值與null，alumniSince示範null及"2024-06"。
- people/member-example：完整個人頁資料與paper-4、paper-3、paper-1，包含內部與外部作者。
- publications：全部四篇，順序4→3→2→1；年份刻意不依序，paper-2沒有PDF。

範例使用完全虛構資料，從暫存SQLite的實際ASGI回應產生；未讀取私人lab.json，也不附實體照片／PDF。程式內核對不代表瀏覽器、實體檔案或部署驗收通過，實際檢查見[IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md)。

公開頁已固定使用上述四個API；原始data/lab.json只供明確首次seed，不再由瀏覽器讀取。public_frontend是主倉庫的普通資料夾，由Compose的public-web獨立容器提供頁面與代理。建置僅包含五個頁面程式檔，沒有data/assets原始內容。

頁面啟動先GET /data/site.json，之後各頁各取一個內容API；相簿頁讀home，聯絡頁只用固定設定。site.json由Nginx唯讀提供，頂層只有siteSettings、assets（品牌圖）、opportunities（固定聯絡文字），沒有people/publications或合照ID清單。首頁合照仍由SQLite home API決定。初始化、公開檔案與配對備份見[OPERATIONS](OPERATIONS.md)。此靜態設定不計入四個業務API，也不作API故障時的回退。
