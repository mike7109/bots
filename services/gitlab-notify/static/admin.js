const DAYS=["Пн","Вт","Ср","Чт","Пт","Сб","Вс"];
const TABS=[["dash","Дашборд"],["src","Группы"],["users","Получатели"],["sched","Рассылки"],["cal","Календарь"],["tpl","Шаблоны"],["logs","Логи"],["conn","Настройки"]];
// Pass cards are driven by the backend registry (S.registry); the only thing the
// registry doesn't carry is an icon, so we keep a small map keyed by pass name
// with a category fallback (group→🗓, personal→📋) and a default •.
const PASS_ICON={
  due:'📅', overdue:'⏰',
  digest_full:'📋', digest_delta:'📋', team_full:'🗓', team_delta:'🗓',
  triage:'🧹', stale:'🕸', metrics:'📊',
};
const CAT_ICON={group:'🗓', personal:'📋'};
function passIcon(p,meta){return PASS_ICON[p]||CAT_ICON[(meta&&meta.category)]||'•';}
// {name: registry-entry} lookup, rebuilt from S.registry on every load().
let REG={};
let UF='all';
// Человеческие названия и описания вместо технических кодов событий.
const KIND={
  issue:{t:'Открытие / закрытие issue',d:'Issue открыли, закрыли или переоткрыли (вебхук) → в общую комнату.'},
  due_soon:{t:'Дедлайн завтра',d:'У issue срок наступает завтра → в общую комнату.'},
  overdue:{t:'Просрочка',d:'Срок задачи уже прошёл → в личку исполнителю.'},
  digest_personal:{t:'Личный дайджест',d:'Личные задачи человеку «что на тебе» → в личку.'},
  digest_team:{t:'Сводка команды',d:'Обзор задач всей команды → в общую комнату.'},
  triage:{t:'Триаж',d:'Задачи без исполнителя или без срока → в общую комнату.'},
  stale:{t:'Зависшие',d:'Открытые задачи без активности N дней → в общую комнату.'},
  metrics:{t:'Метрики потока',d:'Еженедельный снимок метрик → в общую комнату.'},
};
const DEST={room:'Комната',dm:'Личка'};
// «Рассылки» group cards by registry `category`. Stable order + friendly RU
// labels; unknown categories appended (label = the raw code) so new ones still
// show up. PASSCAT is the selected sub-tab ('all' = no filter, the default).
const CAT_ORDER=['group','personal','webhook'];
const CAT_LABEL={group:'Групповые (в комнату)',personal:'Личные (в ЛС)',webhook:'Вебхуки'};
let PASSCAT='all';
const PUSHL={default:'Как задумано',loud:'Всегда пуш',quiet:'Тихо (без пуша)'};
function kindTitle(k){return (KIND[k]||{}).t||k;}
function kindDesc(k){return (KIND[k]||{}).d||'';}
let S=null;
const $=id=>document.getElementById(id);
function esc(x){return String(x==null?'':x).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function toast(m,bad){const t=$('toast');t.textContent=m;t.style.borderColor=bad?'var(--bad)':'var(--line)';t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2200);}
async function api(path,method,body){
  const r=await fetch('/admin/api'+path,{method:method||'GET',headers:{'Content-Type':'application/json'},
    credentials:'same-origin',body:body?JSON.stringify(body):undefined});
  if(r.status===401){showLogin();throw new Error('401');}
  return r.json();
}
function showLogin(){$('app').classList.add('hide');$('login').classList.remove('hide');}
function showApp(){$('login').classList.add('hide');$('app').classList.remove('hide');}
async function doLogin(){
  const r=await fetch('/admin/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
    credentials:'same-origin',body:JSON.stringify({password:$('pw').value})});
  if(r.ok){showApp();load();}else{const j=await r.json();$('loginErr').textContent=j.error||'ошибка';}
}
async function logout(){await api('/logout','POST');showLogin();}

// tabs
$('tabs').innerHTML=TABS.map((t,i)=>`<div class="tab${i==0?' active':''}" data-t="${t[0]}" onclick="switchTab('${t[0]}')">${t[1]}</div>`).join('');
function switchTab(name){
  document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('active',e.dataset.t===name));
  document.querySelectorAll('.view').forEach(e=>e.classList.toggle('active',e.dataset.v===name));
  if(name==='logs')loadLogs();
}

