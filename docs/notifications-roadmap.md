# Уведомления и продуктивность команды — роадмап и ресёрч

Контекст: инфра/devops-команда ~6 человек, self-hosted **GitLab 17.6 Ultimate** +
Matrix/Element. Есть бот-каркас `gitlab-notify` (вебхуки → правила
`событие→шаблон→назначение room/dm`, cron-опрос GitLab API) и `gitlab-expand-tasks`.
Цель — повысить продуктивность и понятность задач, докручивая существующий бот.

Документ собран из трёх веб-ресёрчей (фичи бота / метрики потока / процессы) и
сводит их в приоритизированный план. Полные отчёты с источниками — ниже.

---

## Scope — что делаем (решено, июнь 2026)

Команда использует GitLab **как issue-трекер**: ветки, CI/CD, merge requests —
**не используются**. Отсюда жёсткие границы проекта (переопределяют всё, что ниже):

**В scope** — всё по **issue**, всё на тарифе **Free**, один сервис `gitlab-notify`:
overdue в cron, хранилище состояния (SQLite), персональный DM-дайджест «что на мне»,
тим-дайджест (issue), нудж-триаж и гигиена, stale-issue, опционально issue-метрики
(throughput / WIP / возраст / cycle p85) как «термометр для ретро».

**Вне scope:**
- **DORA** и всё про CI/CD/деплои — пайплайнов нет, считать нечего.
- **MR-фичи** (ревью, stale-MR, ревьюеры) — MR не используются; клиент остаётся issue-only.
- **Ack/assign из чата и async-standup** (интерактив) — отложено; бот остаётся
  **send-only**, входящий слушатель Matrix не нужен.
- **Ultimate не нужен** — единственной Ultimate-фичей была DORA. Хватает Free.

**Порядок реализации:** 1) overdue в cron → 2) хранилище SQLite → 3) персональный
DM-дайджест → 4) тим-дайджест (issue) → 5) нудж-триаж/гигиена → 6) stale-issue →
7) (опц.) issue-метрики.

Разделы ниже про MR / DORA / VSA / интерактив оставлены как **ресёрч-контекст** и
помечены «вне scope»; в план они не входят.

> **Что от какого тарифа:** из перечисленного **Ultimate-only только DORA-API**
> (`/dora/metrics`). **scoped-метки** (`priority::`, `severity::`, `workflow::`) и
> WIP-лимиты на доске — это **Premium+** (на Ultimate, разумеется, есть). Value Stream
> Analytics с дефолтными стадиями работает даже на **Free** (агрегация/кастомные
> стадии — Premium+). Ядро flow-метрик (Issues API + `resource_label_events` +
> `resource_state_events`) — **тариф-независимо (Free)**. NB: `type::` дублирует
> нативное поле **Type** work item (Issue/Task/Incident, есть на Free) — лучше
> опираться на него, чем на scoped-метку.

---

## TL;DR — приоритизированный роадмап

Сквозной вывод: главный враг маленькой команды — **notification fatigue**, а не
молчание. Бот уникален тем, что объединяет поток событий + дайджесты + нуджи
(ни hookshot/нативная интеграция, ни дайджест-боты так не умеют). Почти всё
держится на **дисциплине данных**: due_date, стартовая метка `workflow::in progress`,
type/priority метки — от них зависят и напоминания, и метрики.

> **Условные обозначения трудозатрат.** В колонке «Бот»: «правило» = дешёвый
> реконфиг (маршрут уже готового эвента, новое правило open/close/reopen);
> «cron»/«новый код» = новый cron-проход и/или методы GitLab-клиента, которых пока
> нет, и (для нуджей/дедупа) — хранилище состояния (см. «Пререквизиты и архитектура»).
> Галочка ✅ = «ложится на нашу модель», **не** «уже готово».

### Tier 0 — соглашения (предусловие, S, без кода)
- Единая **стартовая метка** `workflow::in progress` (scoped — на **Premium+**).
- Проставлять **due_date**, нативный **Type** (Issue/Task/Incident) и метки
  **priority::** на задачах.

### Tier 1 — дёшево + высокий эффект (дни)
| Мера | Источник | Бот |
|---|---|---|
| Подавить потоковые эвенты, realtime только actionable (для нас: mention/assign по issue) | фичи #3 | правило (реконфиг) |
| **Тим-дайджест к стендапу** в комнату (новые/untriaged issue, ближайшие дедлайны, зависшие) | фичи #2 + процессы #4 | cron |
| **Персональный DM-дайджест «что на мне»** — самый высокий ROI | фичи #1 | cron |
| WIP-лимиты на доске GitLab (**Premium+**, лимит per-list) + DoD-чеклист в шаблоне + issue-шаблоны Bug/Task | процессы #1,2,3 | конфиг GitLab |

