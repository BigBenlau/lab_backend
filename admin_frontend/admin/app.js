// 原生 DOM 表單；不使用 innerHTML、localStorage 或第三方腳本。
// schema 只決定欄位；真正的授權、容量與關聯驗證全部由 API 執行。
const $ = (selector) => document.querySelector(selector);
const state = { user: null, csrf: '', schemas: {}, refs: {}, dirty: false, page: 'dashboard', nextPage: 'dashboard', view: 0, epoch: 0 };
const titles = { people: '成員與校友', publications: '論文', assets: '資產連結' };
const pages = { dashboard: '總覽', ...titles, users: '管理員帳戶', audit: '操作紀錄', account: '修改密碼' };
const labels = { metadata: '資產描述', purpose: '用途（group_photo＝首頁合照，other＝其他）', mimeType: '媒體類型', alt: '替代文字', caption: '說明', credit: '來源／作者', pdfPath: 'PDF 路徑（/assets/...pdf，無檔案時選擇 null）', id: 'ID（穩定識別碼）', name: '名稱', title: '標題／職銜', authors: '作者（依顯示順序）', personId: '關聯成員', year: '年份', venue: '出版會議／期刊', sourceUrl: '來源網址', familyName: '姓氏', givenName: '名字', englishName: '英文名（可留空）', portrait: '個人照片', bioMarkdown: '個人介紹（Markdown）', links: '連結', memberships: '身份／成員類別', isCurrent: '目前身份', publicEmail: '公開 Email', role: '角色', displayName: '顯示名稱' };
const relations = { personId: 'people' };

