"""完全虛構的局部檢查資料；不讀取或複製lab.json的真實成員與論文。"""
from lab_backend.models import Asset, Person, Publication, MODELS, empty_state, new_metadata, validate_state


def sample_data():
    """以目前模型產生小型合成資料，避免測試契約隨真實資料漂移。"""
    return {
        'assets': [Asset(id='logo', url='/assets/logo.svg').model_dump()],
        'people': [Person(id=f'person{i}', familyName='Example', givenName=f'Member{i}').model_dump() for i in range(3)],
        'publications': [Publication(id=f'paper{i}', title=f'Synthetic Paper {i}', year=2026-i,
                                    authors=[{'personId': 'person0', 'name': 'Example Author'}]).model_dump() for i in range(3)],
    }


def state_from_sample(sample):
    """只在測試中組裝儲存狀態；正式資料匯入使用CSV。"""
    state = empty_state()
    state.update({resource: [{**MODELS[resource].model_validate(row).model_dump(), **new_metadata()} for row in rows]
                  for resource, rows in sample.items()})
    validate_state(state)
    return state
