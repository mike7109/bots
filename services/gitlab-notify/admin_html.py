# Single-page admin UI (served at /admin). Kept as one string so the service
# needs no static-file plumbing. Talks to /admin/api/* with the session cookie.
HTML = r"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>gitlab-notify · админка</title>
<style>
  :root{
    --bg:#0f1318; --panel:#161b22; --panel2:#1b212b; --line:#262d38;
    --fg:#e6edf3; --mut:#8b95a5; --accent:#fc6d26; --accent2:#7dc4ff;
    --ok:#3fb950; --warn:#d29922; --bad:#f85149;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 'Inter','Segoe UI',system-ui,sans-serif}
  a{color:var(--accent2);text-decoration:none}
  .wrap{max-width:1080px;margin:0 auto;padding:20px}
  header{display:flex;align-items:center;gap:14px;margin-bottom:20px;flex-wrap:wrap}
  header h1{font-size:18px;margin:0;font-weight:700;display:flex;align-items:center;gap:10px}
  .logo{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,#fc6d26,#e24329);display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:13px}
  .spacer{flex:1}
  .pill{padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;border:1px solid var(--line)}
  .pill.on{background:rgba(63,185,80,.15);color:var(--ok);border-color:rgba(63,185,80,.4)}
  .pill.off{background:rgba(248,81,73,.15);color:var(--bad);border-color:rgba(248,81,73,.4)}
  .pill.accepted{background:rgba(63,185,80,.12);color:var(--ok)}
  .pill.pending{background:rgba(210,153,34,.12);color:var(--warn)}
  .pill.none{background:rgba(139,149,165,.12);color:var(--mut)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  @media(max-width:780px){.grid{grid-template-columns:1fr}}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
  .card h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);margin:0 0 12px}
  .card.wide{grid-column:1/-1}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:middle}
  th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase}
  tr:last-child td{border-bottom:none}
  .mut{color:var(--mut)} .mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
  button{font:inherit;cursor:pointer;border-radius:8px;border:1px solid var(--line);background:var(--panel2);color:var(--fg);padding:7px 12px}
  button:hover{border-color:var(--accent2)}
  button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
  button.danger{background:rgba(248,81,73,.15);border-color:rgba(248,81,73,.4);color:var(--bad)}
  button.sm{padding:4px 9px;font-size:12px}
  select,input[type=text],textarea{font:inherit;background:var(--panel2);color:var(--fg);border:1px solid var(--line);border-radius:7px;padding:6px 8px}
  textarea{width:100%;min-height:90px;font-family:ui-monospace,Menlo,monospace;font-size:12px;resize:vertical}
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
  .toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:var(--panel2);border:1px solid var(--line);padding:10px 16px;border-radius:9px;opacity:0;transition:.2s;pointer-events:none;z-index:50}
  .toast.show{opacity:1}
  .overlay{position:fixed;inset:0;background:rgba(8,10,14,.75);display:flex;align-items:center;justify-content:center;z-index:40}
  .modal{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;width:min(640px,92vw)}
  .login{max-width:340px;margin:14vh auto;text-align:center}
  .login input{width:100%;margin:12px 0;padding:10px}
  .hide{display:none}
  .tag{font-size:11px;padding:1px 7px;border-radius:6px;background:var(--panel2);border:1px solid var(--line);color:var(--mut);margin-right:4px}
</style></head>
<body>
<div id="app" class="hide">
  <div class="wrap">
    <header>
      <h1><span class="logo">GB</span> gitlab-notify</h1>
      <span id="statusPill" class="pill off">…</span>
      <div class="spacer"></div>
      <label class="row" style="margin:0;gap:8px"><span class="mut">Бот включён</span>
        <span class="switch"><input type="checkbox" id="killSwitch"><span class="slider"></span></span></label>
      <button class="sm" onclick="logout()">Выйти</button>
    </header>

    <div class="grid">
      <div class="card wide">
        <h2>Получатели · кто принял инвайт бота</h2>
        <table><thead><tr><th>Человек</th><th>MXID</th><th>Инвайт</th><th>Уведомления</th><th>Пуш</th></tr></thead>
        <tbody id="users"></tbody></table>
      </div>

      <div class="card">
        <h2>Расписание дайджестов</h2>
        <div class="mut" style="margin-bottom:6px">Якорные дни (полный обзор даже без изменений)</div>
        <div class="days" id="anchorDays"></div>
        <div class="row"><span class="mut">Гигиена (триаж/stale), день:</span>
          <select id="weeklyDay"></select></div>
        <div class="row"><span class="mut">Тишина по выходным</span>
          <span class="switch"><input type="checkbox" id="skipWeekends"><span class="slider"></span></span></div>
        <div class="mut" style="margin:8px 0 4px">Праздники (ISO-даты, по одной в строке) — тоже тишина</div>
        <textarea id="holidays" placeholder="2026-01-01&#10;2026-05-09"></textarea>
        <div class="row"><button class="primary" onclick="saveSchedule()">Сохранить расписание</button>
          <span id="schedHint" class="mut"></span></div>
      </div>

      <div class="card">
        <h2>Запустить рассылку сейчас</h2>
        <div class="mut" style="margin-bottom:10px">Ручной триггер cron-проходов (отправит сразу, минуя расписание).</div>
        <div class="triggers" id="triggers"></div>
      </div>

      <div class="card">
        <h2>Правила маршрутизации</h2>
        <table><thead><tr><th>Событие</th><th>Шаблон</th><th>Куда</th></tr></thead>
        <tbody id="rules"></tbody></table>
      </div>

      <div class="card">
        <h2>Шаблоны сообщений</h2>
        <div id="templates" class="triggers"></div>
      </div>
    </div>
  </div>