// ── 共用 DOM 與 API 操作 ──
// 建立純文字 DOM 節點，避免內容被當作 HTML 執行。
function element(tag, text, attrs = {}) {
  const el = document.createElement(tag);
  if (text !== undefined && text !== null) el.textContent = text;
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
}
// 建立不會意外提交表單的按鈕，綁定操作事件。
function button(text, action, attrs = {}) {
  const el = element('button', text, { type: 'button', ...attrs });
  el.addEventListener('click', action);
  return el;
}
// 以純文字顯示操作結果或錯誤。
function notice(message, error = false) {
  $('#notice').textContent = message;
  $('#notice').className = error ? 'error' : '';
}
// 僅允許已知頁面；網址不能決定任意 API 路徑。
function pageFromHash() {
  const page = location.hash.replace(/^#\//, '');
  return Object.hasOwn(pages, page) ? page : 'dashboard';
}
// pushState 不額外觸發 hashchange，避免同一頁重複讀取。
function pageURL(page, replace = false) {
  if (location.hash !== `#/${page}`) history[replace ? 'replaceState' : 'pushState'](null, '', `#/${page}`);
}
// 過期或登出時移除私人表單與憑證；epoch 使尚未完成的舊請求失效。
function showLogin(message = '') {
  if (state.user) state.nextPage = state.page;
  state.epoch += 1; state.view += 1;
  state.user = null; state.csrf = ''; state.schemas = {}; state.refs = {}; state.dirty = false;
  $('#admin-shell').hidden = true; $('#login-page').hidden = false;
  $('#workspace').replaceChildren(); $('#navigation').replaceChildren(); $('#identity').textContent = '';
  $('#login-form').reset(); $('#login-error').textContent = message;
  notice(''); pageURL('login', true); document.title = '管理員登入 · Lab 內容管理';
  $('#login-form').querySelector('[name=login]').focus();
}
// 頁面切換或登入身份改變後，舊的非同步結果不得覆蓋目前畫面。
function currentView(view) {
  return Boolean(state.user) && view === state.view;
}
// 統一同源 Cookie、CSRF、no-store 及錯誤訊息處理。
async function api(path, options = {}) {
  const epoch = state.epoch;
  const response = await fetch(path, {
    method: options.method || 'GET', credentials: 'same-origin', cache: 'no-store',
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(state.csrf ? { 'X-CSRF-Token': state.csrf } : {}) },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json().catch(() => ({ detail: '服務回應格式不正確' }));
  if (epoch !== state.epoch) throw new Error('先前工作階段的請求已取消');
  if (!response.ok) {
    if (response.status === 401 && !path.endsWith('/login') && !path.endsWith('/session')) showLogin('登入已失效，請重新登入；未儲存內容已清除。');
    const fields = data.fields ? `：${data.fields.map((p) => p.join('.')).join('、')}` : '';
    throw Object.assign(new Error(`${data.detail || '操作失敗'}${fields}`), { status: response.status });
  }
  return data;
}
// ── JSON Schema 表單 ──
// 將 JSON Schema 的 $ref 展開為同一份規格中的定義。
function resolve(schema, root) {
  if (schema.$ref) return root.$defs[schema.$ref.split('/').at(-1)];
  return schema;
}
// 取得可空欄位的非 null 型別，供表單選擇元件。
function nonnull(schema, root) {
  schema = resolve(schema, root);
  return resolve(schema.anyOf?.find((s) => s.type !== 'null') || schema, root);
}
// 依規格建立空表單值；不使用 lab.json 範例內容。
function initial(schema, root, key = '') {
  schema = resolve(schema, root);
  if ('default' in schema) return structuredClone(schema.default);
  if (schema.anyOf?.some((s) => s.type === 'null')) return null;
  if (schema.enum) return schema.enum[0];
  if (schema.type === 'object') return Object.fromEntries(Object.entries(schema.properties).filter(([, s]) => !s.readOnly).map(([k, s]) => [k, initial(s, root, k)]));
  if (schema.type === 'array') return [];
  if (schema.type === 'boolean') return false;
  if (schema.type === 'integer' || schema.type === 'number') return key === 'year' ? new Date().getFullYear() : (schema.minimum || 0);
  return '';
}

// 每個欄位回傳 read()，讓巢狀陣列保持順序，不需框架或隱藏的狀態同步。
function field(key, spec, value, root, context = {}) {
  spec = resolve(spec, root);
  const schema = nonnull(spec, root);
  const nullable = spec.anyOf?.some((s) => s.type === 'null');
  const box = element('div', null, { class: 'field' });
  if (spec.readOnly) {
    return { box: document.createDocumentFragment(), read: () => value };
  }
  if (schema.type === 'object') {
    const group = element('fieldset');
    group.append(element('legend', labels[key] || key));
    const children = Object.entries(schema.properties).map(([k, s]) => [k, field(k, s, value?.[k] ?? initial(s, root, k), root, context)]);
    children.forEach(([, child]) => group.append(child.box));
    box.append(group);
    let absent;
    if (nullable) {
      const option = element('label', '不設定照片');
      absent = element('input', null, { type: 'checkbox' }); absent.checked = value === null;
      group.disabled = absent.checked;
      absent.addEventListener('change', () => { group.disabled = absent.checked; });
      option.prepend(absent); box.prepend(option);
    }
    return { box, read: () => absent?.checked ? null : Object.fromEntries(children.map(([k, child]) => [k, child.read()]).filter(([, v]) => v !== undefined)) };
  }
  if (schema.type === 'array') {
    const group = element('fieldset');
    group.append(element('legend', labels[key] || key));
    const rowsBox = element('div');
    const rows = [];
    // DOM 順序始終跟隨 rows；論文作者順序不可排序丟失。
    function reorder() { rowsBox.replaceChildren(...rows.map((row) => row.el)); }
    // 新增一個可排序的陣列項目，所有變更同步標記未儲存狀態。
    function add(item) {
      const row = { el: element('div', null, { class: 'array-row' }) };
      row.field = field('項目', schema.items, item, root, context);
      row.el.append(row.field.box);
      const controls = element('div', null, { class: 'row-actions' });
      for (const [label, delta] of [['上移', -1], ['下移', 1]]) {
        controls.append(button(label, () => {
          const index = rows.indexOf(row), next = index + delta;
          if (next >= 0 && next < rows.length) {
            [rows[index], rows[next]] = [rows[next], rows[index]];
            state.dirty = true; reorder();
          }
        }));
      }
      controls.append(button('移除', () => { rows.splice(rows.indexOf(row), 1); state.dirty = true; reorder(); }));
      row.el.append(controls); rows.push(row); reorder();
    }
    (value || []).forEach(add);
    group.append(rowsBox, button('新增一項', () => {
      if (rows.length >= (schema.maxItems || 1000)) return notice('已達此欄位上限', true);
      add(initial(schema.items, root)); state.dirty = true;
    }));
    box.append(group);
    return { box, read: () => rows.map((row) => row.field.read()) };
  }
  const label = element('label', `${labels[key] || key} (${key})`);
  let input;
  if (relations[key] || schema.enum) {
    input = element('select');
    if (relations[key]) {
      input.append(element('option', '未指定', { value: '' }));
      for (const item of state.refs[relations[key]] || []) input.append(element('option', `${item.label} — ${item.id}`, { value: item.id }));
      if (value && ![...input.options].some((o) => o.value === value)) input.append(element('option', `${value}（目前不可讀）`, { value }));
    } else {
      schema.enum.forEach((v) => input.append(element('option', v, { value: v })));
    }
    input.value = value ?? '';
  } else if (schema.type === 'boolean') {
    input = element('input', null, { type: 'checkbox' }); input.checked = Boolean(value);
  } else if (schema.type === 'integer' || schema.type === 'number') {
    input = element('input', null, { type: 'number', step: schema.type === 'integer' ? '1' : 'any' });
    input.value = value ?? '';
    if (schema.minimum !== undefined) input.min = schema.minimum;
    if (schema.exclusiveMinimum !== undefined) input.min = schema.exclusiveMinimum + 1;
    if (schema.maximum !== undefined) input.max = schema.maximum;
  } else {
    input = element(/Markdown|caption/.test(key) ? 'textarea' : 'input');
    input.value = value ?? '';
    input.maxLength = schema.maxLength || 50000;
    if (schema.minLength) input.minLength = schema.minLength;
    if (schema.pattern) input.pattern = schema.pattern;
  }
  input.disabled = key === 'id' && context.existing;
  label.append(input); box.append(label);
  let nullInput;
  if (nullable && !relations[key]) {
    const nullLabel = element('label', '保持 null（未提供）', { class: 'null-option' });
    nullInput = element('input', null, { type: 'checkbox' }); nullInput.checked = value === null;
    input.disabled ||= nullInput.checked;
    nullInput.addEventListener('change', () => { input.disabled = nullInput.checked; });
    nullLabel.prepend(nullInput); box.append(nullLabel);
  }
  return { box, read: () => {
    if (nullInput?.checked || (nullable && relations[key] && !input.value)) return null;
    if (schema.type === 'boolean') return input.checked;
    if (schema.type === 'integer' || schema.type === 'number') {
      if (input.value === '') throw new Error(`${key} 需要數值，或明確選擇 null`);
      return Number(input.value);
    }
    return input.value;
  } };
}

// ── 內容管理 ──
// 組合欄位元件與 read()，儲存時才讀取目前輸入。
function formFields(schema, data, existing = false) {
  const fields = Object.entries(schema.properties).map(([key, spec]) => [key, field(key, spec, data[key], schema, { existing })]);
  return { nodes: fields.map(([, item]) => item.box), read: () => Object.fromEntries(fields.map(([key, item]) => [key, item.read()]).filter(([, v]) => v !== undefined)) };
}
// 新增/編輯完整記錄，帶版本提交；錯誤時保留使用者輸入。
function editContent(resource, record = null) {
  const view = state.view;
  const schema = state.schemas[resource], data = record?.data || initial(schema, schema);
  const workspace = $('#workspace'); workspace.replaceChildren(element('h2', `${record ? '編輯' : '新增'}：${titles[resource]}`));
  workspace.append(element('p', '儲存後立即更新公開網站內容；ID 建立後不可修改。'));
  // 建立／修改資訊是伺服器維護的整體，只展示、不加入可編輯表單。
  if (record?.created && record?.updated) {
    workspace.append(element('p', `建立：${record.created.at} · ${record.created.by}`),
      element('p', `更新：${record.updated.at} · ${record.updated.by}`));
  }
  // 關聯只讀展示；編輯論文 authors.personId 才會變更來源，不另存反向清單。
  if (resource === 'people' && record) {
    const related = element('section'); related.append(element('h3', '關聯論文'));
    for (const id of data.publicationIds || []) {
      const paper = (state.refs.publications || []).find((item) => item.id === id);
      related.append(element('p', `${paper?.label || id} — ${id}`));
    }
    if (!data.publicationIds?.length) related.append(element('p', '目前沒有關聯論文。請在論文作者中選擇此成員。'));
    workspace.append(related);
  }
  const form = element('form'), controls = formFields(schema, data, Boolean(record));
  // 整組鎖定避免等待回應時繼續輸入，被成功回應覆蓋；不改動子欄位原本的disabled狀態。
  const fields = element('fieldset'); fields.append(...controls.nodes); form.append(fields);
  form.addEventListener('input', () => { state.dirty = true; });
  form.addEventListener('change', () => { state.dirty = true; });
  const error = element('p', '', { role: 'alert', class: 'error' });
  const save = element('button', '儲存', { type: 'submit' });
  form.append(error, save, button('返回列表', () => navigate(resource)));
  form.addEventListener('submit', async (event) => {
    event.preventDefault(); error.textContent = ''; save.disabled = true; fields.disabled = true;
    try {
      const changed = controls.read();
      const path = `/api/v1/admin/content/${resource}` + (record ? `/${encodeURIComponent(data.id)}` : '');
      const result = await api(path, { method: record ? 'PUT' : 'POST', body: { data: changed, version: record?.version ?? null } });
      if (!currentView(view)) return;
      state.dirty = false; notice('已儲存'); editContent(resource, result);
    } catch (e) { error.textContent = `${e.message}。目前輸入已保留。`; }
    finally { save.disabled = false; fields.disabled = false; }
  });
  workspace.append(form);
}
// 重新讀取關聯與內容清單；刪除同樣帶版本避免覆寫。
async function contentList(resource, view = state.view) {
  const [refs, result] = await Promise.all([api('/api/v1/admin/references'), api(`/api/v1/admin/content/${resource}`)]);
  if (!currentView(view)) return;
  state.refs = refs;
  const workspace = $('#workspace'); workspace.replaceChildren(element('h2', titles[resource]));
  // 30 人／300 篇僅用於性能規劃；畫面顯示實際數量，不當作新增限制。
  workspace.append(element('p', `${result.items.length} 筆記錄`), button('新增', () => editContent(resource)));
  if (!result.items.length) {
    workspace.append(element('p', '目前沒有記錄，點選「新增」建立第一筆內容。', { class: 'empty-state' }));
    return;
  }
  const table = element('table');
  const header = element('tr'); ['名稱', '版本', '操作'].forEach((s) => header.append(element('th', s))); table.append(header);
  for (const record of result.items) {
    const row = element('tr'), data = record.data, actions = element('td');
    row.append(element('td', data.name || data.title || data.metadata?.alt || data.id), element('td', record.version));
    actions.append(button('開啟', () => editContent(resource, record)));
    actions.append(button('刪除', async () => {
      if (!confirm(`確定刪除 ${data.id}？有關聯的記錄將拒絕刪除。`)) return;
      try {
        await api(`/api/v1/admin/content/${resource}/${encodeURIComponent(data.id)}?version=${record.version}`, { method: 'DELETE' });
        if (currentView(view)) { await contentList(resource, view); if (currentView(view)) notice('已刪除'); }
      } catch (e) { if (currentView(view)) notice(e.message, true); }
    }));
    row.append(actions); table.append(row);
  }
  workspace.append(table);
}
// ── 總覽、帳戶與操作紀錄 ──
// 總覽只查管理 API 的關聯摘要；數量每次進入頁面重新取得。
async function dashboard(view) {
  const refs = await api('/api/v1/admin/references');
  if (!currentView(view)) return;
  const workspace = $('#workspace');
  workspace.replaceChildren(element('h2', '管理總覽'), element('p', '選擇一個功能開始管理內容。儲存成功後，公開網站即可讀取最新資料。', { class: 'muted' }));
  const stats = element('div', null, { class: 'stats-grid' });
  for (const key of ['people', 'publications', 'assets']) {
    const card = element('div', null, { class: 'stat-card' });
    card.append(element('span', titles[key]), element('strong', refs[key].length), button('前往管理', () => navigate(key)));
    stats.append(card);
  }
  const links = element('div', null, { class: 'quick-links' });
  for (const key of ['users', 'audit', 'account']) links.append(button(pages[key], () => navigate(key)));
  workspace.append(stats, element('h3', '其他功能'), links);
}
// 帳戶頁只管理本機帳密，角色固定為 admin。
async function users(view = state.view) {
  const { items } = await api('/api/v1/admin/users');
  if (!currentView(view)) return;
  const workspace = $('#workspace');
  workspace.replaceChildren(element('h2', '管理員帳戶'), element('p', '新增帳戶後即可用帳號密碼登入；可在此啟用或停用帳戶。'), button('新增管理員', () => newUser()));
  for (const user of items) {
    const row = element('div', null, { class: 'user-row' });
    row.append(element('span', `${user.displayName} · ${user.login}`));
    const active = element('input', null, { type: 'checkbox' }); active.checked = user.active;
    const activeLabel = element('label', '啟用', { class: 'inline-label' }); activeLabel.prepend(active);
    const save = button('儲存狀態', async () => {
      save.disabled = true; active.disabled = true;
      try {
        await api(`/api/v1/admin/users/${encodeURIComponent(user.id)}`, { method: 'PATCH', body: { active: active.checked, version: user.version } });
        if (!currentView(view)) return;
        // 修改自己的帳戶會撤銷目前 Session，不能繼續使用舊憑證。
        if (user.id === state.user.id) return showLogin('帳戶已更新，請重新登入。');
        state.dirty = false; await users(view); if (currentView(view)) notice('帳戶狀態已更新');
      } catch (e) { if (currentView(view)) notice(e.message, true); }
      finally { save.disabled = false; active.disabled = false; }
    });
    active.addEventListener('change', () => { state.dirty = true; });
    row.append(activeLabel, save); workspace.append(row);
  }
}
// 帳戶新增與密碼變更共用原生表單，欄位、確認密碼及送出狀態只實作一次。
function accountForm(title, description, fields, saveLabel, onSave) {
  const view = ++state.view, workspace = $('#workspace');
  workspace.replaceChildren(element('h2', title), element('p', description));
  const form = element('form', null, { method: 'post' });
  const group = element('fieldset'); form.append(group);
  for (const [name, text, attrs] of fields) {
    const label = element('label', text);
    label.append(element('input', null, { name, required: '', ...attrs })); group.append(label);
  }
  const error = element('p', '', { role: 'alert', class: 'error' });
  const submit = element('button', saveLabel, { type: 'submit' });
  form.append(error, submit, button('返回', () => navigate(state.page)));
  form.addEventListener('input', () => { state.dirty = true; });
  form.addEventListener('submit', async (event) => {
    event.preventDefault(); error.textContent = '';
    const data = Object.fromEntries(new FormData(form));
    submit.disabled = true; group.disabled = true;
    try {
      if (data.confirmPassword !== (data.newPassword ?? data.password)) throw new Error('兩次密碼不一致');
      delete data.confirmPassword;
      await onSave(data, view);
    } catch (e) { if (currentView(view)) error.textContent = e.message; }
    finally { submit.disabled = false; group.disabled = false; }
  });
  workspace.append(form);
}
// 新密碼欄位沿用同一組限制；目前密碼只檢查是否正確，不套新密碼下限。
const newPasswordAttrs = { type: 'password', minlength: '15', maxlength: '128', autocomplete: 'new-password' };
// 帳戶直接提交，不需要 TOTP 設定或暫存註冊狀態。
function newUser() {
  accountForm('新增管理員', '填寫帳號密碼即可建立管理員；所有帳戶均有內容修改權限。', [
    ['displayName', '顯示名稱', { maxlength: '500' }],
    ['login', '登入名稱（3–80 字元，小寫英數、點、底線或連字號；首字為英數）', { pattern: '[a-z0-9][a-z0-9._-]{2,79}', maxlength: '80', autocomplete: 'off' }],
    ['password', '密碼（15–128 字元）', newPasswordAttrs],
    ['confirmPassword', '再次輸入密碼', newPasswordAttrs],
  ], '建立帳戶', async (data, view) => {
    await api('/api/v1/admin/users', { method: 'POST', body: data });
    if (currentView(view)) { state.dirty = false; await navigate('users'); notice('管理員已建立'); }
  });
}
// 分頁查操作紀錄，不把全部歷史讀入瀏覽器；切頁時仍核對目前畫面版本。
async function audit(offset = 0, view = state.view) {
  const result = await api(`/api/v1/admin/audit?offset=${offset}`);
  if (!currentView(view)) return;
  if (result.warnings?.length) notice(result.warnings.join('；'), true);
  const workspace = $('#workspace'); workspace.replaceChildren(element('h2', '操作紀錄'), element('p', `共 ${result.total} 筆紀錄`));
  const table = element('table'), header = element('tr');
  ['時間', '操作者', '操作', '對象', '結果'].forEach((title) => header.append(element('th', title)));
  table.append(header);
  for (const row of result.items) {
    const tr = element('tr'); ['at', 'actor', 'action', 'target', 'result'].forEach((key) => tr.append(element('td', row[key]))); table.append(tr);
  }
  workspace.append(result.items.length ? table : element('p', '目前沒有操作紀錄。', { class: 'empty-state' }));
  const paging = element('div', null, { class: 'row-actions' });
  // 每次翻頁更新 view，避免快速點擊造成較舊的頁面回應蓋過新頁。
  const changePage = (next) => audit(next, ++state.view).catch((e) => notice(e.message, true));
  if (offset) paging.append(button('上一頁', () => changePage(Math.max(0, offset - 100))));
  if (offset + 100 < result.total) paging.append(button('下一頁', () => changePage(offset + 100)));
  workspace.append(paging);
}
// 密碼變更只要求目前密碼，成功後後端撤銷舊 Session，畫面回到登入頁。
function account() {
  accountForm('修改密碼', '輸入目前及新密碼。變更成功後請重新登入。', [
    ['currentPassword', '目前密碼', { type: 'password', maxlength: '128', autocomplete: 'current-password' }],
    ['newPassword', '新密碼（15–128 字元）', newPasswordAttrs],
    ['confirmPassword', '再次輸入新密碼', newPasswordAttrs],
  ], '變更密碼', async (data) => {
    await api('/api/v1/auth/password', { method: 'POST', body: data });
    showLogin('密碼已變更，請重新登入。');
  });
}
// ── 導覽與登入狀態 ──
// hash 表示功能頁；切頁前確認未儲存變更，非同步回應只可更新原頁面。
async function navigate(page, replace = false) {
  if (!Object.hasOwn(pages, page)) page = 'dashboard';
  if (!state.user) { state.nextPage = page; showLogin(); return; }
  if (state.dirty && !confirm('目前有未儲存輸入，確定離開？')) { pageURL(state.page, true); return; }
  state.page = page; state.dirty = false;
  const view = ++state.view;
  pageURL(page, replace); notice(''); document.title = `${pages[page]} · Lab 內容管理`;
  for (const link of $('#navigation').querySelectorAll('a')) {
    if (link.getAttribute('href') === `#/${page}`) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  $('#workspace').replaceChildren(element('p', '正在讀取…', { role: 'status' }));
  try {
    if (page === 'dashboard') await dashboard(view);
    else if (page === 'users') await users(view);
    else if (page === 'audit') await audit(0, view);
    else if (page === 'account') account();
    else await contentList(page, view);
  } catch (e) {
    if (currentView(view)) $('#workspace').replaceChildren(element('h2', pages[page]), element('p', e.message, { class: 'error', role: 'alert' }), button('重新讀取', () => navigate(page, true)));
  }
  if (currentView(view)) $('#workspace').focus();
}
// 先確認身份及取得表單規格，才顯示登入後的側欄與操作頁。
async function signedIn(data) {
  if (data.user.role !== 'admin') throw new Error('後台僅限管理員使用');
  state.user = data.user; state.csrf = data.csrf;
  state.schemas = await api('/api/v1/admin/schema');
  $('#identity').textContent = state.user.displayName;
  const nav = $('#navigation'); nav.replaceChildren();
  for (const [key, title] of Object.entries(pages)) {
    const link = element('a', title, { href: `#/${key}` });
    link.addEventListener('click', (event) => { event.preventDefault(); navigate(key); });
    nav.append(link);
  }
  $('#login-form').reset(); $('#login-error').textContent = '';
  $('#login-page').hidden = true; $('#admin-shell').hidden = false;
  await navigate(state.nextPage, true);
}
// 載入時恢復 Cookie Session；正常未登入不顯示錯誤，服務故障則提示。
async function bootstrap() {
  state.nextPage = pageFromHash();
  const epoch = state.epoch;
  try { await signedIn(await api('/api/v1/auth/session')); }
  catch (e) { if (epoch === state.epoch) showLogin(e.status === 401 ? '' : e.message); }
}
// ── 頁面事件：表單與導覽只呼叫 API，不把憑證存入瀏覽器儲存。 ──
$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget, submit = form.querySelector('[type=submit]'); submit.disabled = true;
  const epoch = ++state.epoch; // 明確登入優先於稍後回來的初始 Session 查詢。
  $('#login-error').textContent = '';
  try { await signedIn(await api('/api/v1/auth/login', { method: 'POST', body: Object.fromEntries(new FormData(form)) })); }
  catch (e) {
    if (epoch === state.epoch) showLogin(e.message);
  } finally { submit.disabled = false; }
});
$('#logout').addEventListener('click', async () => {
  if (state.dirty && !confirm('未儲存輸入將清除，確定登出？')) return;
  $('#logout').disabled = true;
  try { await api('/api/v1/auth/logout', { method: 'POST' }); showLogin('已登出。'); state.nextPage = 'dashboard'; }
  catch (e) { notice(e.message, true); }
  finally { $('#logout').disabled = false; }
});
window.addEventListener('hashchange', () => {
  if (state.user) navigate(pageFromHash(), true);
  else if (location.hash !== '#/login') { state.nextPage = pageFromHash(); pageURL('login', true); }
});
window.addEventListener('beforeunload', (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
bootstrap();
