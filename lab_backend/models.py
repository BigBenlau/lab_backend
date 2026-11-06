"""SQLite 與 API 的明確契約；不讀取本機參考資料。"""
from datetime import datetime, timezone
from typing import Annotated, Generic, Literal, TypeVar
from urllib.parse import unquote, urlsplit
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,199}$")]
Short = Annotated[str, Field(max_length=500)]
Name = Annotated[str, Field(min_length=1, max_length=500)]
LoginName = Annotated[str, Field(min_length=3, max_length=80, pattern=r'^[a-z0-9][a-z0-9._-]{2,79}$')]
Password = Annotated[str, Field(min_length=1, max_length=128)]
NewPassword = Annotated[str, Field(min_length=15, max_length=128)]
PDF_PATH_PATTERN = r'/assets/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.[pP][dD][fF]'
# public 是匿名讀取身分，不是 users 表中的帳戶；登入帳戶只允許 admin。
Role = Literal['admin']


def now() -> str:
    """產生含 UTC 時區的時間字串，供內容與審計共用。"""
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def safe_url(value: str, *, relative: bool = True) -> str:
    """僅檢查字串，不抓取外部 URL；相對資產由公開前端提供。"""
    decoded = unquote(value)
    if len(value) > 2048 or any(ord(c) < 33 for c in decoded) or '\\' in decoded:
        raise ValueError('URL 含不允許字元')
    parsed = urlsplit(decoded)
    if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
        return value
    if relative and not parsed.scheme and not parsed.netloc and decoded.startswith(('./assets/', '/assets/')):
        if '..' not in parsed.path.split('/') and not decoded.startswith('//'):
            return value
    raise ValueError('URL 僅允許 http/https 或 ./assets/、/assets/ 路徑')


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_max_length=50000)

    @model_validator(mode='after')
    def check_common_values(self):
        """集中檢查 URL、Email 及時間語義，不以欄位型別取代內容驗證。"""
        for key in type(self).model_fields:
            value = getattr(self, key)
            if key in ('createdAt', 'updatedAt', 'at'):
                if datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError(f'{key} 需要時區')
            if isinstance(value, str) and value:
                if key == 'url' or key.endswith('Url'):
                    safe_url(value, relative=(key == 'url'))
                if key == 'pdfPath' and not re.fullmatch(PDF_PATH_PATTERN, value):
                    # 同源靜態路徑才能直接下載；不接受主機路徑、外站、跳脫或查詢字串。
                    raise ValueError('pdfPath 須為 /assets/ 下的 PDF 路徑，檔名使用英數字、底線或連字號')
                if key.endswith('Email') and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
                    raise ValueError(f'{key} 格式不正確')
        return self


class Link(Model):
    label: Name
    url: Annotated[str, Field(min_length=1, max_length=2048)]


# 成功／錯誤包裝供所有入口共用；回應模型也會出現在離線 OpenAPI。
class ApiError(Model):
    detail: str
    fields: list[list[str | int]] | None = None


class Ok(Model):
    ok: Literal[True]


Item = TypeVar('Item')


class Items(Model, Generic[Item]):
    items: list[Item]


class Membership(Model):
    role: Name
    isCurrent: bool = True


class Author(Model):
    personId: Id | None = None
    name: Name


class Venue(Model):
    name: str = ''
    shortName: Short = ''


class ImageMetadata(Model):
    """圖片描述集中為一個物件；寬高由前端 CSS 與圖片本身決定。"""
    mimeType: Literal['image/svg+xml', 'image/png', 'image/jpeg', 'image/webp'] = 'image/png'
    alt: Short = ''
    caption: str | None = None
    credit: Short = ''


class AssetMetadata(ImageMetadata):
    """用途決定是否進首頁相簿；不依檔名或固定 ID 猜測合照。"""
    mimeType: Literal['image/svg+xml', 'image/png', 'image/jpeg', 'image/webp', 'application/pdf'] = 'image/png'
    purpose: Literal['group_photo', 'other'] = 'other'

    @model_validator(mode='after')
    def check_group_photo(self):
        """PDF 可作一般資產，但不能被當成首頁合照。"""
        if self.purpose == 'group_photo' and not self.mimeType.startswith('image/'):
            raise ValueError('合照必須是圖片類型')
        return self


