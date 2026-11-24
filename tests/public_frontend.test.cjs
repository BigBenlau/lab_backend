// 程序內 API／DOM 替身；直接執行正式頁面函式，不開瀏覽器或 HTTP listener。
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'public_frontend/dist/api.js'), 'utf8');
const api = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const base = () => ({siteSettings: {name: 'Synthetic Lab', fullName: 'Example Lab', institution: 'Example', links: []}, assets: [], opportunities: []});
const person = changes => ({id: 'amy', name: 'An Example', familyName: 'Example', givenName: 'An', englishName: 'Avery', title: 'Student', portrait: null, memberships: [{role: 'student', isCurrent: true}], alumniSince: null, ...changes});
const paper = (id, year=2026) => ({id, title: id, year, type: 'journal', venue: {name: 'Synthetic Venue', shortName: 'SV'}, authors: [{personId: 'amy', name: 'Exact signature'}, {personId: null, name: 'External'}], sourceUrl: 'https://example.invalid/paper', pdfPath: null});
function response(body, status=200, type='application/json') {
  return {ok: status === 200, status, headers: {get: () => type}, json: async () => body};
}

// DOM 替身只提供頁面使用的介面；回傳的 HTML 仍由真實 lab.js 產生。
async function renderers() {
  const adapter = await api;
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      value: '', innerHTML: '', textContent: '', dataset: {}, style: {},
      addEventListener() {}, setAttribute() {}, removeAttribute() {}, focus() {}, scrollIntoView() {},
      querySelector: key => element(selector + ' ' + key), querySelectorAll: () => [],
      classList: {remove() {}, toggle() {return false;}},
    });
    return elements.get(selector);
  };
  const document = {querySelector: element, getElementById: id => element('#' + id), querySelectorAll: () => [], documentElement: {dataset: {}}, body: {style: {}}};
  const context = vm.createContext({...adapter, document, URL, URLSearchParams, Intl, Date, console,
    localStorage: {getItem() {}, setItem() {}}, location: {hash: '#home'}, history: {replaceState() {}},
    window: {addEventListener() {}, scrollTo() {}}, navigator: {clipboard: {writeText: async () => {}}}});
  let code = fs.readFileSync(path.join(root, 'public_frontend/dist/lab.js'), 'utf8').replace(/^import .*;\n/gm, '').replace(/\nstart\(\);\s*$/, '');
  vm.runInContext(code + '\nglobalThis.render={paperActions,paperLink,image,authors,photoStack,memberLink,home,people,publications,route,start,setData(value){data=baseData=value;}};', context);
  context.render.setData({...base(), people: [], publications: []});
  return {render: context.render, element, context};
}

test('四個頁面各只呼叫指定API；不添加私人憑證或快取', async t => {
  const {readPage, readSettings} = await api;
  const cases = [ ['home', undefined, '/api/home'], ['member', undefined, '/api/people'],
    ['member', 'amy', '/api/people/amy'], ['publications', undefined, '/api/publications'] ];
  for (const [page, id, url] of cases) {
    const calls = [], body = {synthetic: true};
    t.mock.method(globalThis, 'fetch', async (target, options) => {calls.push({target, options}); return response(body);});
    assert.deepEqual(await readPage(page, id), body);
    assert.equal(calls.length, 1); assert.equal(calls[0].target, url);
    assert.equal(calls[0].options.credentials, 'omit'); assert.equal(calls[0].options.cache, 'no-store');
    t.mock.restoreAll();
  }
  t.mock.method(globalThis, 'fetch', async url => {assert.equal(url, '/data/site.json'); return response(base());});
  assert.deepEqual(await readSettings(), base());
});

test('API失敗或HTML回應報錯，不回退lab.json或空陣列', async t => {
  const {readPage} = await api;
  for (const result of [response(null, 503), response(null, 200, 'text/html')]) {
    let calls = 0;
    t.mock.method(globalThis, 'fetch', async () => {calls++; return result;});
    await assert.rejects(readPage('home')); assert.equal(calls, 1); t.mock.restoreAll();
  }
});