async function load(){
  let s; try{s=await api('/state');}catch(e){return;}
  S=s; REG=Object.fromEntries((s.registry||[]).map(p=>[p.name,p])); showApp();
  $('statusPill').className='pill '+(s.enabled?'on':'off');
  $('statusPill').textContent=s.enabled?'РАБОТАЕТ':'ВЫКЛЮЧЕН';
  $('killSwitch').checked=s.enabled;
  $('schedOn').checked=s.scheduler_on!==false;
  renderBreakerBanner(s.breaker);
  renderStats(s.stats);
  renderErrBanner(s.stats);
  renderAlerts();
  renderGuard(s.guard);
  renderUserFilters();
  renderUsers();
  renderPasses();
  $('holAuto').checked=s.schedule.holidays_auto;
  $('holidays').value=(s.schedule.holidays||[]).join('\n');
  renderActiveHours(s.active_hours);
  renderQuietAfterFull();
  renderSources();
  renderConn(s.conn);
  checkConfig();
  $('tplSelect').innerHTML=s.templates.map(t=>`<option>${esc(t)}</option>`).join('');
  loadTpl();
}
function renderStats(st){
  const t=st.today||{},w=st.week||{};
  const s=(o,k)=>o[k]||0;
  $('stats').innerHTML=`
    <div class="stat ok"><div class="n">${s(t,'sent')}</div><div class="l">отправлено сегодня</div></div>
    <div class="stat"><div class="n">${s(w,'sent')}</div><div class="l">за 7 дней</div></div>
    <div class="stat"><div class="n">${s(w,'skipped')}</div><div class="l">пропущено (нед.)</div></div>
    <div class="stat ${s(w,'error')?'bad':''}"><div class="n">${s(w,'error')}</div><div class="l">ошибок (нед.)</div></div>`;
  const bk=st.by_kind||{};const keys=Object.keys(bk);
  $('byKind').innerHTML=keys.length?('<span class="mut" style="font-size:12px">По типам за неделю: </span>'+keys.map(k=>`<span class="tag">${esc(kindTitle(k))}: ${bk[k]}</span>`).join('')):'<span class="mut" style="font-size:12px">Отправок за неделю пока нет.</span>';
}
function renderErrBanner(st){const b=$('errBanner');if(!b)return;
  const et=(st&&st.today&&st.today.error)||0, ew=(st&&st.week&&st.week.error)||0, n=et||ew;
  if(!n){b.innerHTML='';return;}
  const when=et?'сегодня':'за неделю';
  b.innerHTML=`<div class="card" style="border-color:rgba(248,81,73,.4);background:rgba(248,81,73,.08)"><b style="color:var(--bad)">⚠️ ${n} ошибок доставки ${when}</b> <span class="mut">— что-то не отправилось, открой «Логи».</span> <button class="sm" onclick="switchTab('logs')">Открыть «Логи»</button></div>`;
}
// ⛔ Предохранитель: красный баннер на ВСЕХ вкладках, пока защита держит бота.
function renderBreakerBanner(b){const el=$('breakerBanner');if(!el)return;
  if(!b||!b.tripped){el.innerHTML='';return;}
  const since=b.since?(' · с '+esc(String(b.since).replace('T',' ').slice(0,16))):'';
  const reason=b.reason?(' Причина: '+esc(b.reason)+'.'):'';
  el.innerHTML=`<div class="breaker-banner">
    <div>⛔ <b>Сработала защита от спама</b> — бот ОСТАНОВЛЕН, отправки не идут.${reason}<span class="mut">${since}</span></div>
    <button class="primary" onclick="resetBreaker(this)">Сбросить предохранитель</button></div>`;
}
async function resetBreaker(btn){
  btn.disabled=true;const o=btn.textContent;btn.textContent='⏳…';
  try{const r=await api('/breaker/reset','POST');
    if(r&&r.ok){toast('Предохранитель сброшен');load();}
    else{toast('Не удалось сбросить',true);btn.disabled=false;btn.textContent=o;}
  }catch(e){toast('Ошибка',true);btn.disabled=false;btn.textContent=o;}
}
// 🛡 Карточка порогов защиты. Окна показываем в минутах (хранятся в секундах).
const GUARD_FIELDS=[
  {k:'max_global',win:'window_s',label:'Глобальный лимит отправок',unit:'за',min:1},
  {k:'max_per_target',win:'target_window_s',label:'Лимит на одного получателя',unit:'за',min:1},
  {k:'max_duplicate',label:'Лимит одинаковых сообщений подряд',min:1},
  {k:'auto_reset_s',label:'Авто-сброс (0 = только вручную)',unit:'мин',min:0,isMin:true},
];
function renderGuard(g){g=g||{};
  $('grdOn').checked=g.enabled!==false;
  const num=(id,v,min)=>`<input type="number" id="${id}" min="${min}" value="${v==null?'':v}" style="width:80px">`;
  $('guardFields').innerHTML=GUARD_FIELDS.map(f=>{
    let right='';
    if(f.win){const mins=Math.round((g[f.win]||0)/60);
      right=`<span class="mut">${f.unit}</span>${num('grd_'+f.win,mins,1)}<span class="mut">мин</span>`;}
    else if(f.isMin){const mins=Math.round((g[f.k]||0)/60);
      return `<div class="row"><span class="mut" style="width:280px">${f.label}</span>${num('grd_'+f.k,mins,f.min)}<span class="mut">мин</span></div>`;}
    return `<div class="row"><span class="mut" style="width:280px">${f.label}</span>${num('grd_'+f.k,g[f.k],f.min)}${right}</div>`;
  }).join('');
}
async function saveGuard(){
  const v=id=>{const e=$(id);return e?Math.max(0,parseInt(e.value,10)||0):undefined;};
  const body={enabled:$('grdOn').checked,
    max_global:v('grd_max_global'), window_s:v('grd_window_s')*60,
    max_per_target:v('grd_max_per_target'), target_window_s:v('grd_target_window_s')*60,
    max_duplicate:v('grd_max_duplicate'), auto_reset_s:v('grd_auto_reset_s')*60};
  await api('/guard','POST',body);toast('Сохранено');load();
}
function renderAlerts(){
  const a=(S&&S.alerts)||{engineers:[],enabled:true};
  $('alertsOn').checked=a.enabled!==false;
  const sel=new Set(a.engineers||[]);
  const rows=Object.entries(S.users||{});
  $('alertEngineers').innerHTML=rows.length?('<table><tbody>'+rows.map(([login,u])=>`
    <tr><td style="width:32px"><input type="checkbox" class="alEng" value="${esc(login)}" ${sel.has(login)?'checked':''}></td>
    <td><b>${esc(u.name||login)}</b> <span class="mut mono" style="font-size:11px">${esc(login)}</span></td></tr>`).join('')+'</tbody></table>')
    :'<span class="mut">Нет пользователей в конфиге.</span>';
}
async function saveAlerts(){
  const engineers=[...document.querySelectorAll('.alEng:checked')].map(e=>e.value);
  await api('/alerts','POST',{engineers,enabled:$('alertsOn').checked});
  toast('Алерты сохранены');load();
}
const UFILTERS=[['all','Все'],['accepted','Принял'],['pending','Ждёт'],['none','Нет DM'],['muted','Замьючен']];
function renderUserFilters(){
  $('userFilters').innerHTML=UFILTERS.map(([k,l])=>`<span class="day ${UF===k?'sel':''}" onclick="UF='${k}';renderUserFilters();renderUsers()">${l}</span>`).join('');
}
function matchFilter(u){
  if(UF==='muted')return u.muted;
  if(UF==='all')return true;
  return u.invite===UF;
}
function renderUsers(){
  const q=($('userSearch').value||'').toLowerCase();
  const rows=Object.entries(S.users).filter(([l,u])=>matchFilter(u)&&(!q||l.toLowerCase().includes(q)||(u.name||'').toLowerCase().includes(q)));
  $('userCount').textContent=rows.length+' из '+Object.keys(S.users).length;
  $('users').innerHTML=rows.map(([login,u])=>`
    <tr><td><b>${esc(u.name)}</b><div class="mut mono">${esc(login)}</div></td>
    <td class="mono mut">${esc(u.mxid||'—')}</td>
    <td><span class="pill ${u.invite}">${({accepted:'✅ принял',pending:'⏳ ждёт',none:'— нет DM'})[u.invite]}</span></td>
    <td><span class="switch"><input type="checkbox" ${u.muted?'':'checked'} onchange="setUser('${login}',{muted:!this.checked})"><span class="slider"></span></span></td>
    <td><select onchange="setUser('${login}',{push:this.value})">${S.push_modes.map(m=>`<option value="${m}" ${u.push===m?'selected':''}>${PUSHL[m]||m}</option>`).join('')}</select></td></tr>`).join('')
    ||'<tr><td colspan=5 class="mut">Никого не найдено.</td></tr>';
}
// Секрет conn: если задан — чип «🔒 задан …xxxx» + «Заменить»; иначе поле + «Сохранить».
function secretField(wrapId,inputId,field,has,masked,ph,w){
  $(wrapId).innerHTML=has
    ? `<span class="badge sent">🔒 задан ${esc(masked)}</span> <button class="sm" type="button" onclick="revealSecret('${wrapId}','${inputId}','${field}','${esc(ph)}',${w})">Заменить</button>`
    : `<input id="${inputId}" type="password" placeholder="${esc(ph)}" style="width:${w}px"> <button class="primary sm" type="button" onclick="saveSecret('${inputId}','${field}')">Сохранить</button>`;
}
function revealSecret(wrapId,inputId,field,ph,w){
  $(wrapId).innerHTML=`<input id="${inputId}" type="password" placeholder="новый ${esc(ph)}" style="width:${w}px"> <button class="primary sm" type="button" onclick="saveSecret('${inputId}','${field}')">Сохранить</button> <button class="sm" type="button" onclick="load()">Отмена</button>`;
  $(inputId).focus();
}
async function saveSecret(inputId,field){const v=$(inputId).value;
  if(!v){toast('Введите значение',true);return;}
  await api('/conn','POST',{[field]:v});toast('Сохранено');load();}
