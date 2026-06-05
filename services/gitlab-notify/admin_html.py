# Single-page admin UI (served at /admin). The HTML markup stays a Python
# constant (served as-is, no per-request file read); CSS and JS live in
# static/admin.css + static/admin.js, mounted at /admin/static (see app.py).
# Talks to /admin/api/* with the session cookie. Tabbed layout.
HTML = r"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>gitlab-notify · админка</title>
<link rel="stylesheet" href="/admin/static/admin.css"></head>
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

  <!-- Баннер предохранителя — виден на всех вкладках, когда защита сработала -->
  <div id="breakerBanner"></div>

  <div class="tabs" id="tabs"></div>

  <!-- Дашборд -->
  <div class="view active" data-v="dash">
    <div id="errBanner"></div>
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
    <p class="hint" style="margin:0 0 14px">Каждая рассылка: что делает, когда бот её шлёт (дни + время — планирует сам, host-cron не нужен) и кнопка «запустить сейчас». Дайджесты разделены на «полный» (полная сводка) и «изменения» (дельта со вчера) — это отдельные рассылки со своим расписанием.</p>
    <details class="ref"><summary>📖 Справка: кнопки запуска и режимы</summary>
      <div class="refbody">
        <p>У каждой рассылки три-четыре кнопки. Запуск вручную нужен для проверки и для разовой отправки вне расписания.</p>
        <ul class="reflist">
          <li><b>👁 Пример</b> — как выглядит сообщение на <b>примерных</b> данных. Ничего не отправляет, реальные задачи не трогает.</li>
          <li><b>🔍 Предпросмотр</b> — то же, но на <b>реальных сегодняшних</b> данных: показывает, <b>кому</b> и что уйдёт, но <b>не отправляет</b>.</li>
          <li><b>▶ Запустить сейчас</b> — реально отправить эту рассылку прямо сейчас (для дайджестов «полный» шлёт полную сводку, «изменения» — дельту со вчера; это отдельные рассылки). Можно жать сколько угодно — шлёт каждый раз и не влияет на авто-расписание.</li>
        </ul>
        <p><b>Что важно знать:</b></p>
        <ul class="reflist">
          <li>Ручной запуск <b>не влияет на авто-расписание</b> — не занимает дневной слот и не сдвигает «точку отсчёта» дельты. Планировщик отработает как обычно.</li>
          <li>«Только изменения» <b>без базовой точки</b> ничего не покажет — сначала отправь «Всё» один раз (или дождись планировщика), чтобы её задать.</li>
          <li>Личные дайджесты/просрочки шлют ЛС <b>всем исполнителям</b> — перед отправкой спросим подтверждение с числом получателей.</li>
          <li>«Пусто / изменений нет» — это <b>нормальный результат</b>, а не ошибка.</li>
        </ul>
      </div>
    </details>
    <div class="card"><div class="row" style="margin:0"><b style="font-size:15px">⏱ Авторассылки (планировщик)</b>
      <span class="mut" style="font-size:12px">— бот сам шлёт по расписанию. Выключи → пойдут только вебхуки (issue в комнату), дайджесты молчат.</span>
      <span class="spacer"></span>
      <span class="switch"><input type="checkbox" id="schedOn" onchange="api('/global','POST',{scheduler_on:this.checked}).then(()=>toast(this.checked?'Авторассылки включены':'Авторассылки выключены'))"><span class="slider"></span></span></div></div>
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
    <div class="card"><h2>🕗 Часы активности</h2>
      <p class="hint">Вне этого окна бот молчит — плановые рассылки не уходят. Дополняет нерабочие дни.</p>
      <div class="row"><span class="mut">Включить ограничение по времени</span>
        <span class="switch"><input type="checkbox" id="ahOn"><span class="slider"></span></span></div>
      <div class="row" style="margin-top:10px"><span class="mut">С</span>
        <input type="time" id="ahFrom"><span class="mut">до</span><input type="time" id="ahUntil"></div>
      <div class="row" style="margin-top:10px"><span class="mut">Тихие часы и для вебхуков</span>
        <span class="switch"><input type="checkbox" id="ahWh"><span class="slider"></span></span></div>
      <p class="hint" style="margin:6px 0 12px">По умолчанию realtime issue-события идут всегда; включите, чтобы и вебхуки молчали вне окна.</p>
      <div class="row"><button class="primary" onclick="saveActiveHours()">Сохранить</button></div>
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
    <div class="card"><h2>🔔 Алерты инженерам</h2>
      <p class="hint">Если рассылка бота падает (протух Matrix-токен, GitLab недоступен, баг), выбранные люди получат личное сообщение от бота — чтобы тихий простой на несколько дней не остался незамеченным. Они должны принять DM-инвайт бота, иначе сообщение не дойдёт (тогда смотри красный баннер и «Логи» на Дашборде). Алерт по одной и той же поломке шлётся не чаще раза в день.</p>
      <div class="row"><span class="mut">Слать алерты</span>
        <span class="switch"><input type="checkbox" id="alertsOn"><span class="slider"></span></span></div>
      <div class="mut" style="margin:8px 0 4px">Кто получает алерты о сбоях бота:</div>
      <div id="alertEngineers" style="max-height:260px;overflow:auto"></div>
      <div class="row"><button class="primary" onclick="saveAlerts()">Сохранить</button></div>
    </div>
    <div class="card"><h2>🛡 Защита от спама</h2>
      <p class="hint">Предохранитель. Если из-за бага бот начнёт слать слишком много (флуд) — он сам остановит все отправки и позовёт инженеров. Пороги выше нормального объёма рассылок, так что в обычной работе не срабатывает.</p>
      <details class="ref"><summary>📖 Что это и как сбрасывать</summary>
        <div class="refbody">
          <p>Бот считает свои отправки в скользящем окне. Если за окно их станет больше порога (всего, на одного получателя, или одно и то же сообщение подряд) — это похоже на сбой/флуд. Тогда срабатывает предохранитель: отправки <b>останавливаются</b>, наверху появляется красный баннер, инженеры получают алерт.</p>
          <p>Пороги намеренно <b>выше</b> нормального объёма (полный дайджест ≈ десяток ЛС + сводка) — в штатной работе защита молчит.</p>
          <p>Сброс — <b>вручную</b> кнопкой «Сбросить предохранитель» в баннере (сначала разберись, почему сработало). Если задан авто-сброс (кулдаун &gt; 0) — бот снимет блокировку сам через указанное время.</p>
        </div>
      </details>
      <div class="row"><span class="mut">Защита включена</span>
        <span class="switch"><input type="checkbox" id="grdOn"><span class="slider"></span></span></div>
      <div id="guardFields"></div>
      <div class="row"><button class="primary" onclick="saveGuard()">Сохранить</button>
        <span class="mut" style="font-size:12px">— изменения порогов применятся при следующем перезапуске; сброс работает сразу.</span></div>
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
<script src="/admin/static/admin.js"></script>
</body></html>
"""
