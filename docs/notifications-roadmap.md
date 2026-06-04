# Уведомления и продуктивность команды — роадмап и ресёрч

Контекст: инфра/devops-команда ~6 человек, self-hosted **GitLab 17.6 Ultimate** +
Matrix/Element. Есть бот-каркас `gitlab-notify` (вебхуки → правила
`событие→шаблон→назначение room/dm`, cron-опрос GitLab API) и `gitlab-expand-tasks`.
Цель — повысить продуктивность и понятность задач, докручивая существующий бот.

Документ собран из трёх веб-ресёрчей (фичи бота / метрики потока / процессы) и
сводит их в приоритизированный план. Полные отчёты с источниками — ниже.

> **Ultimate важно:** DORA-API (`/dora/metrics`), полная Value Stream Analytics и
> **scoped-метки** (`type::`, `priority::`, `workflow::`) доступны — не нужно
> обходить ограничения Free/Premium, о которых предупреждает отчёт по метрикам.

---

## TL;DR — приоритизированный роадмап

Сквозной вывод: главный враг маленькой команды — **notification fatigue**, а не
молчание. Бот уникален тем, что объединяет поток событий + дайджесты + нуджи
(ни hookshot/нативная интеграция, ни дайджест-боты так не умеют). Почти всё
держится на **дисциплине данных**: due_date, стартовая метка `workflow::in progress`,
type/priority метки — от них зависят и напоминания, и метрики.

### Tier 0 — соглашения (предусловие, S, без кода)
- Единая **стартовая метка** `workflow::in progress` (scoped — на Ultimate есть).
- Проставлять **due_date** и **type/priority** метки на задачах.

### Tier 1 — дёшево + высокий эффект (дни)
| Мера | Источник | Бот |
|---|---|---|
| Подавить потоковые эвенты, realtime только actionable (review requested, pipeline failed на protected, mention/assign) | фичи #3 | правило (реконфиг) |
| **Тим-дайджест к стендапу** в комнату (MR ждут ревью, ближайшие дедлайны, untriaged) | фичи #2 + процессы #4 | cron ✅ |
| **Персональный DM-дайджест «что на мне»** — самый высокий ROI | фичи #1 | cron ✅ |
| WIP-лимиты на доске GitLab + DoD-чеклист в шаблоне + issue-шаблоны Bug/Task | процессы #1,2,3 | конфиг GitLab |

### Tier 2 — ядро «не терять задачи» (M)
| Мера | Бот |
|---|---|
| Дедлайны **soft / hard / miss** (DM → эскалация в room) | cron ✅ (уже есть `due_soon`/`overdue`) |
| **Stale**-дайджест (MR/issue без активности N дней) | cron ✅ |
| **Нудж-триаж**: дайджест untriaged issue | cron ✅ |
| Нудж по гигиене (без assignee/дедлайна/лейбла) | cron ✅ |
| Обвязка: дедуп/снуз/анти-флаппинг + тихие часы (TZ) | правило ✅ |

### Tier 3 — аналитика и интерактив (M–L)
- **Еженедельный flow-отчёт** в комнату: Throughput, WIP, Work Item Age (топ
  застрявших), Cycle Time **p85**. На Ultimate можно через Value Stream Analytics
  / DORA-API, либо тариф-независимо через Issues API + `resource_label_events` +
  `resource_state_events`. **Только командные метрики, не индивидуальные.**
- **DORA** (`/dora/metrics`): Deployment Frequency + Lead Time for Changes —
  для инфры уместно (нужны environments с tier `production`).
- **Ack/assign из чата** (reactions-as-actions) — дороже всего, последним.

### Чего НЕ делать
- Слать каждый push/comment в канал (fatigue).
- **Индивидуальные метрики** (закрытий на человека, строки кода) — яд для доверия
  и гейминг (закон Гудхарта); DORA и SPACE прямо против.
- Vanity: velocity в story points, burndown, абсолюты вместо трендов.
- Внедрять всё сразу — калибровать на ретро.

---

## Отчёт 1. Уведомления / ChatOps-бот GitLab→Matrix

### Принципы маршрутизации
- **Канал-vs-личка**: командно-значимое — в room; «это твоя задача/твой просрочен» — в DM.
- **Батчинг по умолчанию**: некритичное собирать в окно (день/час) и слать одной сводкой.
- **Реалтайм — только срочное и actionable**: review requested, pipeline failed на
  защищённой ветке, прямое упоминание. Остальное — в дайджест.
- **Respect work hours**: дайджесты/нуджи утром в рабочие часы по TZ получателя.
- **Off-ramp**: у каждого нудж/алерта — возможность «не актуально/сделано».

Существующие решения (matrix-hookshot, нативная интеграция) умеют только
потоковую трансляцию с фильтром по лейблам — нет дайджестов, cron-дедлайнов,
ack/assign, нудж-механик. Дайджест-боты (mergentle-reminder, Axolo) — наоборот,
только сводки. Гигиену делает gitlab-triage, но внутри GitLab, не в Matrix.
Ваш бот объединяет все три класса — в этом его ценность.

### Приоритизированные фичи
1. **Персональный утренний DM-дайджест «что на мне»** — эффект высокий, S/M, cron ✅.
   Назначенные issue/MR, MR ждущие моего ревью, мои близкие/просроченные дедлайны.
   Один батч вместо потока пингов. Самый высокий ROI.
2. **Утренний тим-дайджест в room** — высокий, S, cron ✅. MR ждущие ревью
   (автор/возраст/аппрувы), MR без ревьюера, ближайшие дедлайны.
3. **Подавление потоковых эвентов** — высокий, S, правило. Realtime только для
   review-requested, pipeline-failed (protected), mention/assign.