function renderConn(c){c=c||{};
  $('cHs').value=c.matrix_homeserver||'';
  $('cGl').value=c.gitlab_url||'';
  secretField('cTokWrap','cTok','matrix_token',c.has_matrix_token,c.matrix_token_masked,'syt_…',300);
  secretField('cWhWrap','cWh','webhook_secret',c.has_webhook_secret,c.webhook_secret_masked,'секрет вебхука',300);
}
async function saveConn(){
  const body={matrix_homeserver:$('cHs').value,gitlab_url:$('cGl').value};
  const tok=$('cTok'); if(tok&&tok.value) body.matrix_token=tok.value;   // только если введён новый
  const wh=$('cWh'); if(wh&&wh.value) body.webhook_secret=wh.value;
  await api('/conn','POST',body);
  toast('Подключения сохранены');load();
}
async function checkMatrix(btn){const el=$('cMx');el.style.color='var(--mut)';el.textContent='проверяю…';
  const tok=$('cTok');
  const r=await api('/conn/check-matrix','POST',{homeserver:$('cHs').value,token:tok?tok.value:''});
  if(r.ok){el.style.color='var(--ok)';el.textContent='✓ '+r.user_id;}else{el.style.color='var(--bad)';el.textContent='✗ '+(r.error||'не работает');}
}
async function runHealth(){const sum=$('healthSum'),list=$('healthList');
  sum.style.color='var(--mut)';sum.textContent='проверяю…';list.innerHTML='';
  const r=await api('/health');
  sum.style.color=r.ok?'var(--ok)':'var(--bad)';
  const bad=(r.checks||[]).filter(c=>!c.ok).length;
  sum.textContent=r.ok?'✓ всё настроено':`✗ проблем: ${bad}`;
  list.innerHTML='<table><tbody>'+(r.checks||[]).map(c=>`<tr><td style="width:24px">${c.ok?'✅':'❌'}</td><td><b>${esc(c.name)}</b></td><td class="mut">${esc(c.detail||'')}</td></tr>`).join('')+'</tbody></table>';
  return r;
}
async function checkConfig(){const b=$('cfgBanner');
  b.innerHTML='<div class="card" style="border-color:var(--line)"><span class="mut">проверяю конфигурацию…</span></div>';
  let r;try{r=await api('/health');}catch(e){b.innerHTML='';return;}
  const bad=(r.checks||[]).filter(c=>!c.ok).length;
  if(r.ok){b.innerHTML='<div class="card" style="border-color:rgba(63,185,80,.4);background:rgba(63,185,80,.08)"><b style="color:var(--ok)">✅ Бот настроен</b> <span class="mut">— все проверки пройдены</span></div>';}
  else{b.innerHTML=`<div class="card" style="border-color:rgba(248,81,73,.4);background:rgba(248,81,73,.08)"><b style="color:var(--bad)">⚠️ Бот настроен не полностью</b> <span class="mut">— проблем: ${bad}.</span> <button class="sm" onclick="switchTab('conn');runHealth()">Открыть «Настройки»</button></div>`;}
}
function srcCard(s){
  s=s||{};const isNew=!s.id;
  return `<div class="card" data-sid="${esc(s.id||'')}">
    <div class="row" style="margin:0">
      <b style="font-size:15px">${isNew?'➕ Новая группа':'🔗 '+esc(s.name||s.id)}</b>
      <input class="sName" placeholder="Название" value="${esc(s.name||'')}" style="width:170px">
      <span class="spacer"></span>
      <label class="row" style="margin:0;gap:6px"><span class="mut" style="font-size:12px">вкл</span>
        <span class="switch"><input type="checkbox" class="sEn" ${s.enabled!==false?'checked':''}><span class="slider"></span></span></label>
    </div>
    <div class="row" style="margin:10px 0 0"><span class="mut">GitLab группа ID:</span>
      <input class="sGid" value="${esc(s.group_id||'')}" placeholder="напр. 12" style="width:90px">
      <span class="mut">Токен:</span>
      <span class="tokWrap">${s.has_token
        ? `<span class="badge sent">🔒 задан ${esc(s.token_masked)}</span> <button class="sm" type="button" onclick="grpTokEdit(this)">Заменить</button>`
        : `<input class="sTok" type="password" placeholder="glpat-…" style="width:160px">`}</span>
      <span class="mut">Комната:</span>
      <input class="sRoom" value="${esc(s.room||'')}" placeholder="!room:server" style="width:220px"></div>
    <div class="row" style="margin:10px 0 0">
      <button class="sm" onclick="validateSrc(this)">🔌 Проверить доступ</button>
      <button class="primary sm" onclick="saveSrc(this)">Сохранить</button>
      ${isNew?'':`<button class="sm" onclick="deleteSrc('${esc(s.id)}',this)">Удалить</button>`}
      <span class="spacer"></span><span class="srcRes" style="font-size:13px"></span></div>
    ${s.full_path?`<div class="mut" style="font-size:12px;margin-top:8px">GitLab: <b>${esc(s.group_name||'')}</b> · <span class="mono">${esc(s.full_path)}</span> · вебхуки проектов этой группы → сюда</div>`:'<div class="mut" style="font-size:12px;margin-top:8px">Полный путь группы заполнится после «Проверить доступ»/«Сохранить» — без него вебхук-роутинг не сработает.</div>'}
  </div>`;
}
function renderSources(){
  const list=(S.sources||[]).map(srcCard).join('');
  $('srcCards').innerHTML=list+srcCard({});   // + пустая карточка для добавления
}
function grpTokEdit(btn){const w=btn.closest('.tokWrap');
  w.innerHTML='<input class="sTok" type="password" placeholder="новый glpat-…" style="width:150px"> <button class="primary sm" type="button" onclick="saveSrc(this)">Сохранить</button> <button class="sm" type="button" onclick="renderSources()">Отмена</button>';
  w.querySelector('input').focus();}
