"""四個頁面查詢入口；每次唯讀 SQLite，不提供整站輸出或單篇論文 API。"""
from fastapi import APIRouter, Request, HTTPException
from .models import ApiError, Home, Id, PersonDetail, PersonSummary, PublicPerson, PublicPublication, PublicationSummary, publication_index


router = APIRouter(prefix='/api/v1/public', tags=['public'], responses={
    503: {'model': ApiError, 'description': 'SQLite 儲存忙碌或不可用；代理產生的 503 也可能不是 JSON。'},
})


def page_fields(model, row):
    """以回應模型作白名單；不把儲存欄位或其他頁面資料一併送出。"""
    return {key: row[key] for key in model.model_fields}


@router.get('/home', response_model=Home, summary='取得首頁內容',
            description='同一快照回傳全部 group_photo 合照、入庫倒序前三篇論文摘要及論文總數。固定網站文字由前端維護。')
def home(request: Request):
    """首頁只輸出需要的摘要，不把所有成員及所有論文送給前端。"""
    state = request.app.state.store.read(('assets', 'publications'))
    recent = [page_fields(PublicationSummary, row) for row in state['publications'][:3]]
    return {'assets': [row for row in state['assets'] if row['metadata'].get('purpose', 'other') == 'group_photo'],
            'recentPublications': recent, 'counts': {'publications': len(state['publications'])}}


@router.get('/people', response_model=list[PersonSummary], summary='取得全部成員簡介',
            description='包含成員及校友，按姓／名／ID排序；不含完整聯絡方式、興趣或論文資料，空資料回傳 []。')
def people(request: Request):
    """以明確欄位白名單產生名片；同時避免額外欄位進入嚴格回應模型。"""
    rows = request.app.state.store.read(('people',))['people']
    return [page_fields(PersonSummary, row) for row in rows]


@router.get('/people/{person_id}', response_model=PersonDetail, summary='取得單人成員詳情及論文',
            description='person_id 僅匹配 ID；person 與 publications 來自同一 SQLite 快照，作者關聯按 authors[].personId 判定。',
            responses={404: {'model': ApiError, 'description': '成員不存在。'}})
def person_detail(person_id: Id, request: Request):
    """只維護論文端的作者關聯；成員詳情一次回傳反查結果。"""
    state = request.app.state.store.read(('people', 'publications'))
    person = next((p for p in state['people'] if p['id'] == person_id), None)
    if person is None:
        raise HTTPException(404, '找不到成員')
    # 索引共用唯一作者規則；公開列表已倒序，先還原為索引函式接收的入庫順序。
    related = set(publication_index(state['publications'][::-1]).get(person_id, []))
    papers = [page_fields(PublicPublication, p) for p in state['publications'] if p['id'] in related]
    return {'person': page_fields(PublicPerson, person), 'publications': papers}


@router.get('/publications', response_model=list[PublicPublication], summary='取得全部論文',
            description='按新增入庫順序倒序，保留 authors 署名順序。pdfPath 是同源 PDF 路徑或 null；無單篇論文接口。')
def publications(request: Request):
    """列表一次取得完整論文，前端保留新增倒序，篩選及呈現下載入口。"""
    return [page_fields(PublicPublication, row) for row in request.app.state.store.read(('publications',))['publications']]