4. **Stale/no-activity алерт по MR** — высокий, M, cron ✅. Нет активности N дней →
   DM автору+ревьюеру; эскалация (3+ дней) — упоминание в room.
5. **Нудж по гигиене issue** (без assignee/дедлайна/лейбла) — средний, M, cron ✅.
   Батчем раз в день, идемпотентно, с дедупом.
6. **Дедлайны soft/hard/miss** — высокий, M, cron ✅. `deadline::soft` (за N дней — DM),
   `deadline::hard` (в день — DM+room), `deadline::miss` (просрочено — эскалация).
7. **Ack/assign из чата** — средний, L, частично. Reactions-as-actions / интерактив +
   колбэк в GitLab API. Высокая ценность, дороже всего — после 1–6.
8. **Дедуп/снуз/анти-флаппинг** — средний, S, правило. Не повторять нудж чаще раза
   в день, snooze до даты, группировка, не слать на draft-MR.
9. **Эскалация по severity/возрасту** — средний, S. Тихо в DM → эскалация в room.
10. **Тихие часы / TZ-aware** — низкий-средний, S.

Порядок: #2+#1 (дайджесты), #3 (подавление) → #6+#4 (дедлайны/stale) с #8 как
обвязкой → #5 (гигиена) → #7 (интерактив) последним.

---

## Отчёт 2. Метрики потока и их извлечение из GitLab

Для команды из ~6 — только **метрики потока на уровне команды**, не индивидуальные
(DORA и SPACE: индивидуальные метрики «самые опасные в изоляции», ломают
коллаборацию, провоцируют гейминг — закон Гудхарта). Отчёт в Matrix подавать как
«термометр для ретро», не KPI.

> На **Ultimate** доступны DORA-API и полная VSA. Тем не менее ядро удобно строить
> на сырых событиях Issues API — оно прозрачнее и не зависит от настройки VSA-стадий.

### Метрики
1. **Throughput** — высокий, S. Закрыто issue за неделю (штуки, не story points;
   тренд важнее абсолюта). `GET /projects/:id/issues?state=closed&updated_after=…`,
   фильтр по `closed_at`. Не делать таргетом (иначе дробление задач).
2. **WIP** — высокий, S. Открытые с меткой `workflow::in progress`. Лучший leading-
   индикатор (закон Литтла: меньше WIP → короче cycle time). Потолок для шестерых — единицы.
3. **Work Item Age** — высокий, M. Возраст *открытых* задач от старта. «Самая важная»
   (ProKanban) — по ней ещё можно действовать. Старт = `resource_label_events`
   (`action:add` стартовой метки). Подсветка топ-3 застрявших в отчёт.
4. **Cycle Time** — высокий, M. По закрытым: от старта (label event) до закрытия
   (`resource_state_events`, `state:closed`). **Перцентиль 85, не среднее.**
   Упрощённо (S): `closed_at − created_at`.
5. **Lead Time** — средний, S. `closed_at − created_at` (включает лежание в бэклоге;
   для реактивных тикетов шумный — как контекст, не KPI).
6. **DORA: Deployment Frequency + Change Lead Time** — средний/высокий для инфры, M.
   На Ultimate: `GET /projects/:id/dora/metrics?metric=deployment_frequency&interval=daily`
   и `metric=lead_time_for_changes` (нужны environments tier `production`, роль Reporter+).
   Change Failure Rate / Time to Restore требуют трекинга инцидентов — позже.

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
   Отправная точка для маленькой команды — «число людей + 1» (~6–7 в Doing),
   калибровать на ретро. GitLab 17.6 поддерживает нативно. Бот: алерт о превышении
   WIP / персональный лимит «на тебе 4 in-progress».
2. **Definition of Done как чек-лист в шаблоне issue** — высокий, S/M. Убирает
   «полуготовые» задачи. Создавать всей командой, держать видимой. Пример для инфры:
   review/MR approved, CI зелёный, раскатано и проверено, обновлены runbook/доки,
   мониторинг/алерты, нет security-замечаний. Бот: вставлять DoD, нудж при закрытии
   с незакрытыми `[ ]`.
3. **Issue-шаблоны Bug/Task/Incident** — высокий, S. `.gitlab/issue_templates/*.md`.
   Поля bug: заголовок, шаги воспроизведения, ожидаемое/фактическое, окружение,
   логи/severity. Бот: детект «мимо шаблона» (пустое описание/нет шагов) → нудж автору.
4. **Scoped-метки + регулярный триаж** — высокий, M. `type::`, `priority::1..4`,
   `severity::`, `workflow::` (на Ultimate взаимоисключающие в скоупе). Критерии в вики.
   Триаж = 15-мин async-проход раз в день. Бот: дайджест untriaged issue в Matrix.
5. **Async-standup в Matrix** — средний/высокий, S. Не прерывает deep work (~23 мин
   на восстановление фокуса после прерывания). 3 вопроса, ≤5 мин, фикс. время +
   отдельный канал для блокеров. Бот: тред-приглашение, сбор ответов, ремайндеры,
   дайджест блокеров, черновик апдейта из GitLab-активности.
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
- https://dl.acm.org/doi/10.1145/3239235.3239238
- https://www.atlassian.com/agile/project-management/definition-of-done
- https://plane.so/blog/definition-of-done-dod-checklist-examples-for-agile-teams
- https://www.range.co/blog/asynchronous-daily-standups
- https://docs.gitlab.com/tutorials/issue_triage/
- https://handbook.gitlab.com/handbook/product-development/how-we-work/issue-triage/
- https://gitlab.com/gitlab-org/ruby/gems/gitlab-triage
- https://www.qawolf.com/blog/what-makes-a-great-bug-report