### Tier 2 — ядро «не терять задачи» (M)
| Мера | Бот |
|---|---|
| Дедлайны **soft / hard / miss** (DM → эскалация в room) | cron (есть только `due_soon`; `overdue`-проход + тиринг soft/hard/miss — новый код, M) |
| **Stale**-дайджест (issue без активности N дней) | cron |
| **Нудж-триаж**: дайджест untriaged issue | cron ✅ |
| Нудж по гигиене (без assignee/дедлайна/лейбла) | cron ✅ |
| Обвязка: дедуп/снуз/анти-флаппинг + тихие часы (TZ) | правило ✅ |

### Tier 3 — issue-метрики (опционально, M)
- **Еженедельный flow-отчёт** в комнату: Throughput, WIP, Work Item Age (топ
  застрявших), Cycle Time **p85**. Тариф-независимо через Issues API +
  `resource_label_events` + `resource_state_events` (всё Free). **Только командные
  метрики, не индивидуальные** — «термометр для ретро», не KPI.
- ~~**DORA**~~ — **вне scope** (нет CI/CD/деплоев).
- ~~**Ack/assign / async-standup**~~ — **вне scope** (интерактив отложен; бот send-only).

### Чего НЕ делать
- Слать каждый push/comment в канал (fatigue).
- **Индивидуальные метрики** (закрытий на человека, строки кода) — яд для доверия
  и гейминг (закон Гудхарта). **SPACE** прямо предостерегает от индивидуальной
  оценки; **DORA** — командно/системного уровня (явного запрета на индивидуальные
  метрики у неё нет, но она против гейминга).
- Vanity: velocity в story points, burndown, абсолюты вместо трендов.
- Внедрять всё сразу — калибровать на ретро.

---

## Пререквизиты и архитектура (добавлено при ревизии)

Роадмап завязан на три вещи, которых в текущем боте **нет** — без них значки
«cron ✅ / правило ✅» не реализуемы. Это отдельный пласт работ, делать раньше Tier 2.

### Хранилище состояния (новое — пререквизит Tier 2)
Дедуп, снуз, анти-флаппинг, идемпотентные нуджи, кэш обработанных closed issue и
baseline метрик требуют персистентности. Сейчас её нет нигде: cron эфемерный
(`docker compose run --rm`), `MatrixClient` держит только токен. Нужно: **SQLite на
смонтированном volume**, helper в `botkit` (общий слой), файл БД — на сервис.

### Методы GitLab-клиента (новый код)
`botkit/gitlab.py` сейчас умеет только `group_issues`/`get_paginated`/`graphql`. Под
наш (issue-only) scope нужны: `resource_label_events`/`resource_state_events`
(старт/закрытие для метрик) и issues по проекту с `closed_at`. MR/DORA-методы —
**не нужны** (вне scope).

### Cron как подкоманды (рефактор)
`cron.py` сейчас шлёт только `due_soon`. Расширять как entrypoint'ы на своих
расписаниях: `cron.py due|overdue|digest|stale|triage|metrics` — один сервис, разные
таймеры (host cron / systemd). Дайджесты требуют **aggregate-Event** (сейчас `Event`
скалярный, один item) и агрегации многих issue в бакеты по получателю.

### Сервисы: один outbound, и точка
Интерактив (ack/assign, standup) отложен → входящий слушатель Matrix не нужен, всё
помещается в **один outbound-сервис**:
- **Весь функционал — в `gitlab-notify`.** overdue, stale, нудж-триаж/гигиена, тим- и
  DM-дайджесты, issue-метрики — это «состояние GitLab → сообщение в Matrix» по
  вебхуку/cron. Одна модель (`Event→engine→transport`), один конфиг, один деплой,
  общий стор. Cron растёт подкомандами (`cron.py due|overdue|digest|stale|triage|metrics`).
- **`botkit` — общий слой** (matrix/gitlab/identity/notify/стор).

Итого контур: **`botkit` + один `gitlab-notify`** (outbound, +SQLite, +cron-подкоманды).
`gitlab-expand-tasks` остаётся как есть. Второй сервис заведём, только если вернёмся
к интерактиву (тогда — отдельный inbound, что *слушает* Matrix через `/sync`/appservice).

### Деплой / секреты / прод
- Стор → нужен **Docker volume** (сейчас монтируется только config `:ro`).
- Метрики (issue) → токену хватает чтения issues/событий, Reporter+ на проектах —
  ничего сверх текущего `GITLAB_TOKEN` со scope `api`.
