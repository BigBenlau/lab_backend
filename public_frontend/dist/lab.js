import { readPage, pageData, readSettings, pdfPath } from './api.js';

const content = document.querySelector('#content');
const dialog = document.querySelector('#detail-dialog');
const fontPreview = document.querySelector('#font-preview');
try { fontPreview.value = localStorage.getItem('casino-font') === 'times' ? 'times' : 'arial'; } catch {}
document.documentElement.dataset.font = fontPreview.value;
fontPreview.addEventListener('change', () => {
  document.documentElement.dataset.font = fontPreview.value;
  try { localStorage.setItem('casino-font', fontPreview.value); } catch {}
});
let baseData, data, previousFocus, galleryIndex=0, routeVersion=0;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label = value => ({faculty:'Faculty',phd_student:'PhD students',masters_student:'Master’s students',research_assistant:'Research assistants',postdoc:'Postdocs',undergraduate:'Undergraduates',visiting_researcher:'Visitors',conference:'Conference',journal:'Journal',workshop:'Workshop',preprint:'Preprint',book_chapter:'Book chapter',thesis:'Thesis',other:'Other',lab_news:'Lab news',lab_life:'Lab life',publication:'Publication',event:'Event',award:'Award'}[value] || value);
function safeUrl(value, asset=false) {
  if (!value) return '';
  try {
    const decoded=decodeURIComponent(value);
    if (asset && /^(?:\.\/|\/)assets\//.test(value) && !/[\\?#%\s\x00-\x1f\x7f]/.test(decoded) && !decoded.split('/').includes('..')) return value;
    const url=new URL(value);
    return ['http:','https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch { return ''; }
}
const link = (url, title, cls='text-link') => safeUrl(url) ? `<a class="${cls}" href="${esc(safeUrl(url))}" target="_blank" rel="noopener">${esc(title)} ↗</a>` : '';
const asset = id => data.assets.find(a => a.id === id);
function image(id, cls='', priority=false) {
  const a=asset(id), url=safeUrl(a?.url,true);
  return url ? `<img class="${cls}" src="${esc(url)}" alt="${esc(a.alt)}" ${priority?'fetchpriority="high"':'loading="lazy"'}>` : '';
}
// 只顯示段落、換行及清單；原始 HTML 一律跳脫，避免內容執行腳本。
function prose(value) {
  return String(value || '').split(/\n\s*\n/).filter(Boolean).map(block => block.split('\n').every(line=>line.startsWith('- ')) ? `<ul>${block.split('\n').map(line=>`<li>${esc(line.slice(2))}</li>`).join('')}</ul>` : `<p>${esc(block).replaceAll('\n','<br>')}</p>`).join('');
}
const heading = (title,description='',kicker=data?.siteSettings?.name || 'CASINO Lab') => `<header class="page-heading"><p class="eyebrow">${esc(kicker)}</p><h1>${esc(title)}</h1>${description?`<p>${esc(description)}</p>`:''}</header>`;
// 固定品牌名稱來自後端設定；文字仍先跳脫。
const fullName = value => esc(value);
function paperPage(p) {
  return /^#publications\/[a-zA-Z0-9_-]+$/.test(p.pageUrl) ? p.pageUrl : safeUrl(p.pageUrl);
}
function paperLink(p,title=p.title) {
  const url=paperPage(p);
  return url?`<a href="${esc(url)}" ${url.startsWith('#')?'':'target="_blank" rel="noopener"'}>${esc(title)}</a>`:esc(title);
}
function alumniDate(value) {
  const monthOnly=value.length===7;
  const date=new Date(monthOnly?`${value}-01`:value);
  return new Intl.DateTimeFormat('en', {year:'numeric',month:'long',...(monthOnly?{}:{day:'numeric'}),timeZone:'UTC'}).format(date);
}
function photoStack() {
  const ids=data.siteSettings.heroPhotoAssetIds.filter(id=>asset(id));
  if(!ids.length)return '';
  galleryIndex=((galleryIndex%ids.length)+ids.length)%ids.length;
  return `<div class="photo-stack" aria-label="Lab photo album">${ids.map((id,i)=>{const depth=(i-galleryIndex+ids.length)%ids.length;return `<div class="stack-sheet" style="--depth:${depth}" ${depth?'aria-hidden="true"':''}><button class="stack-photo" data-photo="${esc(id)}" aria-label="View group photo ${i+1}" ${depth?'tabindex="-1"':''}>${image(id,'group-photo',depth===0)}</button></div>`;}).join('')}${ids.length>1?'<button class="gallery-arrow gallery-prev" data-gallery-step="-1" aria-label="Previous photo"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg></button><button class="gallery-arrow gallery-next" data-gallery-step="1" aria-label="Next photo"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 6 6 6-6 6"/></svg></button>':''}</div><div class="gallery-controls"><span class="gallery-count" role="status" aria-live="polite">${galleryIndex+1} / ${ids.length}</span><button class="gallery-expand" data-photo="${esc(ids[galleryIndex])}">View full photo ↗</button></div>`;
}
function photo(id=data.siteSettings.heroPhotoAssetIds[0]) {
  if (!asset(id)) return '';
  return `<figure><button class="photo-button" data-photo="${esc(id)}" aria-label="View full group photo">${image(id,'group-photo',true)}</button><figcaption class="photo-caption"><span>${esc(asset(id).caption || 'Our group')}</span><button data-photo="${esc(id)}">View full photo ↗</button></figcaption></figure>`;
}
function memberLink(person, name=person.name, cls='') {
  const url=`#member/${encodeURIComponent(person.id)}`;
  return url?`<a class="${cls}" href="${esc(url)}" ${url.startsWith('#')?'':'target="_blank" rel="noopener"'}>${esc(name)}</a>`:esc(name);
}
const authors = paper => paper.authors.map(a => {const person=data.people.find(p=>p.id===a.personId);return person?memberLink(person,a.name,'author-link'):a.personId?memberLink({id:a.personId,links:[]},a.name,'author-link'):esc(a.name);}).join(', ');
// 下載只接受後端契約允許的同源 PDF；來源頁保留原始 sourceUrl。
function paperActions(p) {
  const pdf=pdfPath(p.pdfPath);
  return [pdf?`<a href="${esc(pdf)}" download>Download PDF</a>`:'',link(p.sourceUrl,'Source')].join('');
}
const paperRow = p => `<article class="paper"><span class="paper-year">${p.year}</span><div><div class="paper-meta">${esc(label(p.type))} ${p.venue.shortName?'· '+esc(p.venue.shortName):''}</div><h3>${paperLink(p)}</h3><p>${authors(p)}</p><p>${esc(p.venue.name)}</p><div class="paper-actions">${paperActions(p)}</div></div></article>`;
function home() {
  const s=data.siteSettings;
  const about=prose(s.aboutMarkdown || s.introduction);
  const latest=data.recentPublications;
  content.innerHTML=`<section class="hm-hero"><div class="hm-copy"><p class="eyebrow">${esc(s.institution)} · Research group</p><h1 class="hero-wordmark">${image(s.wordmarkAssetId,'wordmark',true)}<span class="sr-only">${esc(s.name)}</span></h1><h2 class="lab-full-name">${fullName(s.fullName)}</h2><p class="hm-summary">${esc(s.introduction)}</p><div class="hm-actions"><a href="#publications">Our publications</a><a href="#member">Meet the group ↗</a></div></div><div id="home-gallery" class="hm-gallery">${photoStack()}</div></section>
    <section class="hm-about"><div><p class="eyebrow">ABOUT THE LAB</p><h2>Contact</h2><p class="recruiting-call">Are you looking for a PhD/RA position? ${s.recruitmentUrl?link(s.recruitmentUrl,'Our latest recruitment information'):'<a class="text-link" href="#contact">Our latest recruitment information ↗</a>'}</p></div><div class="about-text">${about}</div></section>
    <section class="hm-stories"><header class="hm-stories-title"><p class="eyebrow">FROM THE LAB</p><h2>Recent publications</h2></header><div class="hm-story-list">${latest.map(p=>`<article><small>${p.year} · ${esc(p.venue.shortName || label(p.type))}</small><h3>${paperLink(p)}</h3></article>`).join('')}</div><a class="text-link" href="#publications">View all ${data.publicationCount} publications ↗</a></section>`;
}

function people(id) {
  if(id){
    const p=data.people.find(p=>p.id===id);if(!p)return notFound();
    const related=data.publications;
    content.innerHTML=`<a class="breadcrumb" href="#member">← Members</a>${heading(p.name,p.title,p.state==='alumni'?'ALUMNI':'MEMBER')}<div class="profile-layout"><aside>${image(p.portraitAssetId,'profile-portrait')}</aside><section><div class="prose">${prose(p.bioMarkdown)}</div>${p.interests.length?`<h2>Research interests</h2><div class="tag-row">${p.interests.map(i=>`<span class="tag">${esc(i)}</span>`).join('')}</div>`:''}${emailLink(p.publicEmail)}<div class="profile-links">${p.links.map(l=>link(l.url,l.label)).join('')}</div>${p.alumniSince?`<p class="alumni-date"><time datetime="${esc(p.alumniSince)}">${esc(alumniDate(p.alumniSince))}</time></p>`:''}</section></div><section class="member-papers bottom-space"><div class="section-heading"><h2>Publications</h2><span>${related.length} papers</span></div>${related.length?related.map(paperRow).join(''):'<p>No publications listed yet.</p>'}</section>`;return;
  }
  const cards=state=>data.people.filter(p=>p.state===state)
    .map(p=>`<article class="person"><div class="avatar">${image(p.portraitAssetId)}</div>${state==='member'&&p.title?`<span class="news-type">${esc(p.title)}</span>`:''}<h3>${memberLink(p)}</h3>${state==='alumni'&&p.alumniSince?`<p class="alumni-date"><time datetime="${esc(p.alumniSince)}">${esc(alumniDate(p.alumniSince))}</time></p>`:''}</article>`).join('');
  content.innerHTML=`<section aria-label="Members">${heading('Members')}<div class="people-grid" id="current-members">${cards('member')}</div></section><section aria-labelledby="alumni-heading"><h2 id="alumni-heading">Alumni</h2><div class="people-grid" id="alumni-members">${cards('alumni')}</div></section>${data.people.some(p=>p.state==='unclassified')?`<section><h2>Other members</h2><div class="people-grid">${cards('unclassified')}</div></section>`:''}`;
}

function publications(id, params) {
  if(id){
    const p=data.publications.find(p=>p.id===id);if(!p)return notFound();
    content.innerHTML=`<a class="breadcrumb" href="#publications">← All publications</a>${heading(p.title,`${p.year} · ${p.venue.name}`,label(p.type))}<article class="prose"><p>${authors(p)}</p><div class="paper-actions">${paperActions(p)}</div></article>`;return;
  }
  const years=[...new Set(data.publications.map(p=>p.year))].sort((a,b)=>b-a);
  const types=[...new Set(data.publications.map(p=>p.type))];
  let personId=params.get('personId')||'',page=1;
  content.innerHTML=heading('Publications')+`<div class="toolbar"><label class="field">Year<select id="paper-year"><option value="">All years</option>${years.map(y=>`<option>${y}</option>`).join('')}</select></label><label class="field">Type<select id="paper-type"><option value="">All types</option>${types.map(t=>`<option value="${t}">${esc(label(t))}</option>`).join('')}</select></label><label class="field">Sort<select id="paper-sort"><option value="new">Newest first</option><option value="old">Oldest first</option></select></label></div><div class="results-line"><span id="paper-count" aria-live="polite"></span><button class="text-link" id="clear-filters">Clear filters</button></div><div id="paper-results"></div><div class="pagination" id="pagination"></div>${data.siteSettings.publicationArchiveUrl?`<p class="source-note">Before 2023: ${link(data.siteSettings.publicationArchiveUrl,'Earlier publication archive')}</p>`:''}`;
  for(const [param,field] of [['year','paper-year'],['type','paper-type'],['sort','paper-sort']])if(params.has(param))document.getElementById(field).value=params.get(param);
  const update=()=>{
    const year=document.querySelector('#paper-year').value,type=document.querySelector('#paper-type').value,sort=document.querySelector('#paper-sort').value;
    const list=data.publications.filter(p=>(!personId||p.authors.some(a=>a.personId===personId))&&(!year||String(p.year)===year)&&(!type||p.type===type));
    // oldest 與 newest 都依入庫順序，不另外按年份重排。
    if(sort==='old')list.reverse();
    const pages=Math.ceil(list.length/12);page=Math.min(page,pages||1);
    document.querySelector('#paper-count').textContent=`${list.length} result${list.length===1?'':'s'}${personId?' · '+(data.people.find(p=>p.id===personId)?.name||'Selected author'):''}`;
    document.querySelector('#paper-results').innerHTML=list.length?list.slice((page-1)*12,page*12).map(paperRow).join(''):'<div class="empty"><h3>No publications match these filters.</h3><p>Try a broader search or clear the filters.</p><button class="button secondary" data-reset-papers>Clear filters</button></div>';
    document.querySelector('#pagination').innerHTML=pages>1?`<button class="button secondary" data-page="${page-1}" ${page===1?'disabled':''}>← Previous</button><span>Page ${page} of ${pages}</span><button class="button secondary" data-page="${page+1}" ${page===pages?'disabled':''}>Next →</button>`:'';
    const query=new URLSearchParams();for(const [k,v] of Object.entries({year,type,personId,sort:sort==='old'?'old':''}))if(v)query.set(k,v);
    history.replaceState(null,'',`#publications${query.size?'?'+query:''}`);
  };
  const reset=()=>{personId='';page=1;['paper-year','paper-type'].forEach(id=>document.getElementById(id).value='');document.querySelector('#paper-sort').value='new';update();};
  ['paper-year','paper-type','paper-sort'].forEach(id=>document.getElementById(id).addEventListener('change',()=>{page=1;update();}));
  document.querySelector('#clear-filters').addEventListener('click',reset);
  document.querySelector('#paper-results').addEventListener('click',e=>{if(e.target.closest('[data-reset-papers]'))reset();});
  document.querySelector('#pagination').addEventListener('click',e=>{const b=e.target.closest('[data-page]');if(!b||b.disabled)return;page=Number(b.dataset.page);update();document.querySelector('#paper-count').scrollIntoView({block:'start'});});update();
}

// News 保留相簿展示；本系統沒有新聞管理或新聞 API。
function newsPage(id) {
  if(id)return notFound();
  content.innerHTML=heading('News & lab life','Moments from the group.')+`<div class="bottom-space">${data.siteSettings.heroPhotoAssetIds.map(id=>photo(id)).join('')}</div>`;
}
function emailLink(value) {
  const valid=value&&/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(value);
  return valid?`<a class="contact-email" href="mailto:${esc(value)}">${esc(value)}</a>`:'';
}
function join() {
  const s=data.siteSettings,opportunities=data.opportunities.filter(o=>o.availability==='open'&&(!o.deadlineAt||Date.parse(o.deadlineAt)>=Date.now()));
  content.innerHTML=heading('Contact','Research enquiries, collaboration, and PhD opportunities.')+`<div class="join-grid"><section><h2>Application information</h2>${opportunities.map((o,i)=>`<details ${i===0?'open':''}><summary>${esc(o.title)}</summary>${prose(o.bodyMarkdown)}${o.requirements.length?`<ul class="requirements">${o.requirements.map(r=>`<li>${esc(r)}</li>`).join('')}</ul>`:''}${o.applicationChecklist.length?`<p>Application materials</p><ul class="application-materials">${o.applicationChecklist.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>`:''}${link(o.applicationUrl,'Apply')}${link(o.guidanceUrl,'Original guidance')}</details>`).join('')}${s.recruitmentMarkdown?`<details><summary>Timeline, program &amp; funding</summary>${prose(s.recruitmentMarkdown)}</details>`:''}</section><aside><p class="eyebrow">GET IN TOUCH</p><h2>${esc(s.contactName || s.name)}</h2><p>${esc(s.institution)}</p>${emailLink(s.publicEmail)}<div class="join-actions"><button class="button secondary" id="copy-email">Copy email</button></div><p id="email-status" class="dialog-status" role="status"></p><div class="profile-links">${s.links.map(l=>link(l.url,l.label)).join('')}</div></aside></div>`;
  document.querySelector('#copy-email').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(s.publicEmail);document.querySelector('#email-status').textContent='Email address copied.';}catch{document.querySelector('#email-status').textContent='Select the email address above to copy it.';}});
}

