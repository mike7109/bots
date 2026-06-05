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
  .switch input{position:absolute;inset:0;width:100%;height:100%;margin:0;opacity:0;cursor:pointer;z-index:2}
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
  .ref{margin:6px 0 14px;border:1px solid var(--line);border-radius:8px;background:var(--panel2)}
  .ref summary{cursor:pointer;padding:9px 12px;font-weight:600;color:var(--accent2);font-size:13px;list-style:none}
  .ref summary::-webkit-details-marker{display:none}
  .ref summary:before{content:"▸ "} .ref[open] summary:before{content:"▾ "}
  .refbody{padding:0 14px 12px;font-size:13px} .refbody p{margin:8px 0}
  .reflist{margin:4px 0 8px;padding-left:18px} .reflist li{margin:3px 0}
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
  <div class="view active" data-v="dash">
    <div id="cfgBanner"></div>
    <div class="card"><h2>Статистика отправок</h2>
      <p class="hint">Сколько и чего бот отправил. Ошибки подсвечены — если их больше нуля, загляни во вкладку «Логи».</p>
      <div id="stats"></div>
      <div id="byKind" style="margin-top:8px"></div>
    </div>
  </div>

  <!-- Группы -->
  <div class="view" data-v="src">
    <p class="hint" style="margin:0 0 14px">Какие GitLab-группы бот слушает и в какой чат шлёт. Вебхуки маршрутизируются по группе проекта; дайджесты опрашивают каждую группу своим токеном. Кнопка «Проверить доступ» дёргает GitLab API и показывает имя группы и число issue.</p>
    <div id="srcCards"></div>
  </div>

  <!-- Получатели -->
  <div class="view" data-v="users">
    <div class="card"><h2>Получатели</h2>
      <p class="hint">Кто получает личные уведомления. «Инвайт» — принял ли человек DM-бота (пока «ждёт» — личка ему не дойдёт, копится). Выключатель — мьют всех его уведомлений. «Пуш»: default — как задумано, loud — всегда с пушем, quiet — тихо (без пуша).</p>
      <div class="row" style="margin-top:0">
        <input type="search" id="userSearch" placeholder="Поиск по имени/логину…" oninput="renderUsers()" style="width:240px">
        <div class="days" id="userFilters"></div>
        <span class="spacer"></span><span id="userCount" class="mut"></span>
        <button class="sm" onclick="inviteBlast(this)" title="Сообщение в общую комнату с @-тегами тех, кто не принял бота">🔔 Позвать принять бота</button>
      </div>
      <div style="max-height:420px;overflow:auto;margin-top:8px">
      <table><thead><tr><th>Человек</th><th>MXID</th><th>Инвайт</th><th>Уведомления</th><th>Пуш</th></tr></thead>
      <tbody id="users"></tbody></table>
      </div>
    </div>
  </div>

  <!-- Рассылки (расписание + триггеры) -->
  <div class="view" data-v="sched">
    <p class="hint" style="margin:0 0 14px">Каждая рассылка: что делает, когда бот её шлёт (дни + время — планирует сам, host-cron не нужен) и кнопка «запустить сейчас». ⚓ якорные дни — для дайджестов полный обзор вместо «только изменения».</p>
    <div id="passCards"></div>
  </div>

  <!-- Календарь -->
  <div class="view" data-v="cal">
    <div class="card"><h2>Нерабочие дни — тишина</h2>
      <p class="hint">В нерабочие дни бот молчит (никаких рассылок). Можно подтянуть производственный календарь РФ автоматически (isdayoff.ru, с учётом переносов) и/или дописать свои даты.</p>
      <div class="row"><span class="mut">Авто-праздники РФ (isdayoff.ru)</span>
        <span class="switch"><input type="checkbox" id="holAuto" onchange="api('/global','POST',{holidays_auto:this.checked}).then(()=>toast('Сохранено'))"><span class="slider"></span></span></div>
      <div class="mut" style="margin:8px 0 4px">Свои даты (ISO, по одной в строке) — отпуск всей команды, локальные праздники и т.п.</div>
      <textarea id="holidays" placeholder="2026-01-01&#10;2026-05-09"></textarea>
      <div class="row"><button class="primary" onclick="saveHolidays()">Сохранить даты</button></div>
    </div>
  </div>

  <!-- Правила -->
  <div class="view" data-v="rules">
    <p class="hint" style="margin:0 0 14px">Какие уведомления бот шлёт и куда. Каждое можно выключить или сменить адрес (Комната / Личка). Ниже — живой пример, как выглядит сообщение.</p>
    <div id="rules"></div>
  </div>

  <!-- Шаблоны -->
  <div class="view" data-v="tpl">
    <div class="card"><h2>Шаблоны сообщений</h2>
      <p class="hint">Текст уведомлений (Jinja2 + Matrix HTML). Слева — исходник, справа — живой предпросмотр на примерных данных. Ошибки шаблона подсветятся.</p>
      <details class="ref"><summary>📖 Справка: как устроены шаблоны, какие есть переменные</summary>
        <div class="refbody">
          <p><b>Как редактировать:</b> выбери шаблон, правь текст слева — справа сразу видно, как отрендерится сообщение (на примерных данных). Красным подсветится ошибка Jinja. Жми «Сохранить».</p>
          <p><b>✅ Можно менять:</b> текст, эмодзи, порядок строк, какие поля показывать, разметку (жирный/курсив/ссылки/списки).<br>
          <b>⛔ Нельзя:</b> имена переменных и хелперов (данные приходят из кода бота) и имя файла шаблона (привязано к типу уведомления). Matrix понимает только часть HTML: <span class="mono">&lt;b&gt; &lt;i&gt; &lt;a&gt; &lt;br&gt; &lt;ul&gt;/&lt;li&gt; &lt;code&gt; &lt;blockquote&gt;</span>.</p>
          <p><b>Переменные события</b> (issue / дедлайн / просрочка):</p>
          <ul class="reflist">
            <li><span class="mono">{{title}}</span> — заголовок · <span class="mono">{{iid}}</span> — номер (#42) · <span class="mono">{{url}}</span> — ссылка</li>
            <li><span class="mono">{{labels}}</span> — метки · <span class="mono">{{assignees}}</span> — исполнители (логины) · <span class="mono">{{due}}</span> — срок · <span class="mono">{{action}}</span>/<span class="mono">{{state}}</span></li>
          </ul>
          <p><b>Для дайджестов</b> данные лежат в <span class="mono">extra</span>:</p>
          <ul class="reflist">
            <li><span class="mono">extra.date</span>, <span class="mono">extra.total</span>, <span class="mono">extra.open_total</span></li>
            <li><span class="mono">extra.sections</span> — список секций <span class="mono">{emoji,title,items,show_who}</span>, элемент: <span class="mono">{iid,title,url,due,assignees,idle}</span></li>
            <li><span class="mono">extra.changes</span> (дельта): <span class="mono">new / moved / overdue / today / due / removed</span></li>
            <li>метрики: <span class="mono">extra.throughput / wip / age_med / cycle_p85 / window</span></li>
          </ul>
          <p><b>Хелперы:</b></p>
          <ul class="reflist">
            <li><span class="mono">{{ due | ru_date }}</span> → «5 июня»</li>
            <li><span class="mono">{{ action | action_emoji }}</span> → 🟢/🔴/⏰ · <span class="mono">{{ action | ru_action }}</span> → «Открыта»</li>
            <li><span class="mono">{{ who(assignees) }}</span> → « · @Имя и @Имя» (исполнители)</li>
            <li><span class="mono">{{ mention(login) }}</span> / <span class="mono">{{ mentions(list) }}</span> → @-пилюли</li>
            <li><span class="mono">{{ meta(labels, assignees) }}</span> → « · 🏷метки · @кто» · <span class="mono">{{ labels_html(labels) }}</span> → 🏷-чипы</li>
            <li><span class="mono">{{ title | truncate(70, true, '…', 0) }}</span> → обрезка длинного текста</li>
          </ul>
        </div>
      </details>
      <div class="row"><select id="tplSelect" onchange="loadTpl()"></select>
        <button class="sm" onclick="previewTpl()">Предпросмотр</button>
        <button class="primary sm" onclick="saveTpl()">Сохранить</button>
        <button class="sm" onclick="resetTpl()" title="Удалить правку из БД, вернуть дефолт из файла">↩ Сбросить</button>
        <span id="tplOv"></span><span id="tplHint" class="mut"></span></div>
      <p class="hint" style="margin:4px 0 0">Правки сохраняются как оверрайд в БД (файлы не меняются). «Сбросить» — вернуть дефолт.</p>
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

  <!-- Настройки -->
  <div class="view" data-v="conn">
    <div class="card"><h2>Подключения</h2>
      <p class="hint">Адреса и секреты бота — хранятся в БД, меняются на лету. Заданный секрет показан как «🔒 задан …xxxx» — жми «Заменить», чтобы ввести новый. Matrix-токен — один аккаунт бота на все комнаты (per-group задаются только GitLab-токены во вкладке «Группы»).</p>
      <div class="row"><span class="mut" style="width:150px">Matrix homeserver</span><input type="text" id="cHs" style="width:300px" placeholder="https://matrix.…"></div>
      <div class="row"><span class="mut" style="width:150px">Matrix токен бота</span><span id="cTokWrap"></span>
        <button class="sm" onclick="checkMatrix(this)">🔌 Проверить</button><span id="cMx" style="font-size:13px"></span></div>
      <div class="row"><span class="mut" style="width:150px">Секрет вебхука</span><span id="cWhWrap"></span></div>
      <div class="row"><span class="mut" style="width:150px">GitLab URL</span><input type="text" id="cGl" style="width:300px" placeholder="https://git.…"></div>
      <div class="row"><button class="primary" onclick="saveConn()">Сохранить адреса</button><span class="mut" style="font-size:12px">— homeserver и GitLab URL (секреты сохраняются своей кнопкой выше)</span></div>
    </div>
    <div class="card"><h2>Проверка конфигурации</h2>
      <p class="hint">Прогоняет всё: Matrix, секрет вебхука, GitLab URL, и каждую группу (доступ по токену + состоит ли бот в её комнате). Видно, что не настроено.</p>
      <div class="row"><button class="primary" onclick="runHealth()">✅ Проверить всё</button><span id="healthSum" style="font-size:13px"></span></div>
      <div id="healthList" style="margin-top:10px"></div>
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
const TABS=[["dash","Дашборд"],["src","Группы"],["users","Получатели"],["sched","Рассылки"],["cal","Календарь"],["rules","Правила"],["tpl","Шаблоны"],["logs","Логи"],["conn","Настройки"]];
const PASS_INFO={
  due:{icon:'📅',title:'Дедлайн завтра',tpl:'due_soon',desc:'Напоминание в общую комнату об issue, у которых срок наступает завтра.'},
  overdue:{icon:'⏰',title:'Просрочки',tpl:'overdue',desc:'Личное напоминание исполнителю об его issue с прошедшим сроком.'},
  digest:{icon:'📋',title:'Личный дайджест «Задачи на сегодня»',tpl:'digest_personal',desc:'Каждому в личку его задачи. В якорные дни — полный обзор, в остальные — только изменения со вчера.'},
  team:{icon:'🗓',title:'Сводка команды',tpl:'digest_team',desc:'Обзор задач команды в общую комнату: просрочено / сегодня / ближайшие / в работе.'},
  triage:{icon:'🧹',title:'Триаж',tpl:'triage',desc:'Что требует внимания: без исполнителя или без срока. Обычно раз в неделю.'},
  stale:{icon:'🕸',title:'Зависшие задачи',tpl:'stale',desc:'Открытые issue без активности ≥ N дней (STALE_DAYS). Обычно раз в неделю.'},
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
  renderStats(s.stats);
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
    return `<div class="card" data-p="${p}">
      <div class="row" style="margin:0"><div style="font-size:15px"><b>${info.icon} ${esc(info.title)}</b> <span class="mut mono" style="font-size:11px">${esc(p)}</span></div>
        <span class="spacer"></span>
        <label class="row" style="margin:0;gap:6px"><span class="mut" style="font-size:12px">вкл</span><span class="switch"><input type="checkbox" class="pEn" ${c.enabled?'checked':''}><span class="slider"></span></span></label>
        <button class="sm" onclick="passExample('${info.tpl}',this)">👁 пример</button>
        <button class="sm" onclick="editTpl('${info.tpl}')">✎ шаблон</button>
        <button class="sm" onclick="trigger('${p}',this)">▶ Запустить сейчас</button></div>
      <p class="hint" style="margin:6px 0 12px">${esc(info.desc)}</p>
      <div class="bubble pEx hide" style="margin-bottom:10px"></div>
      <div class="row" style="margin:0"><span class="mut">Дни:</span><div class="days pDays">${miniDays(c.days||[],'d')}</div>
        <span class="mut" style="margin-left:8px">Время:</span><input type="time" class="pTime" value="${esc(c.time||'09:00')}">
        ${hasA?`<span class="mut" style="margin-left:8px" title="полный обзор вместо «только изменения»">⚓ Якорь:</span><div class="days pAnchor">${miniDays(c.anchor_days||[],'a')}</div>`:''}
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
async function trigger(name,btn){
  const card=btn.closest('.card');const res=card?card.querySelector('.pResult'):null;
  btn.disabled=true;const o=btn.textContent;btn.textContent='⏳ запуск…';
  if(res){res.style.color='var(--mut)';res.textContent='Запускаю…';}
  try{const r=await api('/trigger/'+name,'POST');
    if(r.ok){const m=`✓ отправлено получателям: ${r.sent}`;toast(`«${name}»: отправлено ${r.sent}`);if(res){res.style.color='var(--ok)';res.textContent=m;}}
    else{const m='✗ ошибка: '+(r.error||'неизвестно');toast('Ошибка триггера',true);if(res){res.style.color='var(--bad)';res.textContent=m;}}
  }catch(e){if(res){res.style.color='var(--bad)';res.textContent='✗ '+e;}toast('Ошибка',true);}
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
</script>
</body></html>
"""