function _srcBody(card){
  const t=card.querySelector('.sTok');               // нет поля = чип «задан» -> токен не трогаем
  return {id:card.dataset.sid||'', name:card.querySelector('.sName').value,
    group_id:card.querySelector('.sGid').value, token:(t&&t.value)?t.value:'…keep',
    room:card.querySelector('.sRoom').value, enabled:card.querySelector('.sEn').checked};
}
async function validateSrc(btn){
  const card=btn.closest('.card');const res=card.querySelector('.srcRes');
  res.style.color='var(--mut)';res.textContent='проверяю…';
  const b=_srcBody(card);
  const r=await api('/sources/validate','POST',{id:b.id,group_id:b.group_id,token:b.token});
  if(r.ok){res.style.color='var(--ok)';res.textContent=`✓ ${r.group_name||''} · ${r.full_path||''} · issue: ${r.issues==null?'?':r.issues}`;}
  else{res.style.color='var(--bad)';res.textContent='✗ '+(r.error||'нет доступа');}
}
async function saveSrc(btn){
  const card=btn.closest('.card');const res=card.querySelector('.srcRes');
  const r=await api('/sources','POST',_srcBody(card));
  if(r.ok){toast('Группа сохранена');load();}else{res.style.color='var(--bad)';res.textContent='✗ '+(r.error||'ошибка');}
}
async function deleteSrc(id,btn){if(!confirm('Удалить группу «'+id+'»? Бот перестанет её слушать.'))return;
  await api('/sources/'+encodeURIComponent(id),'DELETE');toast('Группа удалена');load();}