test('聯絡頁不用內容API；相簿讀home；無效ID不發送請求，404回找不到', async t => {
  const {readPage} = await api;
  const calls = [];
  t.mock.method(globalThis, 'fetch', async url => {calls.push(url); return response(null, 404);});
  assert.equal(readPage('contact'), undefined);
  for (const id of ['bad/id', '../private', 'amy\n']) assert.equal(readPage('member', id), null);
  assert.equal(calls.length, 0);
  assert.equal(await readPage('member', 'missing'), null);
  await assert.rejects(readPage('news'));
  assert.deepEqual(calls, ['/api/people/missing', '/api/home']);
});

test('PDF僅允許同源assets路徑且產生download連結', async () => {
  const {pdfPath} = await api;
  const {render} = await renderers();
  for (const value of ['/assets/paper.pdf', '/assets/papers/paper-1.PDF']) {
    assert.equal(pdfPath(value), value);
    assert.match(render.paperActions({pdfPath: value}), / download>Download PDF/);
  }
  for (const value of [null, '', '//evil/p.pdf', 'https://evil/p.pdf', '/etc/passwd', '/assets/../secret.pdf', '/assets/%2e%2e/p.pdf', '/assets/a.pdf?x=1', '/assets/a.pdf#x', '/assets/a.png', '/assets/a.pdf\n']) {
    assert.equal(pdfPath(value), '');
    assert.doesNotMatch(render.paperActions({pdfPath: value}), /Download PDF/);
  }
  assert.match(render.paperActions(paper('a')), /href="https:\/\/example.invalid\/paper"/);
});

test('照片metadata與署名跳脫；不需全體成員即可生成作者連結', async () => {
  const {pageData} = await api, {render} = await renderers();
  const row = person({portrait: {url: '/assets/合照.jpg', metadata: {alt: '<name>'}}});
  const data = pageData(base(), 'member', undefined, [row]); render.setData(data);
  const html = render.image(data.people[0].portraitAssetId);
  assert.match(html, /alt="&lt;name&gt;"/); assert.match(html, /\/assets\/合照.jpg/);
  assert.doesNotMatch(html, /width=|height=/);
  render.setData({...base(), people: [], publications: []});
  assert.match(render.paperLink({pageUrl: '#publications/PAPER_1', title: 'Paper'}), /href="#publications\/PAPER_1"/);
  const authors = render.authors({authors: [{name: '<Author>', personId: 'amy'}, {name: 'External', personId: null}]});
  assert.match(authors, /href="#member\/amy"/); assert.match(authors, /&lt;Author&gt;/); assert.match(authors, /External/);
  for (const url of ['/assets/../secret.jpg', '/assets/%2e%2e/secret.jpg', '//evil/a.jpg', 'javascript:alert(1)', 'https://name:password@example.invalid/a.jpg']) {
    render.setData({...base(), assets: [{id: 'unsafe', url, alt: ''}]});
    assert.equal(render.image('unsafe'), '');
  }
});

