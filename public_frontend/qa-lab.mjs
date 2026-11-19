import assert from 'node:assert/strict';
// 僅對接已備妥合成資料的本機測試站。先確認標記，再讀四個公開 API。
const origin = 'http://127.0.0.1:4173';
async function read(path) {
  const response = await fetch(new URL(path, origin), {redirect: 'error'});
  assert(response.ok, '無法讀取本機測試 API');
  return response.json();
}
const {siteSettings} = await read('/data/site.json');
assert.equal(siteSettings.name, 'Synthetic Lab', '請使用名為 Synthetic Lab 的合成測試站');
const [home, people, papers] = await Promise.all(['/api/home','/api/people','/api/publications'].map(read));
const albumSize = home.assets.length;
const member = people.find(p => papers.some(paper => paper.authors.some(a => a.personId === p.id)));
assert(albumSize >= 2 && member && papers.length > 12, '此瀏覽器案例需要多圖、具論文的成員及第二頁論文');
const detail = await read('/api/people/' + encodeURIComponent(member.id));
assert(detail.publications.length > 0, '成員資料應回傳關聯論文');
const memberRoute = 'member/' + encodeURIComponent(member.id);
const displayName = p => p.englishName && !p.name.toLowerCase().includes(p.englishName.toLowerCase())
  ? p.name + ' (' + p.englishName + ')' : p.name;
