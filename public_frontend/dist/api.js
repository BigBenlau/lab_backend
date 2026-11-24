// 公開頁只讀 API；固定設定由後端管理的 public/site.json 提供。
const idPattern = /^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$/;
export const pdfPath = value => typeof value === 'string' && !/\s/.test(value) && /^\/assets\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.pdf$/i.test(value) ? value : '';

// 每次向同源 Nginx 讀取；錯誤交給畫面重試，不回退舊快照。
async function read(path, missingAllowed = false) {
  const response = await fetch(path, {
    cache: 'no-store', credentials: 'omit', headers: {Accept: 'application/json'},
    signal: AbortSignal.timeout(15000),
  });
  if (missingAllowed && response.status === 404) return null;
  if (!response.ok || !response.headers.get('content-type')?.startsWith('application/json')) {
    throw new Error('Invalid content response: HTTP ' + response.status);
  }
  return response.json();
}

// 固定設定不包含 people/publications，不能用來回退業務內容。
export const readSettings = () => read('/data/site.json');

// 一頁對應一個內容端點；不存在的 ID 由畫面顯示找不到。
export function readPage(page, id) {
  if (page === 'home' || page === 'news') return read('/api/home');
  if (page === 'member') return id ? (idPattern.test(id) && !/\s/.test(id) ? read('/api/people/' + encodeURIComponent(id), true) : null) : read('/api/people');
  if (page === 'publications') return read('/api/publications');
}

/** @param {import('../contracts/public-api').PublicPublication | import('../contracts/public-api').PublicationSummary} row */
const paper = row => ({
  ...row, pageUrl: '#publications/' + encodeURIComponent(row.id),
});

// 只調整展示形狀；不重排論文、不複製人與論文的雙向關聯。
export function pageData(base, page, id, payload) {
  const assets = [...base.assets];
  const person = row => {
    // 空身份尚未分類，不可直接當成校友；列表保留 API 的姓／名／ID 順序。
    const state = row.memberships.some(role => role.isCurrent) ? 'member' : row.memberships.length ? 'alumni' : 'unclassified';
    const portraitAssetId = row.portrait ? 'api:portrait:' + row.id : null;
    if (row.portrait) assets.push({...row.portrait.metadata, id: portraitAssetId, url: row.portrait.url});
    const englishName = row.englishName?.trim();
    // 使用者指定的校友稱謂僅用於前端展示，不修改原始成員資料。
    const alumniName = state === 'alumni' ? [englishName, row.givenName, row.name].join(' ').match(/\b(molly|brian)\b/i)?.[1].toLowerCase() : '';
    return {...row, state, portraitAssetId,
      title: ({molly: 'PhD', brian: 'Master'})[alumniName] || row.title,
      name: englishName && !row.name.toLowerCase().includes(englishName.toLowerCase()) ? row.name + ' (' + englishName + ')' : row.name,
      interests: row.interests ?? [], links: row.links ?? [],
    };
  };
  const data = {...base, assets, people: [], publications: []};
  if (page === 'home' || page === 'news') {
    const photos = payload.assets.map(photo => ({...photo.metadata, url: photo.url, id: 'api:group:' + photo.id}));
    assets.push(...photos);
    data.siteSettings = {...base.siteSettings, heroPhotoAssetIds: photos.map(photo => photo.id)};
    data.recentPublications = payload.recentPublications.map(paper);
    data.publicationCount = payload.counts.publications;
  } else if (page === 'member' && id) {
    if (payload) {
      data.people = [person(payload.person)];
      data.publications = payload.publications.map(paper);
    }
  } else if (page === 'member') {
    data.people = payload.map(person);
  } else if (page === 'publications') {
    data.publications = payload.map(paper);
  }
  return data;
}