test('個人頁與單頁列表維持API論文順序，全部展示且沒有篩選或分頁', async () => {
  const {pageData} = await api, {render, element} = await renderers();
  const papers = [paper('new', 2020), ...Array.from({length: 12}, (_, i) => paper('middle-' + i)), paper('old', 2026)];
  const detail = pageData(base(), 'member', 'amy', {person: person(), publications: papers});
  assert.deepEqual(detail.publications.map(p => p.id), papers.map(p => p.id));
  render.setData(detail); render.people('amy');
  const html = element('#content').innerHTML;
  assert.ok(html.indexOf('>new</a>') < html.indexOf('>old</a>'));
  render.setData(pageData(base(), 'publications', undefined, papers));
  render.publications();
  const list = element('#content').innerHTML;
  assert.equal((list.match(/<article class="paper">/g) || []).length, papers.length);
  assert.deepEqual([...list.matchAll(/<h3><a href="#publications\/([^"]+)"/g)].map(match => match[1]), papers.map(p => p.id));
  assert.match(list, /id="paper-count"[^>]*>14 publications<\/span>/);
  assert.match(list, /href="#member\/amy"[^>]*>Exact signature<\/a>/);
  assert.doesNotMatch(list, /class="toolbar"|id="(?:paper-year|paper-type|paper-sort|clear-filters|pagination)"|data-page=|data-reset-papers/);
  render.setData(pageData(base(), 'publications', undefined, [paper('single')])); render.publications();
  assert.match(element('#content').innerHTML, /id="paper-count"[^>]*>1 publication<\/span>/);
  render.setData(pageData(base(), 'publications', undefined, [])); render.publications();
  const empty = element('#content').innerHTML;
  assert.match(empty, /0 publications/); assert.match(empty, /No publications listed yet\./);
  assert.doesNotMatch(empty, /filters|class="paper"|id="pagination"|data-page=/);
});

test('首頁保留全部合照且不顯示近期論文，英文名可空且不改作者署名', async () => {
  const {pageData} = await api, {render, element} = await renderers();
  render.setData(pageData(base(), 'home', undefined, {
    assets: [{id: 'a', url: '/assets/合照.jpg', metadata: {alt: 'Group A'}}, {id: 'b', url: '/assets/b.jpg', metadata: {alt: 'Group B'}}],
    recentPublications: [paper('third'), paper('second'), paper('first')], counts: {publications: 55},
  }));
  render.home(); const html = element('#content').innerHTML;
  assert.match(html, /\/assets\/合照.jpg/); assert.match(html, /\/assets\/b.jpg/);
  assert.match(html, /1 \/ 2/);
  assert.doesNotMatch(html, /hm-stories|FROM THE LAB|Recent publications/);
  assert.doesNotMatch(html, /View all 55 publications|>third<\/a>|>second<\/a>|>first<\/a>/);
  for (const englishName of ['Avery', null, '', '   ']) {
    const data = pageData(base(), 'member', undefined, [person({englishName})]); render.setData(data);
    assert.equal(data.people[0].name, englishName === 'Avery' ? 'An Example (Avery)' : 'An Example');
    assert.match(render.authors(paper('a')), />Exact signature<\/a>/);
  }
});

test('校友年月在列表和個人頁一致；空身份不誤判校友；保留API人名排序', async () => {
  const {pageData} = await api, {render, element} = await renderers();
  const alum = person({memberships: [{role: 'student', isCurrent: false}], alumniSince: '2025-06'});
  const unclassified = person({id: 'new-person', memberships: [], englishName: null});
  const data = pageData(base(), 'member', undefined, [alum, unclassified]); render.setData(data);
  assert.deepEqual(data.people.map(p => p.state), ['alumni', 'unclassified']);
  assert.deepEqual(data.people.map(p => p.id), ['amy', 'new-person']);
  render.people(); assert.match(element('#content').innerHTML, /datetime="2025-06">June 2025/);
  assert.match(element('#content').innerHTML, /<p class="eyebrow">Synthetic<\/p><h1>Members<\/h1>/);
  assert.match(element('#content').innerHTML, /Other members/);
  render.setData(pageData(base(), 'member', 'amy', {person: alum, publications: []}));
  render.people('amy'); assert.match(element('#content').innerHTML, /datetime="2025-06">June 2025/);
});

test('啟動先讀固定設定，再讀home；換頁重新讀API且無原始快照fallback', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async url => {
    calls.push(url);
    if (url === '/data/site.json') return response(base());
    if (url === '/api/home') return response({assets: [], recentPublications: [], counts: {publications: 0}});
    return response([person()]);
  });
  const {render, context} = await renderers(); await render.start();
  assert.deepEqual(calls, ['/data/site.json', '/api/home']);
  context.location.hash = '#member'; await render.route(); await render.route();
  assert.deepEqual(calls.slice(2), ['/api/people', '/api/people']);
  assert.ok(!calls.some(url => url.endsWith('lab.json')));
  const page = fs.readFileSync(path.join(root, 'public_frontend/dist/lab.js'), 'utf8');
  assert.doesNotMatch(page, /apiMode|data\/lab\.json|isDemo|publishState/);
});