</div>

<div id="login" class="hide"><div class="login card">
  <h1 style="justify-content:center"><span class="logo">GB</span></h1>
  <div class="mut">Админка gitlab-notify</div>
  <input type="password" id="pw" placeholder="Пароль админа" onkeydown="if(event.key=='Enter')doLogin()">
  <button class="primary" style="width:100%" onclick="doLogin()">Войти</button>
  <div id="loginErr" class="mut" style="color:var(--bad);margin-top:8px"></div>
</div></div>

<div id="modalWrap" class="overlay hide"><div class="modal">
  <h2 id="modalName" style="margin-top:0"></h2>
  <textarea id="modalBody" style="min-height:200px"></textarea>
  <div class="row" style="justify-content:flex-end">
    <button onclick="closeModal()">Отмена</button>
    <button class="primary" onclick="saveTemplate()">Сохранить шаблон</button>
  </div>
  <div id="modalHint" class="mut"></div>
</div></div>

<div id="toast" class="toast"></div>

<script>
const DAYS=["Пн","Вт","Ср","Чт","Пт","Сб","Вс"];
let S=null;
const $=id=>document.getElementById(id);
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

async function load(){
  let s; try{s=await api('/state');}catch(e){return;}
  S=s; showApp();
  $('statusPill').className='pill '+(s.enabled?'on':'off');
  $('statusPill').textContent=s.enabled?'РАБОТАЕТ':'ВЫКЛЮЧЕН';
  $('killSwitch').checked=s.enabled;
  // users
  $('users').innerHTML=Object.entries(s.users).map(([login,u])=>`
    <tr><td><b>${esc(u.name)}</b><div class="mut mono">${esc(login)}</div></td>
    <td class="mono mut">${esc(u.mxid||'—')}</td>
    <td><span class="pill ${u.invite}">${({accepted:'✅ принял',pending:'⏳ ждёт',none:'— нет DM'})[u.invite]}</span></td>
    <td><span class="switch"><input type="checkbox" ${u.muted?'':'checked'} onchange="setUser('${login}',{muted:!this.checked})"><span class="slider"></span></span></td>
    <td><select onchange="setUser('${login}',{push:this.value})">${s.push_modes.map(m=>`<option ${u.push===m?'selected':''}>${m}</option>`).join('')}</select></td></tr>`).join('');
  // schedule
  $('anchorDays').innerHTML=DAYS.map((d,i)=>`<span class="day ${s.schedule.anchor_days.includes(i)?'sel':''}" data-d="${i}" onclick="this.classList.toggle('sel')">${d}</span>`).join('');
  $('weeklyDay').innerHTML=DAYS.map((d,i)=>`<option value="${i}" ${s.schedule.weekly_day===i?'selected':''}>${d}</option>`).join('');
  $('skipWeekends').checked=s.schedule.skip_weekends;
  $('holidays').value=(s.schedule.holidays||[]).join('\n');
  // triggers
  $('triggers').innerHTML=s.passes.map(p=>`<button class="sm" onclick="trigger('${p}',this)">▶ ${p}</button>`).join('');
  // rules
  $('rules').innerHTML=s.rules.map(r=>`<tr><td><b>${esc(r.event)}</b>${r.actions?' <span class="mut mono">'+esc(r.actions.join(','))+'</span>':''}</td>
    <td class="mono">${esc(r.template)}</td><td>${(r.to||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</td></tr>`).join('');
  // templates
  $('templates').innerHTML=s.templates.map(t=>`<button class="sm" onclick="openTemplate('${t}')">📄 ${esc(t)}</button>`).join('');
}
function esc(x){return String(x==null?'':x).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

$('killSwitch').addEventListener('change',async e=>{await api('/global','POST',{enabled:e.target.checked});toast(e.target.checked?'Бот включён':'Бот выключен');load();});
async function setUser(login,patch){await api('/user/'+login,'POST',patch);toast('Сохранено: '+login);}
async function saveSchedule(){
  const anchor=[...document.querySelectorAll('#anchorDays .day.sel')].map(e=>+e.dataset.d);
  const holidays=$('holidays').value.split('\n').map(s=>s.trim()).filter(Boolean);
  await api('/global','POST',{anchor_days:anchor,weekly_day:+$('weeklyDay').value,skip_weekends:$('skipWeekends').checked,holidays});
  toast('Расписание сохранено');load();
}
async function trigger(name,btn){btn.disabled=true;btn.textContent='⏳ '+name;
  try{const r=await api('/trigger/'+name,'POST');
    if(r.ok)toast(`«${name}»: отправлено ${r.sent}`);else toast('Ошибка: '+(r.error||''),true);
  }catch(e){toast('Ошибка',true);}
  btn.disabled=false;btn.textContent='▶ '+name;
}
let curTpl=null;
async function openTemplate(name){const r=await api('/template?name='+encodeURIComponent(name));curTpl=name;
  $('modalName').textContent=name;$('modalBody').value=r.content;$('modalHint').textContent='';$('modalWrap').classList.remove('hide');}
function closeModal(){$('modalWrap').classList.add('hide');}
async function saveTemplate(){const r=await api('/template','POST',{name:curTpl,content:$('modalBody').value});
  if(r.ok){toast('Шаблон сохранён');closeModal();}else{$('modalHint').textContent=r.error||'ошибка';$('modalHint').style.color='var(--bad)';}}

load();
</script>
</body></html>
"""
