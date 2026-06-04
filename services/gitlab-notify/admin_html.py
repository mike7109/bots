# Single-page admin UI (served at /admin). One string, no static-file plumbing.
# Talks to /admin/api/* with the session cookie. Tabbed layout.
HTML = r"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>gitlab-notify · админка</title>
<style>
  :root{--bg:#0f1318;--panel:#161b22;--panel2:#1b212b;--line:#262d38;--fg:#e6edf3;
    --mut:#8b95a5;--accent:#fc6d26;--accent2:#7dc4ff;--ok:#3fb950;--warn:#d29922;--bad:#f85149;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 'Inter','Segoe UI',system-ui,sans-serif}
  a{color:var(--accent2);text-decoration:none}
  .wrap{max-width:1080px;margin:0 auto;padding:18px}
  header{display:flex;align-items:center;gap:14px;margin-bottom:14px;flex-wrap:wrap}
  header h1{font-size:18px;margin:0;font-weight:700;display:flex;align-items:center;gap:10px}
  .logo{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,#fc6d26,#e24329);display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:13px}
  .spacer{flex:1}
  .pill{padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;border:1px solid var(--line)}
  .pill.on{background:rgba(63,185,80,.15);color:var(--ok);border-color:rgba(63,185,80,.4)}
  .pill.off{background:rgba(248,81,73,.15);color:var(--bad);border-color:rgba(248,81,73,.4)}
  .pill.accepted{background:rgba(63,185,80,.12);color:var(--ok)}
  .pill.pending{background:rgba(210,153,34,.12);color:var(--warn)}
  .pill.none{background:rgba(139,149,165,.12);color:var(--mut)}
  .tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:16px;flex-wrap:wrap}
  .tab{padding:9px 14px;cursor:pointer;color:var(--mut);border-bottom:2px solid transparent;font-weight:600;font-size:13px}
  .tab:hover{color:var(--fg)}
  .tab.active{color:var(--fg);border-bottom-color:var(--accent)}
  .view{display:none} .view.active{display:block}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
  .card h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);margin:0 0 6px}
  .hint{color:var(--mut);font-size:12px;margin:0 0 12px}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:middle}
  th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase}
  tr:last-child td{border-bottom:none}
  .mut{color:var(--mut)} .mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
  button{font:inherit;cursor:pointer;border-radius:8px;border:1px solid var(--line);background:var(--panel2);color:var(--fg);padding:7px 12px}
  button:hover{border-color:var(--accent2)}
  button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
  button.sm{padding:4px 9px;font-size:12px}
  select,input[type=text],input[type=search],textarea{font:inherit;background:var(--panel2);color:var(--fg);border:1px solid var(--line);border-radius:7px;padding:6px 8px}
  textarea{width:100%;min-height:120px;font-family:ui-monospace,Menlo,monospace;font-size:12px;resize:vertical}
  .switch{position:relative;display:inline-block;width:38px;height:22px}
  .switch input{opacity:0;width:0;height:0}
  .slider{position:absolute;inset:0;background:#39404c;border-radius:22px;transition:.15s}
  .slider:before{content:"";position:absolute;height:16px;width:16px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.15s}
  .switch input:checked + .slider{background:var(--ok)}
  .switch input:checked + .slider:before{transform:translateX(16px)}
  .days{display:flex;gap:6px;flex-wrap:wrap}
  .day{padding:6px 10px;border:1px solid var(--line);border-radius:7px;cursor:pointer;user-select:none;font-size:13px}
  .day.sel{background:var(--accent2);color:#0b0f14;border-color:var(--accent2);font-weight:600}
  .row{display:flex;align-items:center;gap:10px;margin:10px 0;flex-wrap:wrap}
  .triggers{display:flex;gap:8px;flex-wrap:wrap}
  .stat{display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 16px;margin:0 8px 8px 0}
  .stat .n{font-size:22px;font-weight:700}
  .stat .l{font-size:11px;color:var(--mut);text-transform:uppercase}
  .stat.bad .n{color:var(--bad)} .stat.ok .n{color:var(--ok)}
  .tag{font-size:11px;padding:1px 7px;border-radius:6px;background:var(--panel2);border:1px solid var(--line);color:var(--mut);margin-right:4px}
  .toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:var(--panel2);border:1px solid var(--line);padding:10px 16px;border-radius:9px;opacity:0;transition:.2s;pointer-events:none;z-index:50}
  .toast.show{opacity:1}
  .login{max-width:340px;margin:14vh auto;text-align:center}
  .login input{width:100%;margin:12px 0;padding:10px}
  .hide{display:none}
  .split{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  @media(max-width:820px){.split{grid-template-columns:1fr}}
  .bubble{background:#1a1f25;border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:14px;line-height:1.5}
  .bubble ul{margin:3px 0 8px;padding-left:20px}
  .err{color:var(--bad);font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap}
  tr.logerr td{background:rgba(248,81,73,.08)}
  .badge{font-size:11px;font-weight:600;padding:1px 8px;border-radius:6px}
  .badge.sent{background:rgba(63,185,80,.15);color:var(--ok)}
  .badge.skipped{background:rgba(139,149,165,.15);color:var(--mut)}
  .badge.error{background:rgba(248,81,73,.18);color:var(--bad)}
  .badge.ignored{background:rgba(210,153,34,.15);color:var(--warn)}
</style></head>
<body>
<div id="app" class="hide"><div class="wrap">
  <header>
    <h1><span class="logo">GB</span> gitlab-notify</h1>
    <span id="statusPill" class="pill off">…</span>
    <div class="spacer"></div>
    <label class="row" style="margin:0;gap:8px"><span class="mut">Бот включён</span>
      <span class="switch"><input type="checkbox" id="killSwitch"><span class="slider"></span></span></label>
    <button class="sm" onclick="logout()">Выйти</button>
  </header>

  <div class="tabs" id="tabs"></div>

  <!-- Дашборд -->
  <div class="view" data-v="dash">
    <div class="card"><h2>Статистика отправок</h2>
      <p class="hint">Сколько и чего бот отправил. Ошибки подсвечены — если их больше нуля, загляни во вкладку «Логи».</p>
      <div id="stats"></div>
      <div id="byKind" style="margin-top:8px"></div>
    </div>
  </div>

  <!-- Получатели -->
  <div class="view" data-v="users">
    <div class="card"><h2>Получатели</h2>
      <p class="hint">Кто получает личные уведомления. «Инвайт» — принял ли человек DM-бота (пока «ждёт» — личка ему не дойдёт, копится). Выключатель — мьют всех его уведомлений. «Пуш»: default — как задумано, loud — всегда с пушем, quiet — тихо (без пуша).</p>
      <input type="search" id="userSearch" placeholder="Поиск по имени/логину…" oninput="renderUsers()" style="margin-bottom:10px;width:280px">
      <table><thead><tr><th>Человек</th><th>MXID</th><th>Инвайт</th><th>Уведомления</th><th>Пуш</th></tr></thead>
      <tbody id="users"></tbody></table>
    </div>
  </div>

  <!-- Расписание -->
  <div class="view" data-v="sched">
    <div class="card"><h2>Расписание дайджестов</h2>
      <p class="hint">Якорные дни — в них приходит полный обзор, даже если ничего не менялось; в остальные дни — только изменения. Гигиена (триаж/stale) — раз в неделю. Выходные и праздники — тишина. <b>Детальное расписание по каждой рассылке (дни+время) — в следующем дропе.</b></p>
      <div class="mut" style="margin-bottom:6px">Якорные дни (полный обзор)</div>
      <div class="days" id="anchorDays"></div>
      <div class="row"><span class="mut">День гигиены (триаж/stale):</span><select id="weeklyDay"></select></div>
      <div class="row"><span class="mut">Тишина по выходным</span>
        <span class="switch"><input type="checkbox" id="skipWeekends"><span class="slider"></span></span></div>
      <div class="mut" style="margin:8px 0 4px">Праздники (ISO-даты, по одной в строке)</div>
      <textarea id="holidays" placeholder="2026-01-01&#10;2026-05-09"></textarea>
      <div class="row"><button class="primary" onclick="saveSchedule()">Сохранить</button></div>
    </div>
  </div>

  <!-- Рассылки -->
  <div class="view" data-v="send">
    <div class="card"><h2>Запустить рассылку сейчас</h2>
      <p class="hint">Ручной триггер: отправит сразу, минуя расписание (дайджесты — полным обзором). Удобно проверить «как сейчас выглядит» или разослать вне графика.</p>
      <div class="triggers" id="triggers"></div>
    </div>
  </div>

  <!-- Правила -->
  <div class="view" data-v="rules">
    <div class="card"><h2>Правила маршрутизации</h2>
      <p class="hint">Куда уходит каждый тип уведомления. Можно выключить правило или сменить адрес (комната / личка). Событие и шаблон привязаны к коду.</p>
      <table><thead><tr><th>Вкл</th><th>Событие</th><th>Шаблон</th><th>Куда</th></tr></thead>
      <tbody id="rules"></tbody></table>
    </div>
  </div>

  <!-- Шаблоны -->
  <div class="view" data-v="tpl">
    <div class="card"><h2>Шаблоны сообщений</h2>
      <p class="hint">Текст уведомлений (Jinja2 + Matrix HTML). Слева — исходник, справа — живой предпросмотр на примерных данных. Ошибки шаблона подсветятся.</p>
      <div class="row"><select id="tplSelect" onchange="loadTpl()"></select>
        <button class="sm" onclick="previewTpl()">Предпросмотр</button>
        <button class="primary sm" onclick="saveTpl()">Сохранить</button>
        <span id="tplHint" class="mut"></span></div>
      <div class="split">
        <textarea id="tplBody" style="min-height:300px" oninput="schedulePreview()"></textarea>
        <div><div class="mut" style="font-size:11px;margin-bottom:4px">Предпросмотр (примерные данные)</div>
          <div id="tplPreview" class="bubble"></div>
          <div id="tplErr" class="err"></div></div>
      </div>
    </div>
  </div>

  <!-- Логи -->
  <div class="view" data-v="logs">
    <div class="card"><h2>Журнал отправок</h2>
      <p class="hint">Последние события бота. Красным — ошибки доставки.</p>
      <div class="row"><button class="sm" onclick="loadLogs()">Обновить</button>
        <select id="logFilter" onchange="loadLogs()">
          <option value="">все</option><option value="sent">отправлено</option>
          <option value="error">ошибки</option><option value="skipped">пропущено</option>
          <option value="ignored">проигнорировано</option></select></div>
      <table><thead><tr><th>Время</th><th>Тип</th><th>Статус</th><th>Куда</th><th>Детали</th></tr></thead>
      <tbody id="logs"></tbody></table>
    </div>
  </div>
</div></div>

<div id="login" class="hide"><div class="login card">
  <h1 style="justify-content:center"><span class="logo">GB</span></h1>
  <div class="mut">Админка gitlab-notify</div>
  <input type="password" id="pw" placeholder="Пароль админа" onkeydown="if(event.key=='Enter')doLogin()">
  <button class="primary" style="width:100%" onclick="doLogin()">Войти</button>
  <div id="loginErr" class="mut" style="color:var(--bad);margin-top:8px"></div>
</div></div>

<div id="toast" class="toast"></div>
<script>
const DAYS=["Пн","Вт","Ср","Чт","Пт","Сб","Вс"];
const TABS=[["dash","Дашборд"],["users","Получатели"],["sched","Расписание"],["send","Рассылки"],["rules","Правила"],["tpl","Шаблоны"],["logs","Логи"]];
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
  renderStats(s.stats);
  renderUsers();
  $('anchorDays').innerHTML=DAYS.map((d,i)=>`<span class="day ${s.schedule.anchor_days.includes(i)?'sel':''}" data-d="${i}" onclick="this.classList.toggle('sel')">${d}</span>`).join('');
  $('weeklyDay').innerHTML=DAYS.map((d,i)=>`<option value="${i}" ${s.schedule.weekly_day===i?'selected':''}>${d}</option>`).join('');
  $('skipWeekends').checked=s.schedule.skip_weekends;
  $('holidays').value=(s.schedule.holidays||[]).join('\n');
  $('triggers').innerHTML=s.passes.map(p=>`<button class="sm" onclick="trigger('${p}',this)">▶ ${p}</button>`).join('');
  renderRules();
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
  $('byKind').innerHTML=keys.length?('<span class="mut" style="font-size:12px">По типам за неделю: </span>'+keys.map(k=>`<span class="tag">${esc(k)}: ${bk[k]}</span>`).join('')):'<span class="mut" style="font-size:12px">Отправок за неделю пока нет.</span>';
}
function renderUsers(){
  const q=($('userSearch').value||'').toLowerCase();
  const rows=Object.entries(S.users).filter(([l,u])=>!q||l.toLowerCase().includes(q)||(u.name||'').toLowerCase().includes(q));
  $('users').innerHTML=rows.map(([login,u])=>`
    <tr><td><b>${esc(u.name)}</b><div class="mut mono">${esc(login)}</div></td>
    <td class="mono mut">${esc(u.mxid||'—')}</td>
    <td><span class="pill ${u.invite}">${({accepted:'✅ принял',pending:'⏳ ждёт',none:'— нет DM'})[u.invite]}</span></td>
    <td><span class="switch"><input type="checkbox" ${u.muted?'':'checked'} onchange="setUser('${login}',{muted:!this.checked})"><span class="slider"></span></span></td>
    <td><select onchange="setUser('${login}',{push:this.value})">${S.push_modes.map(m=>`<option ${u.push===m?'selected':''}>${m}</option>`).join('')}</select></td></tr>`).join('')
    ||'<tr><td colspan=5 class="mut">Никого не найдено.</td></tr>';
}
function renderRules(){
  $('rules').innerHTML=S.rules.map(r=>`<tr>
    <td><span class="switch"><input type="checkbox" ${r.enabled?'checked':''} onchange="setRule('${r.event}',{enabled:this.checked})"><span class="slider"></span></span></td>
    <td><b>${esc(r.event)}</b>${r.actions?' <span class="mut mono">'+esc(r.actions.join(','))+'</span>':''}</td>
    <td class="mono">${esc(r.template)}</td>
    <td>${['room','dm'].map(d=>`<label class="tag" style="cursor:pointer"><input type="checkbox" ${r.to.includes(d)?'checked':''} onchange="toggleDest('${r.event}','${d}',this.checked)"> ${d}</label>`).join('')}</td></tr>`).join('');
}
$('killSwitch').addEventListener('change',async e=>{await api('/global','POST',{enabled:e.target.checked});toast(e.target.checked?'Бот включён':'Бот выключен');load();});
async function setUser(login,patch){await api('/user/'+login,'POST',patch);toast('Сохранено: '+login);}
async function setRule(ev,patch){await api('/rule/'+ev,'POST',patch);toast('Правило: '+ev);load();}
function toggleDest(ev,d,on){const r=S.rules.find(x=>x.event===ev);let to=r.to.slice();if(on){if(!to.includes(d))to.push(d);}else{to=to.filter(x=>x!==d);}setRule(ev,{to});}
async function saveSchedule(){
  const anchor=[...document.querySelectorAll('#anchorDays .day.sel')].map(e=>+e.dataset.d);
  const holidays=$('holidays').value.split('\n').map(s=>s.trim()).filter(Boolean);
  await api('/global','POST',{anchor_days:anchor,weekly_day:+$('weeklyDay').value,skip_weekends:$('skipWeekends').checked,holidays});
  toast('Расписание сохранено');load();
}
async function trigger(name,btn){btn.disabled=true;const o=btn.textContent;btn.textContent='⏳ '+name;
  try{const r=await api('/trigger/'+name,'POST');
    if(r.ok)toast(`«${name}»: отправлено ${r.sent}`);else toast('Ошибка: '+(r.error||''),true);
  }catch(e){toast('Ошибка',true);}
  btn.disabled=false;btn.textContent=o;
}
async function loadTpl(){const name=$('tplSelect').value;if(!name)return;const r=await api('/template?name='+encodeURIComponent(name));$('tplBody').value=r.content;previewTpl();}
let pvTimer=null;
function schedulePreview(){clearTimeout(pvTimer);pvTimer=setTimeout(previewTpl,500);}
async function previewTpl(){const r=await api('/template/preview','POST',{content:$('tplBody').value});
  if(r.ok){$('tplPreview').innerHTML=r.html;$('tplErr').textContent='';$('tplHint').textContent='✓ валиден';$('tplHint').style.color='var(--ok)';}
  else{$('tplErr').textContent=r.error;$('tplHint').textContent='✗ ошибка';$('tplHint').style.color='var(--bad)';}
}
async function saveTpl(){const r=await api('/template','POST',{name:$('tplSelect').value,content:$('tplBody').value});
  if(r.ok)toast('Шаблон сохранён');else toast('Ошибка: '+(r.error||''),true);}
async function loadLogs(){const f=$('logFilter').value;const r=await api('/logs?limit=150'+(f?'&status='+f:''));
  $('logs').innerHTML=(r.rows||[]).map(x=>`<tr class="${x.status==='error'?'logerr':''}">
    <td class="mono mut">${esc((x.ts||'').replace('T',' ').slice(5,16))}</td>
    <td><b>${esc(x.kind||'')}</b> <span class="mut">${esc(x.action||'')}</span></td>
    <td><span class="badge ${x.status}">${esc(x.status)}</span></td>
    <td class="mono">${esc(x.channel||'')}</td>
    <td class="mut">${esc(x.detail||'')}</td></tr>`).join('')||'<tr><td colspan=5 class="mut">Пусто.</td></tr>';
}
load();
</script>
</body></html>
"""