const targets=await (await fetch('http://127.0.0.1:9224/json')).json();
const socket=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
await new Promise(resolve=>socket.addEventListener('open',resolve,{once:true}));
let next=0;const pending=new Map(),errors=[],requests=[];
socket.addEventListener('message',event=>{const message=JSON.parse(event.data);if(message.id){const p=pending.get(message.id);pending.delete(message.id);message.error?p.reject(message.error):p.resolve(message.result);}else if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails.text);else if(message.method==='Network.requestWillBeSent')requests.push(message.params.request.url);});
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++next;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const settle=()=>evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
const ready=async()=>{await settle();await waitFor("document.querySelector('#content h1') && !document.querySelector('#content .loading')");};
const navigate=async hash=>{await evaluate(`location.hash=${JSON.stringify(hash)}`);await ready();};
const check=async(expression,label)=>{assert(await evaluate(expression),label);console.log('PASS '+label);};
const waitFor=async expression=>{for(let i=0;i<60;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,100));}throw new Error('Timed out: '+expression);};
await send('Runtime.enable');await send('Page.enable');await send('Network.enable');
await send('Network.setCacheDisabled',{cacheDisabled:true});
await send('Page.navigate',{url:'about:blank'});await waitFor("location.href==='about:blank'");
errors.length=0;
await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1080,deviceScaleFactor:1,mobile:false});
await send('Page.navigate',{url:origin + '/#home'});
await waitFor("document.querySelector('.hm-hero') && document.querySelector('.group-photo')?.naturalWidth>0");
assert(requests.some(url=>new URL(url).pathname==='/data/site.json'),'Expected site settings requested');
assert(!requests.some(url=>new URL(url).pathname==='/data/lab.json'),'API mode never reads the static snapshot');
await check("getComputedStyle(document.body).backgroundColor==='rgb(242, 234, 219)'",'original third-direction palette');
await check("getComputedStyle(document.body).fontSize==='20px'",'larger body type');
await check("document.querySelector('.brand img').naturalWidth > 0 && document.querySelector('.brand').getBoundingClientRect().left < innerWidth/2",'logo at top left');
await check("getComputedStyle(document.querySelector('.site-header')).backgroundColor==='rgb(226, 232, 220)' && getComputedStyle(document.querySelector('.brand')).backgroundColor==='rgba(0, 0, 0, 0)' && getComputedStyle(document.querySelector('.brand')).borderTopWidth==='0px' && document.querySelector('.brand img').getBoundingClientRect().bottom<=document.querySelector('.site-header').getBoundingClientRect().bottom",'sage navigation with transparent logo and no backing');
await check(`document.querySelectorAll('.stack-sheet').length===${albumSize} && !!document.querySelector('.hero-wordmark img')`,'photo stack and wordmark');
await check("document.querySelector('.footer-wordmark').naturalWidth>0 && !document.querySelector('.footer-links')",'wordmark footer');
await check("!document.querySelector('.hm-research') && !document.querySelector('a[href^=\"#research\"]')",'Research page links and homepage section removed');
assert.equal(await evaluate("document.querySelector('.lab-full-name').textContent"), siteSettings.fullName);
assert.equal(await evaluate("document.querySelector('.footer-name').textContent"), siteSettings.fullName);
assert.equal(await evaluate("document.querySelector('.recruiting-call a').getAttribute('href')"), siteSettings.recruitmentUrl || '#contact');
const setFont=font=>evaluate(`(()=>{const s=document.querySelector('#font-preview');s.value=${JSON.stringify(font)};s.dispatchEvent(new Event('change'));})()`);
for(const [font,family] of [['times','Times New Roman'],['arial','Arial']]){
  await setFont(font);
  for(const page of ['home','member',memberRoute,'publications','news','contact']){
    await navigate(page);
    await check(`[...document.querySelectorAll('body *')].every(el=>getComputedStyle(el).fontFamily.includes(${JSON.stringify(family)})) && document.documentElement.scrollWidth<=innerWidth`,font+' throughout '+page);
  }
}
await navigate('home');
await check("Math.abs(document.querySelector('.hm-story-list').getBoundingClientRect().left-document.querySelector('.hm-stories-title').getBoundingClientRect().left)<2 && document.querySelector('.hm-story-list').getBoundingClientRect().top>=document.querySelector('.hm-stories-title').getBoundingClientRect().bottom",'recent papers below heading without empty left column');
await check("document.querySelector('.hm-copy').getBoundingClientRect().top<document.querySelector('.hm-gallery').getBoundingClientRect().top+20 && [...document.querySelectorAll('.stack-sheet')].every(el=>{const m=new DOMMatrix(getComputedStyle(el).transform);return Math.abs(Math.atan2(m.b,m.a)*180/Math.PI)<3;})",'top-aligned hero and subtle photo angles');
await evaluate('window.scrollTo(0,700)');await settle();
await check("Math.abs(document.querySelector('.site-header').getBoundingClientRect().top)<1",'navigation remains visible while scrolling');
await evaluate('window.scrollTo(0,0)');
for(let i=0;i<albumSize;i++)await evaluate("document.querySelector('.gallery-next').click()");
await check(`document.querySelector('.gallery-count').textContent==='1 / ${albumSize}'`,'next photo wraps');
await check("document.querySelector('.stack-sheet:not([aria-hidden])').getAnimations().some(a=>a.id==='photo-switch')",'photo switch animation starts');
await evaluate("document.querySelector('[data-gallery-step=\"-1\"]').click()");
await check(`document.querySelector('.gallery-count').textContent==='${albumSize} / ${albumSize}' && document.querySelectorAll('.stack-sheet[aria-hidden=true] button[tabindex]').length===${albumSize-1}`,'previous photo and inactive-frame keyboard behavior');
await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
await evaluate("document.querySelector('[data-gallery-step=\"-1\"]').click()");
await check("!document.querySelector('.photo-stack').getAnimations({subtree:true}).length",'reduced-motion setting disables album animations');
await evaluate("document.querySelector('[data-gallery-step=\"1\"]').click()");
await send('Emulation.setEmulatedMedia',{features:[]});
await evaluate("document.querySelector('.gallery-expand').focus();document.querySelector('.gallery-expand').click()");
await check("document.querySelector('dialog').open",'full photo dialog');
await check("document.querySelector('dialog img').src===document.querySelector('.stack-sheet:not([aria-hidden]) img').src",'dialog opens selected photo');
await evaluate("document.querySelector('.dialog-close').click()");await settle();
await check("document.activeElement.hasAttribute('data-photo')",'dialog focus restoration');
await evaluate("document.querySelector('[data-gallery-step=\"1\"]').click()");
for(const route of ['home','member','publications','news','contact']){
  await navigate(route);await check('document.documentElement.scrollWidth <= innerWidth',route+' desktop width');
  await check("!document.querySelector('input[type=search]')",route+' has no search box');
  await evaluate("Promise.all([...document.images].map(i=>{i.loading='eager';return i.decode().catch(()=>{});} ))");
  await evaluate("Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{})))");
  await check("[...document.images].filter(i=>!i.hidden).every(i=>i.complete&&i.naturalWidth>0)",route+' images');
}
await navigate('member');
await check(`document.querySelectorAll('.person').length===${people.length} && !document.querySelector('#people-chips')`,'member directory');
await check("!document.querySelector('.page-heading p:not(.eyebrow)') && !!document.querySelector('.person h3 a[href^=\"#member/\"]')",'member profile links');
assert.deepEqual(await evaluate("[...document.querySelectorAll('#current-members .person h3 a')].map(a=>a.textContent)"),people.filter(p=>p.memberships.some(m=>m.isCurrent)).map(displayName));
assert.deepEqual(await evaluate("[...document.querySelectorAll('#alumni-members time')].map(t=>t.dateTime)"),people.filter(p=>p.alumniSince).map(p=>p.alumniSince));
await navigate(memberRoute);
assert.equal(await evaluate("document.querySelector('h1').textContent"), displayName(member));
await check(`document.querySelectorAll('.member-papers .paper').length===${detail.publications.length}`,'linked publications');
await evaluate("document.querySelector('.member-papers .paper h3 a').click()");await settle();
await check("location.hash.startsWith('#publications/')",'member paper opens detail page');
await evaluate('history.back()');await ready();assert.equal(await evaluate("document.querySelector('h1').textContent"),displayName(member));
await navigate('publications');await check(`document.querySelector('#paper-count').textContent==='${papers.length} results' && document.querySelectorAll('.paper').length===12`,'publication count and pagination');
await check("!document.querySelector('.page-heading p:not(.eyebrow)')",'publications heading');
await evaluate("document.querySelector('[data-page=\"2\"]').click()");await check(`document.querySelector('#pagination').textContent.includes('Page 2 of ${Math.ceil(papers.length/12)}')`,'publication next page');
const {year,type}=papers[0];
await evaluate(`for (const [id,value] of ${JSON.stringify([['paper-year',String(year)],['paper-type',type]])}) { const field=document.getElementById(id); field.value=value; field.dispatchEvent(new Event('change')); }`);
const expected=papers.filter(p=>p.year===year && p.type===type).length;
await check(`document.querySelectorAll('.paper').length===${Math.min(expected,12)}`,'combined year and type filters');
await navigate('publications');
await check("!document.querySelector('[data-citation]')&&!document.querySelector('[data-abstract]')",'no fabricated citation or abstract actions');
const firstTitle=await evaluate("document.querySelector('.paper h3 a').textContent");
await evaluate("document.querySelector('.paper h3 a').click()");await settle();assert.equal(await evaluate("document.querySelector('h1').textContent"),firstTitle);
await navigate('contact');await evaluate("document.querySelector('.join-grid details')?.setAttribute('open','')");
await check("!document.querySelector('input[type=checkbox]') && [...document.querySelectorAll('.application-materials')].every(list=>list.children.length>0)",'application materials lists');
await check("getComputedStyle(document.querySelector('#navigation .join-link'),'::after').content==='none'",'Contact active state without underline');
assert.equal(await evaluate("document.querySelector('.join-grid aside h2').textContent"),siteSettings.contactName || siteSettings.name);
for(const width of [390,768]){
  await send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:1,mobile:true});
  for(const route of ['home','member',memberRoute,'publications','news','contact']){await navigate(route);await check('document.documentElement.scrollWidth <= innerWidth',route+' '+width+'px width');}
  await navigate('home');await evaluate('window.scrollTo(0,700)');await settle();await check("Math.abs(document.querySelector('.site-header').getBoundingClientRect().top)<1",width+'px sticky navigation');await evaluate('window.scrollTo(0,0)');
  await evaluate("document.querySelector('.photo-stack').scrollIntoView({block:'center',behavior:'instant'})");await settle();
  await check("[...document.querySelectorAll('.gallery-arrow')].every(button=>{const r=button.getBoundingClientRect(),stack=document.querySelector('.photo-stack').getBoundingClientRect();return r.width>=44 && r.height>=44 && Math.abs(r.top+r.height/2-stack.top-stack.height/2)<2 && document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)?.closest('.gallery-arrow')===button;})",width+'px side arrows are large and clickable');
  await evaluate("document.querySelector('.gallery-next').click()");await check(`document.querySelector('.gallery-count').textContent==='2 / ${albumSize}'`,width+'px next photo works');await evaluate("document.querySelector('.gallery-prev').click()");
  await evaluate("Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{})))");
  await navigate('home');await evaluate("document.querySelector('#menu-toggle').click()");await check("document.querySelector('#menu-toggle').getAttribute('aria-expanded')==='true'",width+'px mobile menu');await evaluate("document.querySelector('#navigation a[href=\"#member\"]').click()");await ready();await check("document.querySelector('#menu-toggle').getAttribute('aria-expanded')==='false' && document.querySelector('h1').textContent==='Members'",'menu closes on navigation');
}
await navigate('people');await check("document.querySelector('h1').textContent==='Members'",'legacy People route');
await navigate('join');await check("document.querySelector('h1').textContent==='Contact'",'legacy Join route');
for(const path of ['home','people','people/'+encodeURIComponent(member.id),'publications']){
  assert(requests.some(url=>new URL(url).pathname==='/api/'+path),'Live endpoint was requested: '+path);
}
await navigate('home');
assert.equal(errors.length,0,errors.join('\n'));
console.log('PASS no browser exceptions');socket.close();