function openDialog(html) {
  previousFocus=document.activeElement;document.querySelector('#dialog-body').innerHTML=`<div class="dialog-content">${html}</div>`;dialog.showModal();document.body.style.overflow='hidden';dialog.querySelector('.dialog-close').focus();
}
dialog.querySelector('.dialog-close').addEventListener('click',()=>dialog.close());
dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
dialog.addEventListener('close',()=>{document.body.style.overflow='';if(previousFocus?.isConnected)previousFocus.focus();});
content.addEventListener('click',e=>{
  const galleryControl=e.target.closest('[data-gallery-step]');
  if(galleryControl){
    const gallery=document.querySelector('#home-gallery'),sheets=[...gallery.querySelectorAll('.stack-sheet')];
    const step=Number(galleryControl.dataset.galleryStep);
    galleryIndex=(galleryIndex+step+sheets.length)%sheets.length;
    sheets.forEach((sheet,i)=>{
      sheet.getAnimations().filter(a=>a.id==='photo-switch').forEach(a=>a.cancel());
      const depth=(i-galleryIndex+sheets.length)%sheets.length;
      sheet.style.setProperty('--depth',depth);
      if(depth)sheet.setAttribute('aria-hidden','true');else sheet.removeAttribute('aria-hidden');
      sheet.querySelector('.stack-photo').tabIndex=depth?-1:0;
    });
    const front=sheets[galleryIndex];
    if(!matchMedia('(prefers-reduced-motion: reduce)').matches){
      const animation=front.animate([{opacity:.2,translate:`${step*20}px 0`},{opacity:1,translate:'0 0'}],{duration:360,easing:'ease-out'});
      animation.id='photo-switch';
    }
    gallery.querySelector('.gallery-count').textContent=`${galleryIndex+1} / ${sheets.length}`;
    gallery.querySelector('.gallery-expand').dataset.photo=front.querySelector('.stack-photo').dataset.photo;
    galleryControl.focus({preventScroll:true});return;
  }
  const photoButton=e.target.closest('[data-photo]');if(photoButton){openDialog(`<h2 id="dialog-title">${esc(asset(photoButton.dataset.photo)?.caption||'Our group')}</h2>${image(photoButton.dataset.photo)}`);return;}

});
function notFound(){content.innerHTML=heading('Page not found','The requested page is not available.')+'<div class="bottom-space"><a class="button secondary" href="#home">Return home</a></div>';}
async function route(){
  if(!baseData)return;
  const version=++routeVersion;
  if(dialog.open)dialog.close();
  const [path,query='']=location.hash.slice(1).split('?');
  const [rawPage='home',rawId]=(path||'home').split('/');
  const page=({people:'member',join:'contact'})[rawPage]||rawPage;
  let id;try{id=rawId?decodeURIComponent(rawId):undefined;}catch{return notFound();}
  const params=new URLSearchParams(query);content.className=page==='home'?'home-main':'wrap';
  document.querySelectorAll('#navigation a').forEach(a=>{if(a.hash===`#${page}`)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  document.querySelector('#navigation').classList.remove('open');document.querySelector('#menu-toggle').setAttribute('aria-expanded','false');
  try {
    const pending=readPage(page,id);
    if(pending)content.innerHTML='<p class="loading" role="status">Loading the lab…</p>';
    const payload=await pending;
    if(version!==routeVersion)return;
    data=pageData(baseData,page,id,payload);
    if(page==='home')home();else if(page==='member')people(id);else if(page==='contact')join();else if(page==='publications')publications(id,params);else if(page==='news')newsPage(id);else notFound();
  }catch(error){if(version===routeVersion)showError(error,route);}
  document.title=`${content.querySelector('h1')?.textContent||data.siteSettings.name} · ${data.siteSettings.name}`;window.scrollTo(0,0);
}
document.querySelector('#menu-toggle').addEventListener('click',()=>{const open=document.querySelector('#navigation').classList.toggle('open');document.querySelector('#menu-toggle').setAttribute('aria-expanded',String(open));});
document.querySelector('.skip').addEventListener('click',event=>{event.preventDefault();content.focus();content.scrollIntoView();});
window.addEventListener('hashchange',()=>{route();content.focus({preventScroll:true});});
function showError(error,retry){
  content.innerHTML=heading('The content could not be loaded','Please try again.')+'<button class="button" id="retry">Retry</button>';
  document.querySelector('#retry').onclick=retry;
  console.error('Lab data loading failed:',error);
}
async function start(){
  try{
    // 此檔只提供固定設定；頁面內容每次由四個 API 重新取得。
    const loaded=await readSettings();
    if(!loaded.siteSettings || !Array.isArray(loaded.assets) || !Array.isArray(loaded.opportunities))throw new Error('Invalid site settings');
    data=baseData={...loaded,people:[],publications:[]};
    document.querySelector('.footer-name').textContent=data.siteSettings.fullName;
    document.querySelector('.footer-institution').textContent=data.siteSettings.institution;
    const logo=safeUrl(asset(data.siteSettings.logoAssetId)?.url,true);
    if(logo){document.querySelector('.brand img').src=logo;document.querySelector('.brand img').hidden=false;document.querySelector('link[rel=icon]').href=logo;}
    const wordmark=safeUrl(asset(data.siteSettings.wordmarkAssetId)?.url,true);
    if(wordmark){document.querySelector('.footer-wordmark').src=wordmark;document.querySelector('.footer-wordmark').hidden=false;}
    await route();
  }catch(error){showError(error,start);}
}
start();
