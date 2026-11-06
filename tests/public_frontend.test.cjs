// 程序內 API／渲染替身檢查，不啟動伺服器或瀏覽器。
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'public_frontend/dist/api.js'), 'utf8');
const api = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function response(body, status = 200, type = 'application/json') {
  return {ok: status === 200, status, headers: {get: () => type}, json: async () => body};
}

test('四個頁面各只呼叫指定API，成員詳情不額外取全體資料', async t => {
  const {readPage} = await api;
  const cases = [
    ['home', undefined, '/api/home', {assets: [], recentPublications: [], counts: {publications: 4}}, {assets: [], publications: [], counts: {publications: 4}}],
    ['member', undefined, '/api/people', [{id: 'p1'}], {people: [{id: 'p1'}]}],
    ['member', 'p1', '/api/people/p1', {person: {id: 'p1'}, publications: [{id: 'paper'}]}, {people: [{id: 'p1'}], publications: [{id: 'paper'}]}],
    ['publications', undefined, '/api/publications', [{id: 'paper'}], {publications: [{id: 'paper'}]}],
  ];
  for (const [page, id, url, body, expected] of cases) {
    const calls = [];
    t.mock.method(globalThis, 'fetch', async (target, options) => { calls.push({target, options}); return response(body); });
    assert.deepEqual(await readPage(page, id), expected);
    assert.equal(calls.length, 1); assert.equal(calls[0].target, url);
    assert.equal(calls[0].options.credentials, 'omit'); assert.equal(calls[0].options.cache, 'no-store');
    t.mock.restoreAll();
  }
});

test('讀取失敗或HTML回應拋錯，不回退lab.json或空陣列', async t => {
  const {readPage} = await api;
  for (const result of [response(null, 503), response(null, 200, 'text/html')]) {
    let calls = 0;
    t.mock.method(globalThis, 'fetch', async () => { calls++; return result; });
    await assert.rejects(readPage('home')); assert.equal(calls, 1); t.mock.restoreAll();
  }
});

test('固定頁面不請求內容庫，ID只進入編碼的路徑', async t => {
  const {readPage} = await api;
  const calls = [];
  t.mock.method(globalThis, 'fetch', async url => {calls.push(url); return response({person: {}, publications: []});});
  assert.deepEqual(await readPage('contact'), {}); assert.deepEqual(await readPage('news'), {});
  assert.equal(calls.length, 0);
  await readPage('member', 'bad/id'); assert.deepEqual(calls, ['/api/people/bad%2Fid']);
});

test('PDF路徑僅允許同源assets下檔案', async () => {
  const {pdfPath} = await api;
  for (const path of ['/assets/paper.pdf', '/assets/papers/paper-1.PDF']) assert.equal(pdfPath(path), path);
  for (const path of [null, '', '//evil/p.pdf', 'https://evil/p.pdf', '/etc/passwd', '/assets/../secret.pdf', '/assets/%2e%2e/p.pdf', '/assets/a.pdf?x=1', '/assets/a.pdf#x', '/assets/a.png', '/assets/a.pdf\n']) assert.equal(pdfPath(path), '');
});

// 執行原始渲染函式，確定不是僅驗證一份仿製HTML。
async function renderers() {
  const {pdfPath} = await api;
  let code = fs.readFileSync(path.join(root, 'public_frontend/dist/lab.js'), 'utf8');
  code = code.slice(0, code.indexOf('function home()')).replace(/^import .*;\n/gm, '');
  const context = vm.createContext({document: {querySelector: () => ({})}, URL, pdfPath});
  vm.runInContext(code + '\nglobalThis.render={paperActions,paperLink,image,authors,photoStack,memberName,memberLink,setData(value){data=value;}};', context);
  context.render.setData({assets: [], people: []});
  return context.render;
}

test('論文有pdfPath才產生同源download連結', async () => {
  const render = await renderers();
  assert.match(render.paperActions({pdfPath: '/assets/papers/a.pdf'}), /href="\/assets\/papers\/a.pdf" download>Download PDF/);
  for (const pdfPath of [null, 'javascript:alert(1)', 'https://evil/a.pdf']) assert.doesNotMatch(render.paperActions({pdfPath}), /Download PDF/);
});

test('照片讀metadata且文字跳脫；作者無需成員列表即可建立詳情連結', async () => {
  const render = await renderers();
  const html = render.image({url: '/assets/a.jpg', metadata: {alt: '<name>'}});
  assert.match(html, /alt="&lt;name&gt;"/); assert.doesNotMatch(html, /width=|height=/);
  assert.match(render.paperLink({pageUrl: '#publications/PAPER_1', title: 'Paper'}), /href="#publications\/PAPER_1"/);
  const authors = render.authors({authors: [{name: '<Author>', personId: 'amy'}, {name: 'External', personId: null}]});
  assert.match(authors, /href="#member\/amy"/); assert.match(authors, /&lt;Author&gt;/); assert.match(authors, /External/);
});

test('成員論文及論文物件保留API入庫倒序，不按年份重排', async () => {
  const source = fs.readFileSync(path.join(root, 'public_frontend/dist/models.js'), 'utf8');
  const {Member, Paper} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  const person = new Member({id: 'person'});
  const papers = [
    {id: 'new', year: 2020, authors: [{personId: 'person'}]},
    {id: 'old', year: 2026, authors: [{personId: 'person'}]},
  ].map(p => new Paper(p));
  assert.deepEqual(person.getPapers(papers).map(p => p.id), ['new', 'old']);
  assert.equal(papers[0].pageUrl, '#publications/new');
});

test('首頁相簿使用全部API合照；英文名可留空且不覆寫署名', async () => {
  const render = await renderers();
  render.setData({assets: [
    {id: 'new-a', url: '/assets/合照.jpg', metadata: {alt: 'Group A'}},
    {id: 'new-b', url: '/assets/group-b.jpg', metadata: {alt: 'Group B'}},
  ]});
  const photos = render.photoStack();
  assert.match(photos, /\/assets\/合照.jpg/);
  assert.match(photos, /\/assets\/group-b.jpg/);
  assert.match(photos, /1 \/ 2/);
  const person = {id: 'person', name: 'An Example', englishName: 'Avery'};
  assert.equal(render.memberName(person), 'An Example (Avery)');
  assert.match(render.memberLink(person), /An Example \(Avery\)/);
  for (const englishName of [null, '', '   ']) assert.equal(render.memberName({...person, englishName}), 'An Example');
  assert.match(render.authors({authors: [{personId: 'person', name: 'Exact signature'}]}), />Exact signature<\/a>/);
  for (const url of ['/assets/../secret.jpg', '/assets/%2e%2e/secret.jpg', '//evil/a.jpg', 'javascript:alert(1)', 'https://name:password@example.invalid/a.jpg']) {
    assert.equal(render.image({url, metadata: {alt: ''}}), '');
  }
});