$('killSwitch').addEventListener('change',async e=>{await api('/global','POST',{enabled:e.target.checked});toast(e.target.checked?'Бот включён':'Бот выключен');load();});
async function setUser(login,patch){await api('/user/'+login,'POST',patch);toast('Сохранено: '+login);}
async function inviteBlast(btn){
  btn.disabled=true;const o=btn.textContent;btn.textContent='⏳…';
  try{const r=await api('/invite-blast','POST');
    if(r.ok)toast(r.pinged.length?('Позвали в комнату: '+r.pinged.join(', ')):(r.note||'все уже приняли'));
    else toast('Ошибка: '+(r.error||''),true);
  }catch(e){toast('Ошибка',true);}
  btn.disabled=false;btn.textContent=o;
}
async function setRule(ev,patch){await api('/rule/'+ev,'POST',patch);toast('Правило: '+ev);load();}
function toggleDest(ev,d,on){const r=S.rules.find(x=>x.event===ev);let to=r.to.slice();if(on){if(!to.includes(d))to.push(d);}else{to=to.filter(x=>x!==d);}setRule(ev,{to});}
function miniDays(sel,prefix){return DAYS.map((d,i)=>`<span class="day ${sel.includes(i)?'sel':''}" style="padding:3px 7px;font-size:12px" data-${prefix}="${i}" onclick="this.classList.toggle('sel')">${d}</span>`).join('');}
// Category of a pass (registry-driven), with a safe default for the rare case a
// pass has no registry entry.
function passCat(p){return (REG[p]||{}).trigger==='webhook'?'webhook':((REG[p]||{}).category||'group');}
// The unified «Рассылки» list is ONE registry-driven list: the scheduled passes
// (S.passes) PLUS every webhook-triggered registry entry (e.g. `issue`). Both are
// just registry passes; the card adapts (webhook cards have no schedule/run).
function isWebhook(p){return (REG[p]||{}).trigger==='webhook';}
function allCards(){
  const webhook=(S.registry||[]).filter(p=>p.trigger==='webhook').map(p=>p.name);
  return S.passes.concat(webhook.filter(p=>!S.passes.includes(p)));
}
// Sub-tabs are built from the categories actually present across the unified list:
// «Все» first, then CAT_ORDER, then any extra categories appended in first-seen
// order. Counts span scheduled + webhook cards alike.
function renderPassSubtabs(){
  const box=$('passSubtabs');if(!box)return;
  const cards=allCards();
  const cnt={};cards.forEach(p=>{const c=passCat(p);cnt[c]=(cnt[c]||0)+1;});
  const present=Object.keys(cnt);
  const ordered=CAT_ORDER.filter(c=>present.includes(c))
    .concat(present.filter(c=>!CAT_ORDER.includes(c)));
  const total=cards.length;
  const pill=(cat,label,n)=>`<span class="subtab ${PASSCAT===cat?'sel':''}" onclick="PASSCAT='${cat}';renderPassSubtabs();renderPasses()">${esc(label)}<span class="cnt">${n}</span></span>`;
  box.innerHTML=pill('all','Все',total)
    +ordered.map(c=>pill(c,CAT_LABEL[c]||c,cnt[c])).join('');
}
// Destination editor + type-enable, driven by the RULE for this card's
// event_kind (matched in S.rules by `event`). Lets you set «Куда» (room/dm via
// toggleDest→setRule) and the notification-type on/off (enabled via setRule).
// digest_full & digest_delta share one event_kind → editing either reflects on
// both after reload (we show a hint for those).
function ruleBlock(ek,shared){
  const r=(S.rules||[]).find(x=>x.event===ek);
  if(!r)return '';
  const dests=['room','dm'].map(d=>`<label class="tag" style="cursor:pointer;padding:3px 9px"><input type="checkbox" ${r.to.includes(d)?'checked':''} onchange="toggleDest('${ek}','${d}',this.checked)"> ${DEST[d]}</label>`).join('');
  const hint=shared?`<p class="hint" style="margin:6px 0 0">«Полный» и «изменения» — один тип уведомления: «Тип включён» и «Куда» общие для обеих рассылок.</p>`:'';
  return `<div class="rulebox">
    <div class="row" style="margin:0">
      <label class="row" style="margin:0;gap:6px" title="Включить/выключить весь этот тип уведомления (общий выключатель типа)"><span class="mut" style="font-size:12px">Тип включён</span><span class="switch"><input type="checkbox" ${r.enabled?'checked':''} onchange="setRule('${ek}',{enabled:this.checked})"><span class="slider"></span></span></label>
      ${r.enabled?'':'<span class="badge ignored">тип выключен</span>'}
      <span class="mut" style="margin-left:10px">Куда:</span>${dests}</div>${hint}</div>`;
}
// Human one-liner summary of WHEN/HOW a card fires (read-only, always visible).
// Calendar: «Пн–Пт · 09:00»; interval: «каждые N ч · при ≥M изм.»; webhook: event.
function dayRange(days){
  if(!days||!days.length)return '—';
  const s=[...days].sort((a,b)=>a-b);
  // contiguous run → «Пн–Пт», else comma list.
  const contiguous=s.every((d,i)=>i===0||d===s[i-1]+1);
  if(contiguous&&s.length>2)return DAYS[s[0]]+'–'+DAYS[s[s.length-1]];
  return s.map(i=>DAYS[i]).join(', ');
}
function passSummary(p,c,wh){
  if(wh)return 'по событию issue (open/close/reopen)';
  if(c.kind==='interval'){
    const ev=c.every_hours==null?2:c.every_hours, th=c.change_threshold==null?5:c.change_threshold;
    return `каждые ${ev} ч · при ≥${th} изм.`;
  }
  return `${dayRange(c.days)} · ${esc(c.time||'09:00')}`;
}
function renderPasses(){
  renderPassSubtabs();
  const passes=allCards().filter(p=>PASSCAT==='all'||passCat(p)===PASSCAT);
  if(!passes.length){$('passCards').innerHTML='<p class="hint" style="margin:8px 0">Здесь пока пусто.</p>';return;}
  $('passCards').innerHTML=passes.map(p=>{
    const c=S.pass_schedules[p]||{};
    // Meta comes from the backend registry; `tpl` is the pass's event template
    // (the «✎ шаблон» / «👁 Пример» target) — digest_full & digest_delta share it.
    const meta=REG[p]||{};
    const title=meta.title||p, desc=meta.description||'', tpl=meta.event_kind||p;
    const ek=meta.event_kind||p;
    const icon=passIcon(p,meta);
    const wh=isWebhook(p);
    // Destination badge (self-describing card): dm → «Личка», else «Комната».
    const dm=(meta.to||[]).includes('dm');
    const destBadge=`<span class="tag" title="Куда уходит рассылка">${dm?'→ Личка':'→ Комната'}</span>`;
    // Trigger badge: webhook («🔔 вебхук») fires in real time on a GitLab event;
    // scheduled («⏱ расписание») fires by days/time or interval below.
    const trigBadge=wh
      ? `<span class="tag" title="Срабатывает в реальном времени по событию GitLab">🔔 вебхук</span>`
      : `<span class="tag" title="Идёт по расписанию (дни/время или интервал)">⏱ расписание</span>`;
    // Header enable toggle. Scheduled: the SCHEDULE on/off (.pEn — moved here from
    // the schedule row). Webhook: there's no schedule, so the header toggle is the
    // notification-type on/off (the rule.enabled, via setRule) — .pEn is omitted on
    // webhook cards (savePass is never called for them).
    const r=(S.rules||[]).find(x=>x.event===ek);
    const headToggle=wh
      ? (r?`<label class="switch" title="Включить/выключить весь этот тип уведомления"><input type="checkbox" ${r.enabled?'checked':''} onchange="setRule('${ek}',{enabled:this.checked})"><span class="slider"></span></label>`:'')
      : `<label class="switch" title="Слать эту рассылку по расписанию (планировщик)"><input type="checkbox" class="pEn" ${c.enabled?'checked':''} onchange="savePass('${p}',this)"><span class="slider"></span></label>`;
    // Webhook cards (e.g. issue) have NO schedule editor and NO «Запустить сейчас»
    // (there's nothing to schedule/trigger — they fire on the GitLab event); they
    // still expose «Пример»/«шаблон» + the type-enable + destination editor.
    // Scheduled cards: single-mode (Phase 2a) → one «Запустить сейчас», no mode.
    const runBtns=wh?'':`<button class="sm" onclick="previewPass('${p}',this)">🔍 Предпросмотр</button>
        <span class="sendsep" title="ниже — реальная отправка"></span>
        <button class="primary sm" onclick="send('${p}',this)">▶ Запустить сейчас</button>`;
    // Flag cards that SHARE an event_kind/rule with another card (digest_full ↔
    // digest_delta, team_full ↔ team_delta): the type-enable/«Куда» are common.
    const shared=allCards().filter(q=>(REG[q]||{}).event_kind===ek).length>1;
    // Subtitle: registry description (the «what»), then the technical pass name.
    const subtitle=`${desc?esc(desc)+' · ':''}<span class="mono">${esc(p)}</span>`;
    return `<div class="card passcard" data-p="${p}">
      <div class="passhead">
        <div class="passicon">${icon}</div>
        <div class="passtitle"><b>${esc(title)}</b><div class="passsub">${subtitle}</div></div>
        <div class="passmeta">${trigBadge} ${destBadge}</div>
        ${headToggle}
      </div>
      <div class="passsum"><span class="sumlabel">${wh?'Триггер':'Когда'}:</span> ${passSummary(p,c,wh)}</div>
      <details class="pBody"><summary>⚙ Настроить ${wh?'тип и адрес':'расписание и адрес'}</summary>
        <div class="pBodyInner">
          ${wh?'':passSchedEditor(p,c)}
          ${ruleBlock(ek,shared)}
        </div>
      </details>
      <div class="bubble pEx hide" style="margin:10px 0"></div>
      <div class="pPreview"></div>
      <div class="passactions">
        <button class="sm" onclick="passExample('${esc(tpl)}',this)">👁 Пример</button>
        <button class="sm" onclick="editTpl('${esc(tpl)}')">✎ шаблон</button>
        ${runBtns}
      </div>
      <div class="pResult" style="margin-top:8px;font-size:13px"></div></div>`;
  }).join('');
}
// Кастомный 24-часовой пикуер времени: два тёмных <select> (часы 00..23,
// минуты 00..59) — всегда HH:MM, не зависит от локали браузера (нативный
// <input type=time> может показать 12ч AM/PM). value: "HH:MM" (деф. "09:00").
function _timeOpts(n,sel){let o='';for(let i=0;i<n;i++){const v=String(i).padStart(2,'0');
  o+=`<option value="${v}"${v===sel?' selected':''}>${v}</option>`;}return o;}