class Photo(Model):
    """照片直接儲存在 people.portrait；沿用網址＋metadata 的形狀。"""
    url: Annotated[str, Field(min_length=1, max_length=2048)]
    metadata: ImageMetadata = Field(default_factory=ImageMetadata)


class Asset(Model):
    id: Id
    url: Annotated[str, Field(min_length=1, max_length=2048)]
    metadata: AssetMetadata = Field(default_factory=AssetMetadata)


class Person(Model):
    id: Id
    familyName: Name
    givenName: Name
    englishName: Short | None = Field(default=None, description='英文名，可留空；null 或空字串表示不顯示，不改變姓／名排序。')
    title: Short = ''
    portrait: Photo | None = None
    bioMarkdown: str = ''
    interests: Annotated[list[Short], Field(max_length=100)] = []
    memberships: Annotated[list[Membership], Field(max_length=30)] = []
    publicEmail: Short | None = None
    links: Annotated[list[Link], Field(max_length=30)] = []


class Publication(Model):
    id: Id
    sourceUrl: str = ''
    title: Name
    authors: Annotated[list[Author], Field(min_length=1, max_length=100)]
    year: Annotated[int, Field(ge=1800, le=2200)]
    type: Literal['conference', 'journal', 'workshop'] = 'conference'
    venue: Venue = Field(default_factory=Venue)
    pdfPath: Annotated[str, Field(min_length=1, max_length=2048, pattern=f'^{PDF_PATH_PATTERN}$',
        description='官網同源 /assets/ 下的 PDF 路徑；null 表示無下載檔案。檔案由公開前端提供。')] | None = None


class ChangeStamp(Model):
    """時間與操作者是一個整體；每次變更以同一個 JSON 欄位寫入。"""
    at: str
    by: Name


class History(Model):
    """只屬於儲存／管理回應；不讓內容表單指定操作者或時間。"""
    created: ChangeStamp
    updated: ChangeStamp


class StoredAsset(Asset, History):
    """資產同樣使用合併的建立／更新紀錄。"""


class StoredPerson(Person, History):
    """成員的資料庫模型。"""


class StoredPublication(Publication, History):
    """論文的資料庫模型。"""


class PersonView(Person):
    """讀取才產生姓名及論文清單；不加入 SQLite 或可寫入模型。"""
    name: str = Field(json_schema_extra={'readOnly': True})
    publicationIds: list[Id] = Field(json_schema_extra={'readOnly': True})


class PersonSummary(Model):
    """成員目錄只帶名片與簡介；完整履歷、聯絡方式及論文由詳情取得。"""
    id: Id
    familyName: Name
    givenName: Name
    englishName: Short | None
    name: str
    title: Short
    portrait: Photo | None
    bioMarkdown: str
    memberships: list[Membership]


class PublicPerson(PersonSummary):
    """只增加個人頁實際使用的欄位；論文清單由外層 publications 提供。"""
    interests: list[Short]
    publicEmail: Short | None
    links: list[Link]


class PublicationSummary(Model):
    """首頁論文卡片；作者、PDF 與來源連結留給論文列表／詳情。"""
    id: Id
    title: Name
    year: Annotated[int, Field(ge=1800, le=2200)]
    type: Literal['conference', 'journal', 'workshop']
    venue: Venue


class PublicPublication(PublicationSummary):
    """列表與站內論文頁共用，不附加儲存時間或網址別名。"""
    authors: list[Author]
    pdfPath: Annotated[str, Field(min_length=1, max_length=2048, pattern=f'^{PDF_PATH_PATTERN}$')] | None
    sourceUrl: str


class PersonDetail(Model):
    """單人成員及其論文共用同一次 SQLite 讀取快照。"""
    person: PublicPerson
    publications: list[PublicPublication]


class Counts(Model):
    publications: int = Field(ge=0)


class Home(Model):
    """首頁僅提供全部合照、最近三篇摘要及論文總數。"""
    assets: list[Asset]
    recentPublications: list[PublicationSummary] = Field(max_length=3)
    counts: Counts


class User(Model):
    id: Id
    displayName: Name
    login: LoginName
    role: Role = 'admin'
    active: bool = True
    passwordHash: str
    authVersion: Annotated[int, Field(ge=1)] = 1
    version: Annotated[int, Field(ge=1)] = 1
    createdAt: str = Field(default_factory=now)