- **E2EE**: все комнаты и DM — без шифрования (клиент не поддерживает Olm/Megolm).
  Особенно DM: Element по умолчанию шифрует личку → дайджест молча не расшифруется.
  В чек-лист раската: комната и DM без E2EE, человек один раз принимает инвайт бота.
- **TZ для тихих часов**: GitLab не отдаёт per-user таймзону в bulk — добавить поле
  `tz` в `users:` (для co-located команды хватит одной командной TZ).
- **Маппинг login→mxid** ручной в `config.yaml` (+fallback `@<login>:<domain>`); при
  промахе DM молча падает в общую комнату → «личное» утечёт в команду. Валидировать полноту.
- **Backfill метрик**: первый прогон Work Item Age/Cycle Time — O(#issues) обход
  событий per-issue; кэшировать baseline, steady-state дёшев только после разовой загрузки.

---

## Отчёт 1. Уведомления / ChatOps-бот GitLab→Matrix

### Принципы маршрутизации
- **Канал-vs-личка**: командно-значимое — в room; «это твоя задача/твой просрочен» — в DM.
- **Батчинг по умолчанию**: некритичное собирать в окно (день/час) и слать одной сводкой.
- **Реалтайм — только срочное и actionable**: review requested, pipeline failed на
  защищённой ветке, прямое упоминание. Остальное — в дайджест.
- **Respect work hours**: дайджесты/нуджи утром в рабочие часы по TZ получателя.
- **Off-ramp**: у каждого нудж/алерта — возможность «не актуально/сделано».

Существующие решения (matrix-hookshot, нативная интеграция) умеют только потоковую
трансляцию — matrix-hookshot фильтрует по лейблам, нативная интеграция лишь по типу
события/ветке/статусу пайплайна — но нет дайджестов, cron-дедлайнов, ack/assign,
нудж-механик. Из дайджест-ботов «только сводки» — это mergentle-reminder; Axolo,
наоборот, real-time «1 MR = 1 канал» (и оба Slack-only, не Matrix). Гигиену делает
gitlab-triage, но внутри GitLab, не в Matrix. Ваш бот объединяет эти классы — в этом
его ценность.

### Приоритизированные фичи
1. **Персональный утренний DM-дайджест «что на мне»** — эффект высокий, S/M, cron.
   Назначенные issue, мои близкие/просроченные дедлайны (MR — вне scope).
   Один батч вместо потока пингов. Самый высокий ROI.
2. **Утренний тим-дайджест в room** — высокий, S, cron. Для нас (issue-only):
   новые/untriaged issue, ближайшие дедлайны, зависшие. (MR-часть — вне scope.)
3. **Подавление потоковых эвентов** — высокий, S, правило. Realtime только для
   mention/assign по issue (review/pipeline у нас не используются).
4. **Stale/no-activity алерт по issue** — высокий, M, cron. Нет активности N дней →
   DM исполнителю; эскалация (3+ дней) — упоминание в room. (MR-вариант — вне scope.)
5. **Нудж по гигиене issue** (без assignee/дедлайна/лейбла) — средний, M, cron ✅.
   Батчем раз в день, идемпотентно, с дедупом.
6. **Дедлайны soft/hard/miss** — высокий, M, cron ✅. `deadline::soft` (за N дней — DM),
   `deadline::hard` (в день — DM+room), `deadline::miss` (просрочено — эскалация).
7. **Ack/assign из чата** — **[ВНЕ SCOPE — интерактив отложен]** средний, L, отдельный
   сервис. Reactions-as-actions + колбэк в GitLab API; требует *слушать* Matrix
   (`/sync`/appservice) — другой рантайм, чем send-only путь уведомлений.
8. **Дедуп/снуз/анти-флаппинг** — средний, S, правило. Не повторять нудж чаще раза
   в день, snooze до даты, группировка, не слать на draft-MR.
9. **Эскалация по severity/возрасту** — средний, S. Тихо в DM → эскалация в room.
10. **Тихие часы / TZ-aware** — низкий-средний, S.

Порядок: #2+#1 (дайджесты), #3 (подавление) → #6+#4 (дедлайны/stale) с #8 как
обвязкой → #5 (гигиена) → #7 (интерактив) последним.

---

## Отчёт 2. Метрики потока и их извлечение из GitLab

Для команды из ~6 — только **метрики потока на уровне команды**, не индивидуальные
(SPACE прямо предостерегает от индивидуальной оценки, activity-метрики — «никогда в
изоляции»; ломают коллаборацию, провоцируют гейминг — закон Гудхарта; DORA —
командно/системного уровня). Отчёт в Matrix подавать как «термометр для ретро», не KPI.

> На **Ultimate** доступны DORA-API и полная VSA. Тем не менее ядро удобно строить
> на сырых событиях Issues API — оно прозрачнее и не зависит от настройки VSA-стадий.

### Метрики
1. **Throughput** — высокий, S. Закрыто issue за неделю (штуки, не story points;
   тренд важнее абсолюта). `GET /projects/:id/issues?state=closed&updated_after=<начало окна>`,
   затем **двусторонний** клиентский фильтр `начало ≤ closed_at ≤ конец` (закрытие
   бампает `updated_at`, поэтому `updated_after` — безопасная нижняя граница;
   серверного `closed_after/closed_before` нет — FR #440227). Не делать таргетом
   (иначе дробление задач).
2. **WIP** — высокий, S. Открытые с меткой `workflow::in progress`. Сильный leading-
   индикатор (закон Литтла `WIP = Throughput × Cycle Time`: при той же пропускной
   способности меньше WIP → короче cycle time). Потолок для шестерых — единицы.
   (Титул «самой важной» источники отдают Work Item Age, не WIP — см. #3.)
3. **Work Item Age** — высокий, M. Возраст *открытых* задач от старта. «Самая важная»
   (ProKanban) — по ней ещё можно действовать. Старт = ранний `action:add` любой
   `workflow::`-метки в `resource_label_events` (проверено на живом 17.6: метка,
   выставленная при создании issue, тоже даёт `add`-событие). **Фоллбэк `created_at`**
   на случай импорта/миграции, где add-события может не быть. Подсветка топ-3 застрявших в отчёт.
4. **Cycle Time** — высокий, M. По закрытым: от старта (label event) до закрытия
   (`resource_state_events`, `state:closed`; reopen пишется отдельным событием
   `state:reopened` — проверено на живом 17.6 — поэтому для reopened-then-reclosed
   берём последний `closed` по `created_at`). **Перцентиль 85, не среднее.** Упрощённо
   (S): `closed_at − created_at` — но это, строго говоря, формула Lead Time (#5); без
   стартовой метки обе метрики схлопываются.
5. **Lead Time** — средний, S. `closed_at − created_at` (включает лежание в бэклоге;
   для реактивных тикетов шумный — как контекст, не KPI).
6. **DORA: Deployment Frequency + Lead Time for Changes** — **[ВНЕ SCOPE — нет CI/CD]**
   средний/высокий для инфры, M.
   На Ultimate: `GET /projects/:id/dora/metrics?metric=deployment_frequency&interval=daily`
   и `metric=lead_time_for_changes` (нужны environments tier `production`, роль Reporter+;
   `lead_time_for_changes` = merge→deploy, не путать с issue Lead Time #5).
   Change Failure Rate / Time to Restore требуют трекинга инцидентов — позже.
   (В новом DORA-каноне MTTR → Failed Deployment Recovery Time; для API 17.6 имена прежние.)

### Чего НЕ мерить
Velocity в story points, индивидуальные метрики, burndown/«процент закрытых»,
абсолюты вместо трендов (для малой выборки шум огромный — показывать динамику
4–8 недель и перцентили).

Реализация: ядро (Throughput, WIP, Work Item Age, Cycle Time p85) через Issues API +
`resource_label_events` + `resource_state_events`. Требует одного соглашения —
единой стартовой метки `workflow::in progress`. DORA отдельным блоком. Объёмы для
6 человек малы, N+1 запросы на события не проблема; кэшировать обработанные closed issue.

---

## Отчёт 3. Лёгкие процессы ведения задач (без тяжёлого скрама)

1. **WIP-лимиты на доске GitLab** — высокий, S. Бьёт по контекст-переключению.
   Отправная точка для маленькой команды — «число людей + 1» (~6–7 в Doing, так
   советуют Businessmap/Planview; Atlassian, наоборот, за лимит **ниже** числа людей —
   калибровать на ретро). GitLab 17.6 поддерживает нативно, но это **Premium+** и лимит
   ставится **per-list** (не на дефолтные Open/Closed). Бот: алерт о превышении WIP /
   персональный лимит «на тебе 4 in-progress».
2. **Definition of Done как чек-лист в шаблоне issue** — высокий, S/M. Убирает
   «полуготовые» задачи. Создавать всей командой, держать видимой. Пример для инфры:
   review/MR approved, CI зелёный, раскатано и проверено, обновлены runbook/доки,
   мониторинг/алерты, нет security-замечаний. Бот: вставлять DoD, нудж при закрытии
   с незакрытыми `[ ]`.
3. **Issue-шаблоны Bug/Task/Incident** — высокий, S. `.gitlab/issue_templates/*.md`.
   Поля bug: заголовок, шаги воспроизведения, ожидаемое/фактическое, окружение,
   логи/severity. Бот: детект «мимо шаблона» (пустое описание/нет шагов) → нудж автору.
4. **Scoped-метки + регулярный триаж** — высокий, M. `priority::1..4`, `severity::`,
   `workflow::` (взаимоисключающие в скоупе — поведение scoped labels, **Premium+**,
   не Ultimate-only). Тип задачи — нативным полем **Type** (Free), не `type::`-меткой.
   Критерии в вики. Триаж = 15-мин async-проход раз в день. Бот: дайджест untriaged issue в Matrix.
5. **Async-standup в Matrix** — **[ВНЕ SCOPE — интерактив отложен]** средний/высокий, S
   (сбор ответов — это inbound, см. #7). Не прерывает deep work (популярная оценка ~23 мин на
   восстановление фокуса — Gloria Mark, оценочно, без рецензируемого первоисточника).
   3 вопроса, ≤5 мин, фикс. время + отдельный канал для блокеров. Бот: тред-приглашение,
   сбор ответов, ремайндеры, дайджест блокеров, черновик апдейта из GitLab-активности.
6. **Борьба со stale-issue** — средний/высокий, M. gitlab-triage gem (YAML-политики
   condition→action по расписанию в CI) или своя логика в боте + дайджест зависших
   в Matrix (персональнее, не засоряет issue). Рекомендация — гибрид: метка `stale` +
   еженедельный дайджест.

Порядок: 1–3 (дни) → 4 и 6 (триаж+stale, нагрузка на бота) → 5 (только если
синхронный дейли мешает фокусу). Калибровать на ретро, не вводить всё сразу.

---

## Источники

**Фичи / ChatOps:**
- https://www.tines.com/blog/chatops-fatigue-how-to-create-alerts-that-matter/
- https://www.datadoghq.com/blog/best-practices-to-prevent-alert-fatigue/
- https://matrix-org.github.io/matrix-hookshot/latest/usage/room_configuration/gitlab_project.html
- https://docs.gitlab.com/user/project/integrations/matrix/
- https://github.com/flexoid/mergentle-reminder
- https://axolo.co/gitlab-slack-integration
- https://github.com/wemake-services/kira-stale
- https://about.gitlab.com/blog/automating-agile-workflows-with-the-gitlab-triage-gem/
- https://docs.slack.dev/messaging/creating-interactive-messages/

**Метрики:**
- https://docs.gitlab.com/api/resource_state_events/
- https://docs.gitlab.com/api/resource_label_events/
- https://docs.gitlab.com/api/issues/
- https://docs.gitlab.com/api/dora/metrics/
- https://docs.gitlab.com/user/analytics/dora_metrics/
- https://docs.gitlab.com/user/group/value_stream_analytics/
- https://dora.dev/guides/dora-metrics/
- https://cloud.google.com/blog/products/devops-sre/using-the-four-keys-to-measure-your-devops-performance
- https://www.prokanban.org/blog/the-kanban-pocket-guide-chapter-6-the-basic-metrics-of-flow
- https://www.scrum.org/resources/blog/4-key-flow-metrics-and-how-use-them-scrums-events
- https://www.55degrees.se/blog/post/what-is-work-item-age
- https://jellyfish.co/blog/goodharts-law-in-software-engineering-and-how-to-avoid-gaming-your-metrics/
- https://getdx.com/blog/space-metrics/

**Процессы:**
- https://businessmap.io/kanban-resources/getting-started/what-is-wip
- https://www.planview.com/resources/articles/benefits-wip-limits/
- https://www.atlassian.com/agile/kanban/wip-limits
- https://dl.acm.org/doi/10.1145/3239235.3239238 (Sjøberg, ESEM'18 — эмпирика WIP-лимитов; относится к #1, не к оценке «23 мин»)
- https://www.atlassian.com/agile/project-management/definition-of-done
- https://plane.so/blog/definition-of-done-dod-checklist-examples-for-agile-teams
- https://www.range.co/blog/asynchronous-daily-standups
- https://docs.gitlab.com/tutorials/issue_triage/
- https://handbook.gitlab.com/handbook/product-development/how-we-work/issue-triage/
- https://gitlab.com/gitlab-org/ruby/gems/gitlab-triage
- https://www.qawolf.com/blog/what-makes-a-great-bug-report