function timePicker(value,cls){
  const m=/^(\d{1,2}):(\d{1,2})$/.exec(value||'')||[];
  const hh=String(parseInt(m[1],10)||0).padStart(2,'0');
  const mm=String(parseInt(m[2],10)||0).padStart(2,'0');
  return `<span class="timepick"><select class="${cls}-h">${_timeOpts(24,hh)}</select>`+
    `<span class="tpsep">:</span><select class="${cls}-m">${_timeOpts(60,mm)}</select></span>`;
}
function readTimePicker(container,cls){
  const h=container.querySelector('.'+cls+'-h'),m=container.querySelector('.'+cls+'-m');
  return (h?h.value:'09')+':'+(m?m.value:'00');
}
// Schedule editor differs by pass kind. interval (digest_delta/team_delta): the
// delta digests fire every N hours within the workday window, or sooner on a
// burst of ≥change_threshold changes — so no «Время», but «Каждые … часов» and
// «Слать раньше при ≥ … изменениях». daily/calendar: the classic days+time row.
function passSchedEditor(p,c){
  // The schedule on/off (.pEn) lives in the card header now; this editor holds the
  // days/time (or interval) fields + Save. The .pEn checkbox is read by savePass
  // from the card root, so it doesn't matter that it sits outside this block.
  if(c.kind==='interval'){
    return `<div class="row" style="margin:0"><span class="mut">Рабочие дни:</span><div class="days pDays">${miniDays(c.days||[],'d')}</div>
        <span class="mut" style="margin-left:8px">Каждые:</span><input type="number" class="pEvery" step="0.5" min="0.5" value="${c.every_hours==null?2:c.every_hours}" style="width:64px"><span class="mut">ч</span>
        <span class="mut" style="margin-left:8px">Слать раньше при ≥</span><input type="number" class="pThresh" min="0" value="${c.change_threshold==null?5:c.change_threshold}" style="width:60px"><span class="mut">изменениях</span>
        <span class="spacer"></span><button class="primary sm" onclick="savePass('${p}',this)">Сохранить</button></div>
      <p class="hint" style="margin:6px 0 0">Дельта уходит только когда есть изменения: каждые N часов в рабочие часы или раньше — при всплеске на ≥ N изменений.</p>`;
  }
  return `<div class="row" style="margin:0"><span class="mut">Дни:</span><div class="days pDays">${miniDays(c.days||[],'d')}</div>
        <span class="mut" style="margin-left:8px">Время:</span>${timePicker(c.time||'09:00','pTime')}
        ${p==='stale'?`<span class="mut" style="margin-left:8px">Без активности:</span><input type="number" class="pIdle" min="1" value="${c.days_idle||14}" style="width:60px"><span class="mut">дн.</span>`:''}
        <span class="spacer"></span><button class="primary sm" onclick="savePass('${p}',this)">Сохранить</button></div>`;
}
async function savePass(p,btn){
  const card=btn.closest('.card');
  const kind=(S.pass_schedules[p]||{}).kind;
  const days=[...card.querySelectorAll('.pDays .day.sel')].map(e=>+e.dataset.d);
  let body;
  if(kind==='interval'){
    body={enabled:card.querySelector('.pEn').checked,days,
      every_hours:parseFloat(card.querySelector('.pEvery').value)||2,
      change_threshold:Math.max(0,parseInt(card.querySelector('.pThresh').value,10)||0)};
  }else{
    body={enabled:card.querySelector('.pEn').checked,days,
      time:readTimePicker(card,'pTime')};
    const idle=card.querySelector('.pIdle');if(idle)body.days_idle=+idle.value;
  }
  await api('/pass/'+p,'POST',body);toast('Расписание «'+p+'» сохранено');
}
async function saveHolidays(){
  const holidays=$('holidays').value.split('\n').map(s=>s.trim()).filter(Boolean);
  await api('/global','POST',{holidays});toast('Даты сохранены');
}
// 🕗 Часы активности — окно времени суток, вне которого плановые рассылки молчат.
function renderActiveHours(a){a=a||{};
  $('ahOn').checked=a.enabled!==false;
  // Подменяем нативные <input type=time id=ahFrom/ahUntil> на кастомные 24ч-пикеры
  // (span с тем же id), чтобы saveActiveHours читал часы/минуты из <select>.
  $('ahFrom').outerHTML=`<span id="ahFrom">${timePicker(a.from||'08:00','ahFrom')}</span>`;
  $('ahUntil').outerHTML=`<span id="ahUntil">${timePicker(a.until||'20:00','ahUntil')}</span>`;
  $('ahWh').checked=!!a.webhooks_quiet;
}
async function saveActiveHours(){
  const body={enabled:$('ahOn').checked,from:readTimePicker($('ahFrom'),'ahFrom'),
    until:readTimePicker($('ahUntil'),'ahUntil'),webhooks_quiet:$('ahWh').checked};
  await api('/active-hours','POST',body);toast('Сохранено');load();
}
// 🔕 Тишина после полного дайджеста — глобальная пауза дельты после полной сводки.
function renderQuietAfterFull(){
  const v=(S&&S.schedule&&S.schedule.delta_quiet_after_full_h);
  $('quietAfterFull').value=v==null?3:v;
}
async function saveQuietAfterFull(){
  const v=Math.max(0,parseFloat($('quietAfterFull').value)||0);
  await api('/global','POST',{delta_quiet_after_full_h:v});toast('Сохранено');load();
}
async function passExample(tpl,btn){
  const box=btn.closest('.card').querySelector('.pEx');
  if(!box.classList.contains('hide')){box.classList.add('hide');return;}
  box.classList.remove('hide');box.innerHTML='<span class="mut">загрузка…</span>';
  const r=await api('/example?template='+encodeURIComponent(tpl));
  box.innerHTML=r.ok?r.html:'<span class="err">'+esc(r.error||'ошибка')+'</span>';
}
function editTpl(tpl){switchTab('tpl');$('tplSelect').value=tpl+'.matrix.html.j2';loadTpl();}
// Manual "Run now" — мягкие RU-пояснения причин (нейтральные, не ошибка).
const REASON_INFO={
  no_changes:'Изменений нет — отправлять нечего (это нормально)',
  no_baseline:'Пока нет базовой точки — отправьте «Отправить всё» один раз, чтобы её задать',
  empty:'Нет подходящих задач',
};
// Причины, при которых ничего не уйдёт (и слать не нужно).
function isNoSend(reason){return reason==='no_changes'||reason==='no_baseline'||reason==='empty';}
// mode → JSON-параметры запроса (пустой mode = single-mode pass, не передаём).
function modeBody(mode,dry){const b={dry:!!dry};if(mode)b.mode=mode;return b;}
// Рендер одной строки источника (предпросмотр / результат) с пузырём сообщения.
function perRow(r){
  if(r.reason==='error')
    return `<div class="bubble"><span class="err">✗ ${esc(r.error||'ошибка')}</span></div>`;
  if(r.reason==='ok'){
    const who=(r.recipients||[]).map(esc).join(', ')||'—';
    return `<div style="margin-bottom:6px"><span class="mut">Кому:</span> <b>${who}</b></div>`
      +(r.html?`<div class="bubble">${r.html}</div>`:'');
  }
  return `<div class="pinfo">${esc(REASON_INFO[r.reason]||r.reason)}</div>`;
}
function renderPer(box,per){
  box.innerHTML=(per||[]).map(r=>
    `<div style="margin-bottom:10px"><div class="mut mono" style="font-size:11px;margin-bottom:4px">${esc(r.source)}</div>${perRow(r)}</div>`
  ).join('')||'<div class="pinfo">Нет включённых групп.</div>';
}
// 🔍 Предпросмотр — что и кому реально уйдёт сейчас (реальные данные), но НЕ шлёт.
async function previewPass(name,btn){
  const card=btn.closest('.card');const box=card.querySelector('.pPreview');
  // Single-mode: the pass's mode is fixed server-side, so we never send a `mode`.
  btn.disabled=true;const o=btn.textContent;btn.textContent='⏳…';
  box.innerHTML='<span class="mut">загрузка…</span>';
  try{const r=await api('/trigger/'+name,'POST',modeBody('',true));
    if(r.ok)renderPer(box,r.per);
    else box.innerHTML='<span class="err">'+esc(r.error||'ошибка')+'</span>';
  }catch(e){box.innerHTML='<span class="err">'+esc(e)+'</span>';}
  btn.disabled=false;btn.textContent=o;
}
// ▶ Отправить — сначала dry (узнать, что уйдёт), потом — реальная отправка.
async function send(name,btn){
  const card=btn.closest('.card');const res=card.querySelector('.pResult');
  btn.disabled=true;const o=btn.textContent;btn.textContent='⏳ запуск…';
  res.style.color='var(--mut)';res.textContent='Проверяю, что уйдёт…';
  try{
    // Single-mode: the pass's mode is fixed server-side, so we never send a `mode`.
    const dryR=await api('/trigger/'+name,'POST',modeBody('',true));
    if(!dryR.ok){res.style.color='var(--bad)';res.textContent='✗ '+(dryR.error||'ошибка');toast('Ошибка',true);btn.disabled=false;btn.textContent=o;return;}
    // Ничего не уйдёт ни по одному источнику — объясняем и не шлём.
    const per=dryR.per||[];
    const sendable=per.filter(r=>r.reason==='ok'||r.reason==='error');
    if(per.length&&!sendable.length){
      res.style.color='';res.innerHTML=per.map(r=>`<div class="pinfo">${esc(r.source)}: ${esc(REASON_INFO[r.reason]||r.reason)}</div>`).join('');
      toast('Ничего не отправлено — нечего слать');btn.disabled=false;btn.textContent=o;return;
    }
    // DM-рассылка (личка каждому исполнителю) — подтверждение. Какие пассы
    // шлют в личку, знает реестр (to включает 'dm'): digest_full/digest_delta/overdue.
    if(REG[name]&&(REG[name].to||[]).includes('dm')&&dryR.total_recipients>0){
      const all=[];per.forEach(r=>{if(r.reason==='ok')(r.recipients||[]).forEach(x=>all.push(x));});
      const uniq=[...new Set(all)];const shown=uniq.slice(0,12).join(', ')+(uniq.length>12?', …':'');
      if(!confirm(`Разослать ${dryR.total_recipients} личных сообщений (получателям: ${shown}) сейчас? Сообщение уйдёт каждому исполнителю.`)){
        res.style.color='var(--mut)';res.textContent='Отменено.';btn.disabled=false;btn.textContent=o;return;
      }
    }
    // Реальная отправка.
    res.textContent='Отправляю…';
    const r=await api('/trigger/'+name,'POST',modeBody('',false));
    if(!r.ok){res.style.color='var(--bad)';res.textContent='✗ '+(r.error||'ошибка');toast('Ошибка триггера',true);btn.disabled=false;btn.textContent=o;return;}
    const errs=(r.per||[]).filter(x=>x.reason==='error');
    const lines=(r.per||[]).filter(x=>!isNoSend(x.reason)).map(x=>
      x.reason==='error'?`<div class="pinfo"><span class="err">${esc(x.source)}: ✗ ${esc(x.error||'ошибка')}</span></div>`
                        :`<div class="pinfo">${esc(x.source)}: уйдёт ${x.reason==='ok'?(x.recipients||[]).length:0}</div>`);
    res.style.color='';
    res.innerHTML=`<div style="color:var(--ok);font-weight:600;margin-bottom:4px">✓ Отправлено: ${r.total_sent}</div>`+lines.join('');
    toast(errs.length?`«${name}»: отправлено ${r.total_sent}, ошибок ${errs.length}`:`«${name}»: отправлено ${r.total_sent}`,!!errs.length);
  }catch(e){res.style.color='var(--bad)';res.textContent='✗ '+e;toast('Ошибка',true);}
  btn.disabled=false;btn.textContent=o;
}
async function loadTpl(){const name=$('tplSelect').value;if(!name)return;const r=await api('/template?name='+encodeURIComponent(name));
  $('tplBody').value=r.content;
  $('tplOv').innerHTML=r.overridden?'<span class="badge ignored">изменён (в БД)</span> ':'<span class="mut" style="font-size:12px">дефолт · </span>';
  previewTpl();}