class Audit(Model):
    id: Id
    at: str
    actor: str
    action: Short
    target: Short = ''
    result: Literal['success', 'denied']


MODELS = {'assets': Asset, 'people': Person, 'publications': Publication}
ALL_MODELS = {'assets': StoredAsset, 'people': StoredPerson, 'publications': StoredPublication,
              'users': User, 'audit': Audit}
FILES = {key: f'{key}.csv' for key in ALL_MODELS}
META = {'_version': 1}
# 30人／300篇是性能估算，沒有成員或論文筆數上限。
ROW_LIMITS = {'assets': 1000, 'users': 1000}


def new_metadata(actor: str = 'system') -> dict:
    """API／CSV 新增共用一次時間取樣，避免兩個紀錄分別組裝而遺漏。"""
    stamp = ChangeStamp(at=now(), by=actor).model_dump()
    return {**META, 'created': stamp.copy(), 'updated': stamp.copy()}


def person_name(row: dict) -> str:
    """組合顯示姓名；不把格式化字串另存一份，也不以名字推斷身份。"""
    return f"{row['givenName']} {row['familyName']}"


def person_sort_key(row: dict):
    """姓→名→ID穩定排序；Unicode正規化及casefold，不猜測漢字拼音。"""
    return (*(unicodedata.normalize('NFKC', row[k]).casefold().strip()
              for k in ('familyName', 'givenName')), row['id'])


def publication_index(publications: list[dict]) -> dict[str, list[str]]:
    """一輪建立反向索引，只用本次交易資料；同文同人只加入一次；輸入為入庫順序，輸出由新到舊。"""
    index = {}
    for paper in reversed(publications):
        for person_id in dict.fromkeys(a['personId'] for a in paper['authors'] if a['personId']):
            index.setdefault(person_id, []).append(paper['id'])
    return index


def person_view(row: dict, index: dict) -> dict:
    """讀取模型的衍生欄位永不作為寫入來源。"""
    return {**data_only('people', row), 'name': person_name(row),
            'publicationIds': index.get(row['id'], [])}


def data_only(resource: str, row: dict) -> dict:
    """以模型欄位白名單移除私有管理欄位，供公開投影使用。"""
    return {key: row[key] for key in MODELS[resource].model_fields}


def empty_state() -> dict:
    """建立初始化／驗證／恢復使用的空資料集。"""
    return {key: [] for key in ALL_MODELS}


def validate_state(state: dict) -> None:
    """在提交鎖內執行：容量/唯一性/關聯不能只靠表單檢查。"""
    if set(state) != set(ALL_MODELS):
        raise ValueError('資料集合不完整')
    for resource, model in ALL_MODELS.items():
        fields = set(model.model_fields)
        ids = set()
        limit = ROW_LIMITS.get(resource)
        if limit is not None and len(state[resource]) > limit:
            raise ValueError(f'{resource} 超過安全容量，需先維護')
        for row in state[resource]:
            if resource in MODELS:
                if set(row) != fields | META.keys():
                    raise ValueError('管理欄位不完整')
                if type(row['_version']) is not int or row['_version'] < 1:
                    raise ValueError('版本無效')
                model.model_validate({k: row[k] for k in fields})
            else:
                model.model_validate(row)
            if 'id' in row:
                if row['id'] in ids:
                    raise ValueError(f'{resource} ID 重複')
                ids.add(row['id'])
    identities = [u['login'] for u in state['users']]
    if len(identities) != len(set(identities)):
        raise ValueError('登入身份重複')
    if state['users'] and not any(u['active'] for u in state['users']):
        raise ValueError('必須保留一位啟用的本地管理員')
    # 唯一可寫關聯是作者personId，直接驗證即可，不再維護通用關聯遍歷器。
    people_ids = {person['id'] for person in state['people']}
    for paper in state['publications']:
        for author in paper['authors']:
            ref = author['personId']
            if ref and ref not in people_ids:
                raise ValueError(f'publications 關聯 people/{ref} 不存在，不能刪除被引用資料')


def public_row(resource: str, row: dict) -> dict:
    """唯讀資料不附帶未使用的反查清單；頁面需要關聯時才建立索引。"""
    data = data_only(resource, row)
    return {**data, 'name': person_name(row)} if resource == 'people' else data
