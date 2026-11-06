"""內容 CRUD；所有可改動結果的檢查均在 store.change 鎖內重做。"""
from fastapi import APIRouter, Request, Query
from pydantic import Field
from functools import cache
from typing import Literal
from .models import (Asset, ChangeStamp, Id, Items, Model, MODELS, Ok, Person, Publication,
                     PersonView, data_only, now, new_metadata, person_name, person_view, publication_index)
from .auth import audit, deny

router = APIRouter(prefix='/api/v1/admin', tags=['content'])


Resource = Literal['assets', 'people', 'publications']
ContentData = Asset | Person | Publication


class CreateContent(Model):
    """聯集列出三類資料形狀；路由仍須驗證 data 與 resource 相符。"""
    data: ContentData
    version: None = None


class Edit(CreateContent):
    """PUT 必須提供正整數版本；缺少版本是 422，過期版本才是 409。"""
    version: int = Field(ge=1)


class ContentRecord(Model):
    data: Asset | PersonView | Publication
    version: int = Field(ge=1)
    created: ChangeStamp
    updated: ChangeStamp


class Reference(Model):
    id: Id
    label: str


class References(Model):
    people: list[Reference]
    publications: list[Reference]
    assets: list[Reference]


def envelope(resource, row, index):
    """將業務內容和編輯版本分開，供後台偵測並行更新。"""
    return {'data': person_view(row, index) if resource == 'people' else data_only(resource, row),
            'version': row['_version'], 'created': row['created'], 'updated': row['updated']}


def find(state, resource, record_id):
    """依穩定 ID 尋找記錄。"""
    return next((r for r in state[resource] if r['id'] == record_id), None)


@router.get('/schema', response_model=dict[Resource, dict])
def schema(request: Request):
    """管理員取得表單規格；不回傳內容或帳戶資料。"""
    state = request.app.state.store.read(('users',))
    request.app.state.security.actor(request, state)
    return content_schema()


@cache
def content_schema():
    """僅重用固定模型規格，這不是業務內容快取。"""
    return {key: model.model_json_schema() for key, model in MODELS.items()}


@router.get('/references', response_model=References)
def reference_options(request: Request):
    """提供管理員關聯選項，儲存時仍由後端驗證對象存在。"""
    state = request.app.state.store.read()
    request.app.state.security.actor(request, state)
    # actor() 統一要求有效 admin；選項只是輔助，不能取代提交時的關聯驗證。
    return {key: [{'id': r['id'], 'label': person_name(r) if key == 'people' else r.get('title') or r.get('metadata', {}).get('alt') or r['id']}
                  for r in state[key]]
            for key in ('people', 'publications', 'assets')}


@router.get('/content/{resource}', response_model=Items[ContentRecord])
def list_content(resource: Resource, request: Request):
    """取得指定類型的內容及管理版本，只供管理員使用。"""
    # 管理成員表單會顯示反查論文，與成員放在同一快照讀取；其他資源不附帶載入。
    resources = ('users', 'people', 'publications') if resource == 'people' else ('users', resource)
    state = request.app.state.store.read(resources)
    request.app.state.security.actor(request, state)
    index = publication_index(state.get('publications', []))
    return {'items': [envelope(resource, row, index) for row in (reversed(state[resource]) if resource == 'publications' else state[resource])]}


@router.post('/content/{resource}', status_code=201, response_model=ContentRecord)
def create(resource: Resource, body: CreateContent, request: Request):
    """依路徑模型建立內容；新增版本由系統設定。"""
    model = MODELS[resource]  # Resource 路徑型別已限制為三類模型。
    def operation(state):
        """在交易內重查管理員，驗證唯一 ID；儲存成功即對公開 API 可見。"""
        actor, _ = request.app.state.security.actor(request, state)
        data = model.model_validate(body.data.model_dump(exclude_unset=True)).model_dump()
        if find(state, resource, data['id']):
            deny('ID 已存在', 409)
        row = {**data, **new_metadata(actor['id'])}
        state[resource].append(row)
        audit(state, actor['id'], 'content.created', f'{resource}/{data["id"]}')
        return envelope(resource, row, publication_index(state['publications']))
    return request.app.state.store.change(operation)


@router.put('/content/{resource}/{record_id}', response_model=ContentRecord)
def update(resource: Resource, record_id: Id, body: Edit, request: Request):
    """以完整資料及版本更新記錄，成功後立即可公開讀取。"""
    model = MODELS[resource]
    def operation(state):
        """重查身份與版本；時間欄位由伺服器維護，不能改 ID。"""
        actor, _ = request.app.state.security.actor(request, state)
        row = find(state, resource, record_id)
        if row is None:
            deny('找不到資料', 404)
        if body.version != row['_version']:
            deny('資料已被修改；請另開頁面核對，本頁輸入仍保留', 409)
        data = model.model_validate(body.data.model_dump(exclude_unset=True)).model_dump()
        if data['id'] != record_id:
            deny('ID 不能修改', 400)
        # created 保持原值；版本、updated 及審計由同一交易一起提交。
        row.update(data, _version=row['_version'] + 1,
                   updated=ChangeStamp(at=now(), by=actor['id']).model_dump())
        audit(state, actor['id'], 'content.updated', f'{resource}/{record_id}')
        return envelope(resource, row, publication_index(state['publications']))
    return request.app.state.store.change(operation)


@router.delete('/content/{resource}/{record_id}', response_model=Ok)
def delete(resource: Resource, record_id: Id, request: Request, version: int = Query(ge=1)):
    """刪除需攜帶版本；仍被論文引用的成員會拒絕。"""
    def operation(state):
        """先確認記錄與版本，再在同次提交中刪除並寫審計。"""
        actor, _ = request.app.state.security.actor(request, state)
        row = find(state, resource, record_id)
        if row is None:
            deny('找不到資料', 404)
        if version != row['_version']:
            deny('版本衝突', 409)
        state[resource].remove(row)
        audit(state, actor['id'], 'content.deleted', f'{resource}/{record_id}')
        return {'ok': True}
    # validate_state 會阻止刪除仍被任何資料引用的記錄。
    return request.app.state.store.change(operation)