async function resetTpl(){if(!confirm('Сбросить шаблон к дефолту из файла? Правка из БД удалится.'))return;
  await api('/template/reset','POST',{name:$('tplSelect').value});toast('Сброшено к дефолту');loadTpl();}
let pvTimer=null;
function schedulePreview(){clearTimeout(pvTimer);pvTimer=setTimeout(previewTpl,500);}
async function previewTpl(){const r=await api('/template/preview','POST',{content:$('tplBody').value});
  if(r.ok){$('tplPreview').innerHTML=r.html;$('tplErr').textContent='';$('tplHint').textContent='✓ валиден';$('tplHint').style.color='var(--ok)';}
  else{$('tplErr').textContent=r.error;$('tplHint').textContent='✗ ошибка';$('tplHint').style.color='var(--bad)';}
}
async function saveTpl(){const r=await api('/template','POST',{name:$('tplSelect').value,content:$('tplBody').value});
  if(r.ok){toast('Шаблон сохранён');loadTpl();}else toast('Ошибка: '+(r.error||''),true);}
async function loadLogs(){const f=$('logFilter').value;const r=await api('/logs?limit=150'+(f?'&status='+f:''));
  $('logs').innerHTML=(r.rows||[]).map(x=>`<tr class="${x.status==='error'?'logerr':''}">
    <td class="mono mut">${esc((x.ts||'').replace('T',' ').slice(5,16))}</td>
    <td><b>${esc(kindTitle(x.kind))}</b></td>
    <td><span class="badge ${x.status}">${esc(({sent:'отправлено',skipped:'пропущено',error:'ошибка',ignored:'не слалось'})[x.status]||x.status)}</span></td>
    <td>${(x.channel||'').split(',').filter(Boolean).map(d=>DEST[d]||d).join(', ')||'—'}</td>
    <td class="mut">${esc(x.detail||'')}</td></tr>`).join('')||'<tr><td colspan=5 class="mut">Пусто.</td></tr>';
}
load();
