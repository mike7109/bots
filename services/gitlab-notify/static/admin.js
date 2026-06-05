const DAYS=["Пн","Вт","Ср","Чт","Пт","Сб","Вс"];
const TABS=[["dash","Дашборд"],["src","Группы"],["users","Получатели"],["sched","Рассылки"],["cal","Календарь"],["rules","Правила"],["tpl","Шаблоны"],["logs","Логи"],["conn","Настройки"]];
const PASS_INFO={
  due:{icon:'📅',title:'Дедлайн завтра',tpl:'due_soon',desc:'Напоминание в общую комнату об issue, у которых срок наступает завтра.'},
  overdue:{icon:'⏰',title:'Просрочки',tpl:'overdue',desc:'Личное напоминание исполнителю об его issue с прошедшим сроком.'},
  digest:{icon:'📋',title:'Личный дайджест «Задачи на сегодня»',tpl:'digest_personal',desc:'Каждому в личку его задачи. В якорные дни — полный обзор, в остальные — только изменения со вчера.'},
  team:{icon:'🗓',title:'Сводка команды',tpl:'digest_team',desc:'Обзор задач команды в общую комнату: просрочено / сегодня / ближайшие / в работе.'},
  triage:{icon:'🧹',title:'Триаж',tpl:'triage',desc:'Что требует внимания: без исполнителя или без срока. Обычно раз в неделю.'},
  stale:{icon:'🕸',title:'Зависшие задачи',tpl:'stale',desc:'Открытые issue без активности ≥ N дней (порог — ниже). Обычно раз в неделю.'},
  metrics:{icon:'📊',title:'Метрики потока',tpl:'metrics',desc:'Еженедельный снимок: закрыто / в работе (WIP) / возраст / cycle time p85.'},
};
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
  S=s; showApp();
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
  renderRules();
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
function renderRules(){
  $('rules').innerHTML=S.rules.map(r=>`<div class="card" style="padding:14px">
    <div class="row" style="margin:0"><div style="font-size:15px"><b>${esc(kindTitle(r.event))}</b> <span class="mut mono" style="font-size:11px">${esc(r.event)}</span>${r.enabled?'':' <span class="badge ignored">выключено</span>'}</div>
      <span class="spacer"></span>
      <label class="row" style="margin:0;gap:6px"><span class="mut" style="font-size:12px">вкл</span><span class="switch"><input type="checkbox" ${r.enabled?'checked':''} onchange="setRule('${r.event}',{enabled:this.checked})"><span class="slider"></span></span></label></div>
    <p class="hint" style="margin:6px 0 10px">${esc(kindDesc(r.event))}</p>
    <div class="row" style="margin:0"><span class="mut">Куда слать:</span>
      ${['room','dm'].map(d=>`<label class="tag" style="cursor:pointer;padding:3px 9px"><input type="checkbox" ${r.to.includes(d)?'checked':''} onchange="toggleDest('${r.event}','${d}',this.checked)"> ${DEST[d]}</label>`).join('')}
      <span class="spacer"></span><button class="sm" onclick="toggleExample('${r.event}','${r.template}',this)">👁 пример</button></div>
    <div class="bubble exmpl hide" id="ex_${r.event}" style="margin-top:10px"></div></div>`).join('');
}
async function toggleExample(ev,tpl,btn){
  const box=$('ex_'+ev);
  if(!box.classList.contains('hide')){box.classList.add('hide');return;}
  box.classList.remove('hide');box.innerHTML='<span class="mut">загрузка…</span>';
  const r=await api('/example?template='+encodeURIComponent(tpl));
  box.innerHTML=r.ok?r.html:'<span class="err">'+esc(r.error||'ошибка')+'</span>';
}
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
function renderPasses(){
  $('passCards').innerHTML=S.passes.map(p=>{
    const c=S.pass_schedules[p]||{};const hasA=S.has_anchor.includes(p);const info=PASS_INFO[p]||{icon:'•',title:p,desc:''};
    const sendBtns=hasA
      ? `<button class="primary sm" onclick="send('${p}','delta',this)">▶ Отправить изменения</button>
         <button class="primary sm" onclick="send('${p}','full',this)">▶ Отправить всё</button>`
      : `<button class="primary sm" onclick="send('${p}','',this)">▶ Запустить сейчас</button>`;
    return `<div class="card" data-p="${p}">
      <div class="row" style="margin:0"><div style="font-size:15px"><b>${info.icon} ${esc(info.title)}</b> <span class="mut mono" style="font-size:11px">${esc(p)}</span></div>
        <span class="spacer"></span>
        <label class="row" style="margin:0;gap:6px"><span class="mut" style="font-size:12px">вкл</span><span class="switch"><input type="checkbox" class="pEn" ${c.enabled?'checked':''}><span class="slider"></span></span></label>
        <button class="sm" onclick="passExample('${info.tpl}',this)">👁 Пример</button>
        <button class="sm" onclick="editTpl('${info.tpl}')">✎ шаблон</button>
        <button class="sm" onclick="previewPass('${p}',this)">🔍 Предпросмотр</button>
        <span class="sendsep" title="ниже — реальная отправка"></span>
        ${sendBtns}</div>
      <p class="hint" style="margin:6px 0 12px">${esc(info.desc)}</p>
      <div class="bubble pEx hide" style="margin-bottom:10px"></div>
      <div class="pPreview" style="margin-bottom:10px"></div>
      <div class="row" style="margin:0"><span class="mut">Дни:</span><div class="days pDays">${miniDays(c.days||[],'d')}</div>
        <span class="mut" style="margin-left:8px">Время:</span><input type="time" class="pTime" value="${esc(c.time||'09:00')}">
        ${hasA?`<span class="mut" style="margin-left:8px" title="полный обзор вместо «только изменения»">⚓ Якорь:</span><div class="days pAnchor">${miniDays(c.anchor_days||[],'a')}</div>`:''}
        ${p==='stale'?`<span class="mut" style="margin-left:8px">Без активности:</span><input type="number" class="pIdle" min="1" value="${c.days_idle||14}" style="width:60px"><span class="mut">дн.</span>`:''}
        <span class="spacer"></span><button class="primary sm" onclick="savePass('${p}',this)">Сохранить</button></div>
      <div class="pResult" style="margin-top:8px;font-size:13px"></div></div>`;
  }).join('');
}
async function savePass(p,btn){
  const card=btn.closest('.card');
  const body={enabled:card.querySelector('.pEn').checked,
    days:[...card.querySelectorAll('.pDays .day.sel')].map(e=>+e.dataset.d),
    time:card.querySelector('.pTime').value};
  const a=card.querySelector('.pAnchor');if(a)body.anchor_days=[...a.querySelectorAll('.day.sel')].map(e=>+e.dataset.a);
  const idle=card.querySelector('.pIdle');if(idle)body.days_idle=+idle.value;
  await api('/pass/'+p,'POST',body);toast('Расписание «'+p+'» сохранено');
}
async function saveHolidays(){
  const holidays=$('holidays').value.split('\n').map(s=>s.trim()).filter(Boolean);
  await api('/global','POST',{holidays});toast('Даты сохранены');
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
  const hasA=S.has_anchor.includes(name);const mode=hasA?'delta':'';
  btn.disabled=true;const o=btn.textContent;btn.textContent='⏳…';
  box.innerHTML='<span class="mut">загрузка…</span>';
  try{const r=await api('/trigger/'+name,'POST',modeBody(mode,true));
    if(r.ok)renderPer(box,r.per);
    else box.innerHTML='<span class="err">'+esc(r.error||'ошибка')+'</span>';
  }catch(e){box.innerHTML='<span class="err">'+esc(e)+'</span>';}
  btn.disabled=false;btn.textContent=o;
}
// ▶ Отправить — сначала dry (узнать, что уйдёт), потом — реальная отправка.
async function send(name,mode,btn){
  const card=btn.closest('.card');const res=card.querySelector('.pResult');
  btn.disabled=true;const o=btn.textContent;btn.textContent='⏳ запуск…';
  res.style.color='var(--mut)';res.textContent='Проверяю, что уйдёт…';
  try{
    const dryR=await api('/trigger/'+name,'POST',modeBody(mode,true));
    if(!dryR.ok){res.style.color='var(--bad)';res.textContent='✗ '+(dryR.error||'ошибка');toast('Ошибка',true);btn.disabled=false;btn.textContent=o;return;}
    // Ничего не уйдёт ни по одному источнику — объясняем и не шлём.
    const per=dryR.per||[];
    const sendable=per.filter(r=>r.reason==='ok'||r.reason==='error');
    if(per.length&&!sendable.length){
      res.style.color='';res.innerHTML=per.map(r=>`<div class="pinfo">${esc(r.source)}: ${esc(REASON_INFO[r.reason]||r.reason)}</div>`).join('');
      toast('Ничего не отправлено — нечего слать');btn.disabled=false;btn.textContent=o;return;
    }
    // DM-рассылка (личка каждому исполнителю) — подтверждение.
    if((name==='digest'||name==='overdue')&&dryR.total_recipients>0){
      const all=[];per.forEach(r=>{if(r.reason==='ok')(r.recipients||[]).forEach(x=>all.push(x));});
      const uniq=[...new Set(all)];const shown=uniq.slice(0,12).join(', ')+(uniq.length>12?', …':'');
      if(!confirm(`Разослать ${dryR.total_recipients} личных сообщений (получателям: ${shown}) сейчас? Сообщение уйдёт каждому исполнителю.`)){
        res.style.color='var(--mut)';res.textContent='Отменено.';btn.disabled=false;btn.textContent=o;return;
      }
    }
    // Реальная отправка.
    res.textContent='Отправляю…';
    const r=await api('/trigger/'+name,'POST',modeBody(mode,false));
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
