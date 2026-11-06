// Node 內建工具模擬 DOM/API，驗證登入與非同步邊界；不替代真實瀏覽器驗收。
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'admin_frontend/admin/index.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'admin_frontend/admin/app.js'), 'utf8');

// 只模擬本頁所需 DOM 操作；真實頁面的 ID/表單名稱從 HTML 取得。
class Element {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.events = {}; this.hidden = false; this.value = ''; this._text = ''; }
  set textContent(text) { this._text = String(text); this.children = []; }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(''); }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  getAttribute(key) { return this.attrs[key]; }
  removeAttribute(key) { delete this.attrs[key]; }
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  addEventListener(type, fn) { (this.events[type] ||= []).push(fn); }
  async emit(type) { for (const fn of this.events[type] || []) await fn({ currentTarget: this, preventDefault() {} }); }
  querySelectorAll(selector) {
    const attr = selector.match(/^\[([^=]+)=([^\]]+)\]$/);
    const match = (child) => attr ? child.attrs[attr[1]] === attr[2] : child.tag === selector;
    return this.children.flatMap((child) => [...(match(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  reset() { this.querySelectorAll('input').forEach((input) => { input.value = ''; }); }
  focus() {}
}

// 回應包裝與延後完成工具，用於重現網路回應順序顛倒。
const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const settle = () => new Promise((resolve) => setImmediate(resolve));
const identity = { user: { id: 'admin1', displayName: 'Test Admin', role: 'admin' }, csrf: 'test-csrf' };

// 載入真實 JS，API 以資料替身回應；不啟動 HTTP 服務或讀取正式資料庫。
function harness({ hash = '', session = false, override = () => undefined } = {}) {
  const nodes = {}, calls = [], events = {}, location = { hash };
  for (const match of html.matchAll(/<([a-z]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    nodes[match[3]] = new Element(match[1]);
    nodes[match[3]].hidden = /\bhidden\b/.test(match[2]);
  }
  for (const match of html.matchAll(/<form\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/form>/g)) {
    for (const control of match[2].matchAll(/<(input|button)\b([^>]*)>/g)) {
      const el = new Element(control[1]);
      for (const attr of control[2].matchAll(/([\w-]+)="([^"]*)"/g)) el.setAttribute(attr[1], attr[2]);
      nodes[match[1]].append(el);
    }
  }
  const schemas = Object.fromEntries(['people', 'publications', 'assets']
    .map((key) => [key, { type: 'object', properties: {} }]));
  const fallback = (url) => {
    if (url.endsWith('/session')) return session ? reply(identity) : reply({ detail: '需登入' }, 401);
    if (url.endsWith('/login')) return reply(identity);
    if (url.endsWith('/schema')) return reply(schemas);
    if (url.endsWith('/references')) return reply({ people: [], publications: [], assets: [] });
    if (url.endsWith('/users')) return reply({ items: [{ id: 'admin1', displayName: 'Test Admin', login: 'tester', active: true, version: 1 }, { id: 'second', login: 'second', displayName: 'Second Admin', active: false, version: 1 }] });
    if (url.includes('/audit')) return reply({ items: [], total: 0 });
    if (url.includes('/content/')) return reply({ items: [] });
    if (url.endsWith('/logout')) return reply({ ok: true });
    throw new Error(`Unexpected API: ${url}`);
  };
  const context = vm.createContext({
    document: { querySelector: (selector) => nodes[selector.slice(1)], createElement: (tag) => new Element(tag), createDocumentFragment: () => new Element('fragment') },
    location, history: { pushState: (_, __, hash) => { location.hash = hash; }, replaceState: (_, __, hash) => { location.hash = hash; } },
    window: { addEventListener: (name, fn) => { events[name] = fn; } },
    fetch: async (url, options) => { calls.push({ url, options }); return await (override(url, options) ?? fallback(url)); },
    FormData: class extends Map { constructor(form) { super(form.querySelectorAll('input').map((input) => [input.getAttribute('name'), input.value])); } },
    confirm: () => true, structuredClone, console,
  });
  vm.runInContext(script, context);
  return { context, nodes, calls, location, events, state: () => vm.runInContext('state', context) };
}

// 透過真實 submit 事件進入登入流程，確認表單欄位與 JSON 請求一致。
async function login(app) {
  for (const [key, value] of Object.entries({ login: 'tester', password: 'example-password' })) {
    app.nodes['login-form'].querySelector(`[name=${key}]`).value = value;
  }
  await app.nodes['login-form'].emit('submit');
}

test('匿名只能看到登入頁，登入成功後可切換全部管理功能', async () => {
  const app = harness(); await settle();
  assert.equal(app.nodes['admin-shell'].hidden, true);
  assert.equal(app.nodes['login-page'].hidden, false);
  assert.equal(app.calls.some((call) => call.url.includes('/admin/')), false);
  assert.doesNotMatch(html, /UMPASS|name="code"|reauth-dialog/);
  assert.match(html, /id="login-form"[^>]*method="post"/); // 未載入 JS 時也不能把密碼放入 GET URL。
  await login(app);
  assert.equal(app.nodes['admin-shell'].hidden, false);
  assert.equal(app.nodes['login-page'].hidden, true);
  assert.match(app.nodes.workspace.textContent, /管理總覽/);
  assert.equal(app.nodes.navigation.querySelectorAll('a').length, 7);
  for (const page of ['people', 'publications', 'assets', 'users', 'audit', 'account']) {
    await app.context.navigate(page);
    assert.equal(app.location.hash, `#/${page}`);
    if (page === 'users') assert.match(app.nodes.workspace.textContent, /Second Admin · second/);
    assert.ok(app.nodes.workspace.children.length);
  }
  const sent = app.calls.find((call) => call.url.endsWith('/login'));
  assert.deepEqual(JSON.parse(sent.options.body), { login: 'tester', password: 'example-password' });
  assert.equal(sent.options.credentials, 'same-origin');
  assert.equal(sent.options.cache, 'no-store');
});

test('帳號或密碼錯誤不顯示管理頁，也不載入管理資料', async () => {
  const app = harness({ override: (url) => url.endsWith('/login') ? reply({ detail: '帳號或密碼無效' }, 401) : undefined });
  await settle(); await login(app);
  assert.equal(app.nodes['admin-shell'].hidden, true);
  assert.match(app.nodes['login-error'].textContent, /無效/);
  assert.equal(app.state().user, null);
  assert.equal(app.calls.some((call) => call.url.includes('/admin/')), false);
  assert.equal(app.nodes['login-form'].querySelector('[name=password]').value, '');
});

test('既有 Session 重新整理後直接回到指定功能頁', async () => {
  const app = harness({ hash: '#/publications', session: true }); await settle();
  assert.equal(app.location.hash, '#/publications');
  assert.equal(app.nodes['admin-shell'].hidden, false);
  assert.match(app.nodes.workspace.textContent, /論文/);
  assert.equal(app.calls.some((call) => call.url.endsWith('/login')), false);
});

test('慢速舊頁面回應不能覆蓋已切換的新頁面', async () => {
  const pending = deferred();
  const app = harness({ session: true, override: (url) => url.endsWith('/content/people') ? pending.promise : undefined });
  await settle();
  const old = app.context.navigate('people');
  await app.context.navigate('publications');
  pending.resolve(reply({ items: [{ data: { id: 'p1', name: 'Old Member' }, version: 1 }] }));
  await old;
  assert.equal(app.location.hash, '#/publications');
  assert.match(app.nodes.workspace.textContent, /論文/);
  assert.doesNotMatch(app.nodes.workspace.textContent, /Old Member/);
});

test('Session 過期清空私人畫面，登出後舊請求也不能重新填入', async () => {
  let expired = true;
  const pending = deferred();
  const app = harness({ session: true, override: (url) => url.endsWith('/content/people')
    ? expired ? reply({ detail: '登入已過期' }, 401) : pending.promise : undefined });
  await settle(); await app.context.navigate('people');
  assert.equal(app.nodes['admin-shell'].hidden, true);
  assert.equal(app.nodes.workspace.children.length, 0);
  assert.equal(app.state().csrf, '');
  expired = false; app.state().nextPage = 'dashboard'; await login(app);
  const old = app.context.navigate('people');
  await app.nodes.logout.emit('click');
  pending.resolve(reply({ items: [{ data: { id: 'p1', name: 'Private Content' }, version: 1 }] }));
  await old;
  assert.equal(app.nodes['admin-shell'].hidden, true);
  assert.equal(app.nodes.workspace.children.length, 0);
  assert.equal(app.state().user, null);
  const logout = app.calls.find((call) => call.url.endsWith('/logout'));
  assert.equal(logout.options.headers['X-CSRF-Token'], 'test-csrf');
});

// 用 UI 事件送出帳戶資料；不直接呼叫 API helper，避免漏掉表單接線。
const findButton = (node, text) => node.querySelectorAll('button').find((el) => el.textContent === text);
function fill(form, values) {
  for (const [name, value] of Object.entries(values)) form.querySelector(`[name=${name}]`).value = value;
}
async function openNewUser(app) {
  await app.context.navigate('users');
  await findButton(app.nodes.workspace, '新增管理員').emit('click');
  const form = app.nodes.workspace.querySelector('form');
  assert.equal(form.getAttribute('method'), 'post');
  fill(form, { displayName: 'Third Admin', login: 'third', password: 'long-new-password', confirmPassword: 'long-new-password' });
  return form;
}

test('新增帳戶直接送出帳密，確認密碼不一致不送出，重複帳戶錯誤保留輸入', async () => {
  let duplicate = true;
  const app = harness({ session: true, override: (url, options) => {
    if (url.endsWith('/users') && options.method === 'POST') return duplicate
      ? reply({ detail: '登入名稱已存在' }, 409) : reply({ id: 'third' }, 201);
  } });
  await settle(); const form = await openNewUser(app);
  fill(form, { confirmPassword: 'mismatch' }); await form.emit('submit');
  assert.match(form.textContent, /兩次密碼不一致/);
  assert.equal(app.calls.some((call) => call.options.method === 'POST'), false);
  fill(form, { confirmPassword: 'long-new-password' }); await form.emit('submit');
  assert.match(form.textContent, /登入名稱已存在/);
  assert.equal(form.querySelector('[name=login]').value, 'third');
  duplicate = false; await form.emit('submit');
  const sent = app.calls.find((call) => call.options.method === 'POST');
  assert.deepEqual(JSON.parse(sent.options.body), { displayName: 'Third Admin', login: 'third', password: 'long-new-password' });
  assert.equal(sent.options.headers['X-CSRF-Token'], 'test-csrf');
  assert.equal(app.nodes.workspace.querySelector('[name=password]'), undefined);
  assert.match(app.nodes.notice.textContent, /管理員已建立/);
});

test('新增帳戶的慢速回應在登出後不能重新顯示管理頁', async () => {
  const pending = deferred();
  const app = harness({ session: true, override: (url, options) => url.endsWith('/users') && options.method === 'POST' ? pending.promise : undefined });
  await settle(); const form = await openNewUser(app);
  const submitted = form.emit('submit'); await settle();
  await app.nodes.logout.emit('click');
  pending.resolve(reply({ id: 'third' }, 201)); await submitted;
  assert.equal(app.nodes.workspace.children.length, 0);
  assert.equal(app.state().user, null);
});

test('內容頁完成新增、帶版本更新與刪除，衝突保留輸入', async () => {
  let row = null, conflict = true;
  const app = harness({ session: true, override: (url, options) => {
    if (url.endsWith('/schema')) return reply({ people: { type: 'object', properties: { id: { type: 'string' }, name: { type: 'string' } } } });
    if (url === '/api/v1/admin/content/people' && options.method === 'GET') return reply({ items: row ? [row] : [] });
    if (url === '/api/v1/admin/content/people' && options.method === 'POST') {
      row = { data: JSON.parse(options.body).data, version: 1 }; return reply(row, 201);
    }
    if (url === '/api/v1/admin/content/people/p1' && options.method === 'PUT') {
      if (conflict) return reply({ detail: '版本衝突' }, 409);
      row = { data: JSON.parse(options.body).data, version: 2 }; return reply(row);
    }
    if (url === '/api/v1/admin/content/people/p1?version=2' && options.method === 'DELETE') { row = null; return reply({ ok: true }); }
  } });
  await settle(); await app.context.navigate('people');
  await findButton(app.nodes.workspace, '新增').emit('click');
  let form = app.nodes.workspace.querySelector('form');
  const inputs = form.querySelectorAll('input'); inputs[0].value = 'p1'; inputs[1].value = 'New Person';
  await form.emit('submit');
  form = app.nodes.workspace.querySelector('form');
  form.querySelectorAll('input')[1].value = 'Changed';
  await form.emit('submit');
  assert.match(form.textContent, /版本衝突/);
  assert.equal(form.querySelectorAll('input')[1].value, 'Changed');
  conflict = false; await form.emit('submit');
  await findButton(app.nodes.workspace, '返回列表').emit('click');
  await findButton(app.nodes.workspace, '刪除').emit('click');
  assert.equal(row, null);
  const writes = app.calls.filter((call) => ['POST', 'PUT', 'DELETE'].includes(call.options.method));
  assert.deepEqual(writes.map((call) => call.options.method), ['POST', 'PUT', 'PUT', 'DELETE']);
  assert.equal(JSON.parse(writes[0].options.body).version, null);
  assert.equal(JSON.parse(writes[1].options.body).version, 1);
  assert.ok(writes.every((call) => call.options.headers['X-CSRF-Token'] === 'test-csrf'));
});

test('帳戶狀態、審計翻頁及密碼變更均由功能頁呼叫 API', async () => {
  const app = harness({ session: true, override: (url, options) => {
    if (url === '/api/v1/admin/users/second' && options.method === 'PATCH') return reply({ ok: true });
    if (url.includes('/audit')) return reply({ items: [], total: 101 });
    if (url.endsWith('/password')) return reply({ ok: true });
  } });
  await settle(); await app.context.navigate('users');
  const controls = app.nodes.workspace.querySelectorAll('input'); controls[1].checked = true;
  await app.nodes.workspace.querySelectorAll('button').filter((el) => el.textContent === '儲存狀態')[1].emit('click');
  const patch = app.calls.find((call) => call.options.method === 'PATCH');
  assert.deepEqual(JSON.parse(patch.options.body), { active: true, version: 1 });
  await app.context.navigate('audit'); await findButton(app.nodes.workspace, '下一頁').emit('click');
  assert.ok(app.calls.some((call) => call.url === '/api/v1/admin/audit?offset=100'));
  await app.context.navigate('account');
  const form = app.nodes.workspace.querySelector('form');
  fill(form, { currentPassword: 'current-password', newPassword: 'different-password', confirmPassword: 'different-password' });
  await form.emit('submit');
  const change = app.calls.find((call) => call.url.endsWith('/password'));
  assert.deepEqual(JSON.parse(change.options.body), { currentPassword: 'current-password', newPassword: 'different-password' });
  assert.equal(app.state().user, null);
  assert.equal(app.nodes.workspace.children.length, 0);
});

test('成員表單展示反向論文，送出僅含可寫欄位，照片可設為 null', async () => {
  const components = JSON.parse(fs.readFileSync(path.join(root, 'docs/admin-openapi.json'), 'utf8')).components.schemas;
  const schema = { ...components.Person, $defs: components };
  const record = { data: { id: 'amy', familyName: 'Chen', givenName: 'Amy', englishName: null, name: 'Amy Chen', publicationIds: ['paper-1'], portrait: null }, version: 1, created: {at: '2026-11-06T00:00:00Z', by: 'admin1'}, updated: {at: '2026-11-06T00:00:00Z', by: 'admin1'} };
  const app = harness({ session: true, override: (url, options) => {
    if (url.endsWith('/schema')) return reply({people: schema, assets: components.Asset, publications: components.Publication});
    if (url.endsWith('/references')) return reply({people: [{id: 'amy', label: 'Amy Chen'}], publications: [{id: 'paper-1', label: 'Linked Paper'}], assets: []});
    if (url.endsWith('/content/people')) return reply({items: [record]});
    if (url.endsWith('/content/people/amy') && options.method === 'PUT') return reply(record);
  }});
  await settle(); await app.context.navigate('people');
  await app.nodes.workspace.querySelectorAll('button').find(b => b.textContent === '開啟').emit('click');
  assert.match(app.nodes.workspace.textContent, /Linked Paper — paper-1/);
  assert.match(app.nodes.workspace.textContent, /familyName/);
  assert.match(app.nodes.workspace.textContent, /英文名（可留空）/);
  for (const removed of ['slug', 'affiliation', 'sourceUrl']) assert.equal(Object.hasOwn(schema.properties, removed), false);
  assert.match(app.nodes.workspace.textContent, /建立：2026-11-06T00:00:00Z · admin1/);
  assert.match(app.nodes.workspace.textContent, /更新：2026-11-06T00:00:00Z · admin1/);
  await app.nodes.workspace.querySelector('form').emit('submit');
  const call = app.calls.find(c => c.url.endsWith('/people/amy') && c.options.method === 'PUT');
  const body = JSON.parse(call.options.body);
  assert.equal(body.data.familyName, 'Chen'); assert.equal(body.data.givenName, 'Amy');
  assert.equal(body.data.portrait, null);
  assert.equal(body.data.englishName, null);
  assert.equal(Object.hasOwn(body.data, 'preferredName'), false);
  assert.equal(Object.hasOwn(body.data, 'name'), false);
  assert.equal(Object.hasOwn(body.data, 'publicationIds'), false);
  assert.equal(Object.hasOwn(body.data, 'created'), false);
  assert.equal(Object.hasOwn(body, 'created'), false);
  // 真實schema的可空照片物件能取消null，再編輯其url，無需選asset。
  const photo = app.context.field('portrait', schema.properties.portrait, null, schema);
  const absent = photo.box.querySelectorAll('input').find(i => i.attrs.type === 'checkbox');
  absent.checked = false; await absent.emit('change');
  const urlLabel = photo.box.querySelectorAll('label').find(l => l.textContent === 'url (url)');
  urlLabel.querySelector('input').value = '/assets/new-photo.jpg';
  assert.equal(photo.read().url, '/assets/new-photo.jpg');
  // 英文名沿用可空字串元件；合照用途沿用enum元件，不另外維護表單規格。
  const english = app.context.field('englishName', schema.properties.englishName, null, schema);
  const absentName = english.box.querySelectorAll('input').find(i => i.attrs.type === 'checkbox');
  absentName.checked = false; await absentName.emit('change');
  const nameInput = english.box.querySelectorAll('input').find(i => i !== absentName);
  nameInput.value = 'Avery'; assert.equal(english.read(), 'Avery');
  nameInput.value = ''; assert.equal(english.read(), '');
  absentName.checked = true; assert.equal(english.read(), null);
  const assetSchema = {...components.Asset, $defs: components};
  const purpose = app.context.field('purpose', components.AssetMetadata.properties.purpose, 'other', assetSchema);
  purpose.box.querySelector('select').value = 'group_photo';
  assert.equal(purpose.read(), 'group_photo');
});

test('新資產metadata表單與論文PDF欄位可編輯／設為null', async () => {
  const components = JSON.parse(fs.readFileSync(path.join(root, 'docs/admin-openapi.json'), 'utf8')).components.schemas;
  const app = harness({session: true}); await settle();
  const assetRoot = {...components.Asset, $defs: components};
  const meta = app.context.field('metadata', assetRoot.properties.metadata, {mimeType: 'image/jpeg', alt: 'Old', caption: null, credit: ''}, assetRoot);
  meta.box.querySelectorAll('label').find(l => l.textContent.endsWith('(alt)')).querySelector('input').value = 'New description';
  assert.equal(meta.read().alt, 'New description');
  assert.equal(Object.hasOwn(meta.read(), 'width'), false); assert.equal(Object.hasOwn(meta.read(), 'height'), false);
  const paperRoot = {...components.Publication, $defs: components};
  const pdf = app.context.field('pdfPath', paperRoot.properties.pdfPath, null, paperRoot);
  const inputs = pdf.box.querySelectorAll('input');
  const absent = inputs.find(i => i.attrs.type === 'checkbox');
  const pathInput = inputs.find(i => i !== absent);
  assert.equal(pdf.read(), null);
  absent.checked = false; await absent.emit('change'); pathInput.value = '/assets/papers/new.pdf';
  assert.equal(pdf.read(), '/assets/papers/new.pdf');
  absent.checked = true; await absent.emit('change'); assert.equal(pdf.read(), null);
});

test('儲存等待期間鎖定整份內容表單，失敗解鎖且保留輸入', async () => {
  const pending = deferred();
  const app = harness({ session: true, override: (url, options) => {
    if (url.endsWith('/schema')) return reply({ people: {type: 'object', properties: {id: {type: 'string'}, givenName: {type: 'string'}}} });
    if (url.endsWith('/content/people') && options.method === 'POST') return pending.promise;
  }});
  await settle(); await app.context.navigate('people');
  await findButton(app.nodes.workspace, '新增').emit('click');
  const form = app.nodes.workspace.querySelector('form'), group = form.querySelector('fieldset');
  form.querySelectorAll('input')[0].value = 'new-person';
  form.querySelectorAll('input')[1].value = 'Unsaved name';
  await form.emit('input');
  const submitted = form.emit('submit'); await settle();
  assert.equal(group.disabled, true); assert.equal(app.state().dirty, true);
  pending.resolve(reply({detail: 'Too large'}, 422)); await submitted;
  assert.equal(group.disabled, false);
  assert.equal(form.querySelectorAll('input')[1].value, 'Unsaved name');
  assert.equal(app.state().dirty, true);
  assert.match(form.textContent, /Too large/);
});

test('帳戶表單先收集輸入再鎖定，請求失敗後解鎖', async () => {
  const pending = deferred();
  const app = harness({session: true, override: (url, options) => url.endsWith('/users') && options.method === 'POST' ? pending.promise : undefined});
  await settle(); const form = await openNewUser(app);
  const submitted = form.emit('submit'); await settle();
  assert.equal(form.querySelector('fieldset').disabled, true);
  assert.equal(JSON.parse(app.calls.find(c => c.options.method === 'POST').options.body).login, 'third');
  pending.resolve(reply({detail: 'Conflict'}, 409)); await submitted;
  assert.equal(form.querySelector('fieldset').disabled, false);
  assert.equal(form.querySelector('[name=login]').value, 'third');
});
