import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import ReactFlow, {
  Background,
  ConnectionMode,
  MarkerType,
  MiniMap,
  Panel,
  SelectionMode,
  useEdgesState,
  useNodesState,
  useReactFlow,
  useViewport,
  type Connection,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from "reactflow";
import { api } from "./api";
import { nodeAccent, readableText } from "./colors";
import { IssueNode } from "./IssueNode";
import { BoardView } from "./Board";
import { Markdown } from "./Markdown";
import {
  connectedComponentsLayout,
  radialLayout,
  columnLayout,
  columnHeaders,
  NODE_W,
  NODE_H,
  COL_W,
  COLLAPSED_W,
  type Column,
  type GraphLayout,
} from "./layout";
import type {
  Assignee,
  Board,
  BulkPatchBody,
  Capabilities,
  GraphData,
  IssueDetail,
  Label,
  Namespaces,
  Scope,
  ScopeMeta,
} from "./types";

const nodeTypes = { issue: IssueNode };

// ── Этап 9 (D4): вкладка «Связанные» — тип связи относительно выбранной ноды ──
type RelatedKind = "blocks" | "blocked" | "parent" | "child" | "relates" | "epic";
type RelatedItem = { node: GraphData["nodes"][number]; kind: RelatedKind };
// иконка + человекочитаемая метка для каждого вида связи
const RELATED_KIND_LABEL: Record<RelatedKind, { icon: string; label: string }> = {
  blocks: { icon: "⛔", label: "Блокирует" },
  blocked: { icon: "⛔", label: "Блокируется" },
  parent: { icon: "↳", label: "Родитель" },
  child: { icon: "↳", label: "Дочерняя" },
  relates: { icon: "🔗", label: "Связана" },
  epic: { icon: "◆", label: "Эпик" },
};
// порядок групп в списке: блокировки → иерархия → связи → эпик
const RELATED_ORDER: Record<RelatedKind, number> = {
  blocks: 0,
  blocked: 1,
  parent: 2,
  child: 3,
  relates: 4,
  epic: 5,
};

// ── Этап 5: сохранение вида (scope/группировка/фильтры) в URL + localStorage ──
// URL — приоритетный источник (можно поделиться ссылкой на отфильтрованный вид);
// localStorage — фолбэк между сессиями. Читаем при bootstrap ДО дефолта, чтобы
// открыть последний/расшаренный вид, а не навязанную первую группу.
type ViewState = {
  scope?: { kind: "group" | "project"; id: string };
  groupBy?: string;
  loadState?: "opened" | "all";
  mine?: boolean;
  search?: string;
  fState?: string;
  fMs?: string;
  fLabel?: string;
  fAssignee?: string;
  days?: number; // D2: «активные за N дней» (0 = выключено)
};

const VIEW_LS_KEY = "ig.view";

function readSavedView(): ViewState {
  // URL имеет приоритет над localStorage (расшаренная ссылка важнее истории)
  const out: ViewState = {};
  try {
    const raw = localStorage.getItem(VIEW_LS_KEY);
    if (raw) Object.assign(out, JSON.parse(raw) as ViewState);
  } catch {
    /* битый localStorage — игнорируем */
  }
  const p = new URLSearchParams(window.location.search);
  const sc = p.get("scope"); // формат "group:123" / "project:456"
  if (sc && (sc.startsWith("group:") || sc.startsWith("project:"))) {
    const [kind, ...rest] = sc.split(":");
    out.scope = { kind: kind as "group" | "project", id: rest.join(":") };
  }
  if (p.get("view")) out.groupBy = p.get("view") || undefined;
  if (p.get("state") === "all" || p.get("state") === "opened")
    out.loadState = p.get("state") as "opened" | "all";
  if (p.has("mine")) out.mine = p.get("mine") === "1";
  if (p.get("q")) out.search = p.get("q") || undefined;
  if (p.get("fstate")) out.fState = p.get("fstate") || undefined;
  if (p.get("fms")) out.fMs = p.get("fms") || undefined;
  if (p.get("flabel")) out.fLabel = p.get("flabel") || undefined;
  if (p.get("fassignee")) out.fAssignee = p.get("fassignee") || undefined;
  if (p.get("days")) out.days = Number(p.get("days")) || undefined;
  return out;
}

// единожды считанный стартовый вид (URL+LS) — для инициализаторов state
const SAVED_VIEW = readSavedView();

function edgeStyle(type: string, directed?: boolean): Partial<Edge> {
  if (type === "blocks")
    return {
      animated: true,
      style: { stroke: "#d9534f", strokeWidth: 2 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#d9534f" },
      label: "blocks",
    };
  if (type === "epic" || type === "parent")
    return {
      style: { stroke: "#7c3aed", strokeWidth: 2.5 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#7c3aed" },
      label: type === "parent" ? "parent → child" : "epic",
    };
  // relates_to: пунктир; если есть наше направление — со стрелкой
  return {
    style: { stroke: "#7a8198", strokeWidth: 2, strokeDasharray: "6 4" },
    ...(directed
      ? { markerEnd: { type: MarkerType.ArrowClosed, color: "#7a8198" } }
      : {}),
  };
}

export default function App() {
  const [namespaces, setNamespaces] = useState<Namespaces | null>(null);
  const [scope, setScope] = useState<Scope | null>(null);
  const [data, setData] = useState<GraphData | null>(null);
  const [meta, setMeta] = useState<ScopeMeta | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // пресеты группировки: none = граф зависимостей (dagre), остальное = колонки.
  // Дефолт (Этап 10) — none: граф зависимостей читаем (connectedComponentsLayout
  // даёт сетку+деревья). Выбор пользователя помним в localStorage.
  const [groupBy, setGroupBy] = useState<
    "none" | "milestone" | "label" | "assignee" | "project" | "state"
  >(() => {
    // Этап 5: приоритет URL/LS-вида, затем старый ig.groupBy, затем дефолт.
    // F2: дефолт сменён "state" → "none" (граф зависимостей читаем: сетка+деревья).
    // Сохранённый выбор пользователя (URL/LS) по-прежнему уважаем — не перетираем.
    const saved = SAVED_VIEW.groupBy ?? localStorage.getItem("ig.groupBy");
    const allowed = ["none", "milestone", "label", "assignee", "project", "state"];
    return (allowed.includes(saved ?? "") ? saved : "none") as
      | "none"
      | "milestone"
      | "label"
      | "assignee"
      | "project"
      | "state";
  });
  // геометрия графа зависимостей (когда вид = none)
  const [graphLayout, setGraphLayout] = useState<GraphLayout>("td");
  // Этап 12 (D8): режим «Доска» (GitLab-style) — вместо ReactFlow-холста.
  // Помним выбор в localStorage. Импортированные доски области грузим отдельно.
  const [boardMode, setBoardMode] = useState<boolean>(
    () => localStorage.getItem("ig.boardMode") === "1"
  );
  useEffect(() => {
    localStorage.setItem("ig.boardMode", boardMode ? "1" : "0");
  }, [boardMode]);
  const [boardsData, setBoardsData] = useState<Board[]>([]);
  // Состояние загрузки досок (fetch /api/boards). Нужно, чтобы BoardView не
  // показывал фолбэк «доски не найдены», пока ответ ещё в пути (он приходит
  // позже тяжёлого графа state=all). См. эффект ниже.
  const [boardsLoading, setBoardsLoading] = useState(false);
  // Этап 8 (Kanban): свёрнутые колонки (по ключу) — их ноды скрыты, колонка узкая.
  // Сбрасываем при смене группировки (ключи привязаны к измерению).
  const [collapsedCols, setCollapsedCols] = useState<Set<string>>(new Set());
  // Этап 8: вертикальная сортировка внутри колонки. Помним выбор в localStorage.
  const [colSort, setColSort] = useState<
    "none" | "due" | "weight" | "iid" | "title"
  >(() => {
    const s = localStorage.getItem("ig.colsort");
    return (["none", "due", "weight", "iid", "title"].includes(s ?? "")
      ? s
      : "none") as "none" | "due" | "weight" | "iid" | "title";
  });
  useEffect(() => {
    localStorage.setItem("ig.colsort", colSort);
  }, [colSort]);
  // Этап 8: WIP-лимит на колонку (0 = выкл). Превышение подсвечивается в шапке.
  const [wipLimit, setWipLimit] = useState<number>(() => {
    const n = Number(localStorage.getItem("ig.wip") || "0");
    return Number.isFinite(n) && n > 0 ? n : 0;
  });
  useEffect(() => {
    localStorage.setItem("ig.wip", String(wipLimit));
  }, [wipLimit]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Этап 7: инструмент холста (левая панель). select — выбор/перетаскивание,
  // pan — рука (панорама), zoomArea — лупа (рамкой → зум), lasso — выделение пачкой.
  const [tool, setTool] = useState<"select" | "pan" | "zoomArea" | "lasso">(
    "select"
  );
  // Этап 7: миникарта — выключаемая (на больших графах мешает). По умолчанию вкл.
  const [showMiniMap, setShowMiniMap] = useState<boolean>(
    () => localStorage.getItem("ig.minimap") !== "0"
  );
  useEffect(() => {
    localStorage.setItem("ig.minimap", showMiniMap ? "1" : "0");
  }, [showMiniMap]);
  // Этап 7: выбранные лассо ноды (для плавающей панели массовых действий).
  // Канал — onSelectionChange ReactFlow; single-select (selectedId) живёт отдельно.
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [bulkBusy, setBulkBusy] = useState(false);
  // Этап 6 (паритет): детали выбранной задачи (description/weight/time_stats/MR),
  // подгружаются отдельным запросом при клике на ноду. null — ещё не загружено.
  const [detail, setDetail] = useState<IssueDetail | null>(null);
  // D3: новая карточка — навести камеру ПОСЛЕ раскладки + кратко подсветить (pulse)
  const [pendingFocusId, setPendingFocusId] = useState<string | null>(null);
  const [pulseId, setPulseId] = useState<string | null>(null);
  // режим «связать с…»: исходная нода + строка поиска кандидатов
  const [linkingFrom, setLinkingFrom] = useState<string | null>(null);
  const [linkQuery, setLinkQuery] = useState("");
  // поповер выбора типа после броска связи (source→target)
  const [pendingLink, setPendingLink] = useState<{ source: string; target: string } | null>(null);
  // меню на ребре (смена типа / удаление)
  const [edgeMenu, setEdgeMenu] = useState<{
    id: string; source: string; target: string; link_id?: number; x: number; y: number;
  } | null>(null);

  // D1: что грузить с бэка — только открытые (дефолт) или все. Тумблер догружает
  // closed; помним выбор. Это НЕ клиентский фильтр fState, а параметр запроса.
  const [loadState, setLoadState] = useState<"opened" | "all">(() => {
    if (SAVED_VIEW.loadState) return SAVED_VIEW.loadState;
    return localStorage.getItem("ig.loadState") === "all" ? "all" : "opened";
  });
  useEffect(() => {
    localStorage.setItem("ig.loadState", loadState);
  }, [loadState]);

  // Этап 5: «Мои задачи» — стартовый вид по умолчанию (assignee=me на бэке).
  // По умолчанию включён (самый частый первый запрос); сохранённый вид/URL и
  // PAT-фолбэк (нет username) переопределяют. Решение о фактическом значении с
  // учётом логина принимаем в bootstrap, здесь — лишь стартовое из URL/LS.
  const [myTasks, setMyTasks] = useState<boolean>(
    () => SAVED_VIEW.mine ?? true
  );
  // D2: пресет «активные за N дней» по updated_at — видимая плашка, НЕ молчаливый
  // дефолт. 0 = выключено. Сохраняем в URL/LS вместе с прочим видом.
  const [daysActive, setDaysActive] = useState<number>(
    () => SAVED_VIEW.days ?? 0
  );
  // Закрытые пользователем плашки над холстом (× на баннере). Ключ привязан к
  // области (overload:group:2 / mine:project:5) — закрыл для graphlab, в другой
  // (тоже перегруженной) области плашка покажется снова. Помним между сессиями.
  const [dismissed, setDismissed] = useState<Set<string>>(() => {
    try {
      return new Set<string>(
        JSON.parse(localStorage.getItem("ig.dismissed") || "[]")
      );
    } catch {
      return new Set<string>();
    }
  });
  const dismissBanner = useCallback((key: string) => {
    setDismissed((prev) => {
      if (prev.has(key)) return prev;
      const next = new Set(prev);
      next.add(key);
      try {
        localStorage.setItem("ig.dismissed", JSON.stringify([...next]));
      } catch {
        /* приватный режим / переполнение — не критично */
      }
      return next;
    });
  }, []);

  // фильтры (Фаза 2) — стартовые значения берём из сохранённого вида (URL/LS)
  const [search, setSearch] = useState(SAVED_VIEW.search ?? "");
  // дебаунс поиска ~250 мс: ввод символа не запускает релейаут на каждый кейстрок
  const [searchDebounced, setSearchDebounced] = useState(SAVED_VIEW.search ?? "");
  useEffect(() => {
    const t = setTimeout(() => setSearchDebounced(search), 250);
    return () => clearTimeout(t);
  }, [search]);
  const [fState, setFState] = useState(SAVED_VIEW.fState ?? "all");
  const [fMs, setFMs] = useState(SAVED_VIEW.fMs ?? "all");
  const [fLabel, setFLabel] = useState(SAVED_VIEW.fLabel ?? "all");
  const [fAssignee, setFAssignee] = useState(SAVED_VIEW.fAssignee ?? "all");
  const [fDateField, setFDateField] = useState<"due_date" | "created_at" | "updated_at">(
    "due_date"
  );
  const [fFrom, setFFrom] = useState(""); // YYYY-MM-DD
  const [fTo, setFTo] = useState("");

  const [creating, setCreating] = useState(false);
  const [parentNode, setParentNode] = useState<GraphData["nodes"][number] | null>(
    null
  );
  const [toast, setToast] = useState<string | null>(null);
  const [newSprint, setNewSprint] = useState<string | null>(null); // null = поле скрыто
  const [showHelp, setShowHelp] = useState(false);
  const [botConfigured, setBotConfigured] = useState(false);
  // вход: настроен ли инстанс админом, доступен ли OAuth, вошёл ли пользователь
  const [loggedIn, setLoggedIn] = useState(false);
  const [user, setUser] = useState<{
    name: string | null;
    username: string | null;
    avatar_url: string | null;
  } | null>(null);
  const [instanceReady, setInstanceReady] = useState(true);
  const [oauthAvailable, setOauthAvailable] = useState(false);
  const [adminEnabled, setAdminEnabled] = useState(false);
  const [appEnabled, setAppEnabled] = useState(true);
  const [filtersOpen, setFiltersOpen] = useState(false);
  // что делать с задачами вне фильтра: скрывать или затемнять
  const [filterMode, setFilterMode] = useState<"hide" | "dim">(
    () => (localStorage.getItem("ig.filterMode") === "dim" ? "dim" : "hide")
  );
  const [sidebarOpen, setSidebarOpen] = useState(
    () => localStorage.getItem("ig.sidebarOpen") !== "0"
  );
  const [sidebarW, setSidebarW] = useState<number>(() =>
    Number(localStorage.getItem("ig.sidebarW")) || 360
  );
  const [theme, setTheme] = useState<"light" | "dark">(
    () => (localStorage.getItem("ig.theme") === "dark" ? "dark" : "light")
  );
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("ig.theme", theme);
  }, [theme]);

  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  // инстанс ReactFlow (App не внутри провайдера) — ловим через onInit,
  // нужен для setCenter по результату поиска
  const rfRef = useRef<ReactFlowInstance | null>(null);
  // Этап 7: враппер холста — для лупы (screen-точки рамки относительно него) и
  // как контейнер нижнего левого дока (переключатель режима + раскладка).
  const canvasRef = useRef<HTMLDivElement | null>(null);

  // сохранённые вручную позиции нод (по области), переживают перезагрузку
  const pinnedRef = useRef<Record<string, { x: number; y: number }>>({});
  const [pinnedTick, setPinnedTick] = useState(0);
  const posKey = scope ? `ig.pos.${scope.kind}:${scope.id}` : null;
  const [hasPins, setHasPins] = useState(false);

  // загрузка сохранённых позиций при смене области
  useEffect(() => {
    let saved: Record<string, { x: number; y: number }> = {};
    if (posKey) {
      try {
        saved = JSON.parse(localStorage.getItem(posKey) || "{}");
      } catch {
        saved = {};
      }
    }
    pinnedRef.current = saved;
    setHasPins(Object.keys(saved).length > 0);
    setPinnedTick((t) => t + 1);
  }, [posKey]);

  // запоминаем позицию в начале перетаскивания — чтобы отличить клик от drag
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);
  const onNodeDragStart = useCallback((_e: unknown, node: Node) => {
    dragStartRef.current = { ...node.position };
  }, []);

  // перетащил ноду → сохраняем ручную позицию (pinned), только если реально
  // сдвинул (не клик). Этап 12 (D11): drag на холсте больше НЕ меняет данные —
  // и в графе, и в колоночных видах это просто ручное позиционирование (просмотр).
  // Вся смена данных задач переехала в отдельный режим «Доска».
  const onNodeDragStop = useCallback(
    (_e: unknown, node: Node) => {
      const s = dragStartRef.current;
      if (
        s &&
        Math.abs(s.x - node.position.x) < 3 &&
        Math.abs(s.y - node.position.y) < 3
      )
        return; // это был клик, а не перетаскивание
      pinnedRef.current[node.id] = node.position;
      if (posKey) {
        localStorage.setItem(posKey, JSON.stringify(pinnedRef.current));
        setHasPins(true);
      }
    },
    [posKey]
  );

  const resetLayout = () => {
    pinnedRef.current = {};
    if (posKey) localStorage.removeItem(posKey);
    setHasPins(false);
    setPinnedTick((t) => t + 1);
  };

  // начальная загрузка (и повторно — после входа)
  const bootstrap = useCallback(async () => {
    try {
      const cfg = await api.getConfig();
      setAppEnabled(cfg.app_enabled);
      setInstanceReady(cfg.instance_configured);
      setOauthAvailable(cfg.oauth_available);
      setAdminEnabled(cfg.admin_enabled);
      setBotConfigured(cfg.bot_available);
      setLoggedIn(cfg.configured);
      setUser(cfg.user);
      if (!cfg.configured) return; // покажется экран входа (LoginGate)
      const [ns, cp] = await Promise.all([
        api.namespaces(),
        api.capabilities(),
      ]);
      setNamespaces(ns);
      setCaps(cp);
      setError(null);

      // Этап 5: PAT-фолбэк — без username «Мои задачи» бессмысленны (бэк не
      // сможет резолвить "me"), переключаемся на обычный вид.
      if (!cfg.user?.username) setMyTasks(false);

      // Этап 5: восстановить сохранённый scope (URL/LS), если он ещё доступен;
      // иначе — прежний дефолт (graphlab → первая группа → первый проект).
      const findSaved = (): Scope | null => {
        const sv = SAVED_VIEW.scope;
        if (!sv) return null;
        if (sv.kind === "group") {
          const g = ns.groups.find((x) => String(x.id) === sv.id);
          return g
            ? { kind: "group", id: String(g.id), label: g.full_path }
            : null;
        }
        const pr = ns.projects.find((x) => String(x.id) === sv.id);
        return pr
          ? { kind: "project", id: String(pr.id), label: pr.full_path }
          : null;
      };
      const saved = findSaved();
      const def =
        ns.groups.find((g) => g.full_path === "graphlab") ?? ns.groups[0];
      if (saved) setScope(saved);
      else if (def)
        setScope({ kind: "group", id: String(def.id), label: def.full_path });
      else if (ns.projects[0])
        setScope({
          kind: "project",
          id: String(ns.projects[0].id),
          label: ns.projects[0].full_path,
        });
      else setScope(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  // возврат с OAuth-редиректа: сессия уже в cookie, просто показать результат
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("oauth") === "ok") {
      setToast("Вход через GitLab выполнен");
      bootstrap();
    } else if (params.get("oauth_error"))
      setToast("Ошибка OAuth: " + params.get("oauth_error"));
    if (params.has("oauth") || params.has("oauth_error")) {
      // Этап 5: чистим только oauth-параметры, сохраняя расшаренный вид (scope/…)
      params.delete("oauth");
      params.delete("oauth_error");
      const qs = params.toString();
      window.history.replaceState(
        {},
        "",
        window.location.pathname + (qs ? `?${qs}` : "")
      );
    }
  }, [bootstrap]);

  useEffect(() => {
    localStorage.setItem("ig.sidebarW", String(sidebarW));
  }, [sidebarW]);
  useEffect(() => {
    localStorage.setItem("ig.sidebarOpen", sidebarOpen ? "1" : "0");
  }, [sidebarOpen]);
  useEffect(() => {
    localStorage.setItem("ig.filterMode", filterMode);
  }, [filterMode]);
  useEffect(() => {
    localStorage.setItem("ig.groupBy", groupBy);
  }, [groupBy]);
  // Этап 8: свёрнутые колонки привязаны к измерению группировки — при смене вида
  // (и области) ключи теряют смысл, сбрасываем, чтобы ничего не «застряло».
  useEffect(() => {
    setCollapsedCols(new Set());
  }, [groupBy, scope]);

  // Этап 5: сохранение вида (scope/группировка/фильтры) в URL + localStorage.
  // Пишем только когда scope известен (после bootstrap), чтобы не затереть
  // расшаренные параметры до их применения. URL — для ссылок; LS — между сессиями.
  useEffect(() => {
    if (!scope) return;
    const view: ViewState = {
      scope: { kind: scope.kind, id: scope.id },
      groupBy,
      loadState,
      mine: myTasks,
      search: search.trim() || undefined,
      fState: fState !== "all" ? fState : undefined,
      fMs: fMs !== "all" ? fMs : undefined,
      fLabel: fLabel !== "all" ? fLabel : undefined,
      fAssignee: fAssignee !== "all" ? fAssignee : undefined,
      days: daysActive || undefined,
    };
    try {
      localStorage.setItem(VIEW_LS_KEY, JSON.stringify(view));
    } catch {
      /* приватный режим / переполнение — не критично */
    }
    const p = new URLSearchParams(window.location.search);
    p.set("scope", `${scope.kind}:${scope.id}`);
    p.set("view", groupBy);
    p.set("state", loadState);
    p.set("mine", myTasks ? "1" : "0");
    const setOrDel = (k: string, v: string | undefined) =>
      v ? p.set(k, v) : p.delete(k);
    setOrDel("q", view.search);
    setOrDel("fstate", view.fState);
    setOrDel("fms", view.fMs);
    setOrDel("flabel", view.fLabel);
    setOrDel("fassignee", view.fAssignee);
    setOrDel("days", view.days ? String(view.days) : undefined);
    const qs = p.toString();
    window.history.replaceState(
      {},
      "",
      window.location.pathname + (qs ? `?${qs}` : "")
    );
  }, [
    scope,
    groupBy,
    loadState,
    myTasks,
    search,
    fState,
    fMs,
    fLabel,
    fAssignee,
    daysActive,
  ]);

  const activeFilters =
    (search.trim() ? 1 : 0) +
    (fState !== "all" ? 1 : 0) +
    (fMs !== "all" ? 1 : 0) +
    (fLabel !== "all" ? 1 : 0) +
    (fAssignee !== "all" ? 1 : 0) +
    (fFrom || fTo ? 1 : 0);
  const resetFilters = () => {
    setSearch("");
    setFState("all");
    setFMs("all");
    setFLabel("all");
    setFAssignee("all");
    setFFrom("");
    setFTo("");
  };

  const loadGraph = useCallback(
    async (
      s: Scope,
      fresh = false,
      ls: "opened" | "all" = "opened",
      mine = false
    ) => {
      setLoading(true);
      setError(null);
      try {
        const [g, m] = await Promise.all([
          // Этап 5: «Мои задачи» → assignee=me на бэке (N+1 лишь по своим задачам)
          api.graph(s, fresh, ls, mine ? "me" : undefined),
          api.scopeMeta(s),
        ]);
        setData(g);
        setMeta(m);
      } catch (e) {
        setError(String(e));
      } finally {
        setLoading(false);
      }
    },
    []
  );

  // Этап 12 (D9): доске нужны ВСЕ состояния (колонка Closed). В boardMode грузим
  // граф со state=all независимо от тумблера «открытые/все» (он для холста).
  const effLoadState: "opened" | "all" = boardMode ? "all" : loadState;
  useEffect(() => {
    if (scope) loadGraph(scope, false, effLoadState, myTasks);
  }, [scope, effLoadState, myTasks, loadGraph]);

  // Этап 12 (D7): импорт досок области из GitLab для режима «Доска».
  // Грузим лениво — только когда режим включён и есть scope. Ошибка/нет досок →
  // пустой список (BoardView деградирует к ручным Open|Closed колонкам).
  useEffect(() => {
    if (!boardMode || !scope) return;
    let alive = true;
    setBoardsLoading(true);
    api
      .boards(scope)
      .then((r) => {
        if (alive) setBoardsData(r.boards);
      })
      .catch(() => {
        if (alive) setBoardsData([]);
      })
      .finally(() => {
        if (alive) setBoardsLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [boardMode, scope]);

  // F1: если «мои задачи» резолвятся в 0 нод (а режим включён) — авто-переключить
  // на «показать все» + неблокирующий тост. Защита от цикла: делаем это лишь раз
  // (autoAllRef), чтобы не зациклиться, если и «все» окажутся пустыми.
  const autoAllRef = useRef(false);
  useEffect(() => {
    if (loading || !data) return; // ждём завершения загрузки
    if (myTasks && data.nodes.length === 0 && !autoAllRef.current) {
      autoAllRef.current = true;
      setMyTasks(false); // перезагрузит граф (эффект выше по myTasks)
      setToast("Вам ничего не назначено — показаны все задачи");
    }
  }, [data, loading, myTasks]);

  // ── ФИЛЬТР: какие ноды вообще показывать (несоответствующие скрываются) ──
  // D2: «активные за N дней» по updated_at — нижняя граница даты обновления.
  const daysCutoff = useMemo(() => {
    if (!daysActive) return null;
    const d = new Date();
    d.setDate(d.getDate() - daysActive);
    return d.toISOString().slice(0, 10);
  }, [daysActive]);
  const visibleIds = useMemo(() => {
    const out = new Set<string>();
    const q = searchDebounced.trim().toLowerCase();
    for (const n of data?.nodes ?? []) {
      if (q && !n.title.toLowerCase().includes(q) && !`#${n.iid}`.includes(q))
        continue;
      // D2: только обновлённые за N дней (по updated_at; задачи без даты прячем)
      if (daysCutoff) {
        const u = n.updated_at ? n.updated_at.slice(0, 10) : null;
        if (!u || u < daysCutoff) continue;
      }
      if (fState !== "all" && n.state !== fState) continue;
      if (fMs !== "all") {
        if (fMs === "none" && n.milestone) continue;
        if (fMs !== "none" && String(n.milestone?.id) !== fMs) continue;
      }
      if (fLabel !== "all" && !n.labels.some((l) => l.name === fLabel)) continue;
      if (fAssignee !== "all" && !n.assignees.some((a) => a.username === fAssignee))
        continue;
      if (fFrom || fTo) {
        const raw = n[fDateField];
        const d = raw ? raw.slice(0, 10) : null;
        if (!d) continue; // нет даты в выбранном поле — прячем при активном фильтре
        if (fFrom && d < fFrom) continue;
        if (fTo && d > fTo) continue;
      }
      out.add(n.id);
    }
    return out;
  }, [data, searchDebounced, fState, fMs, fLabel, fAssignee, fFrom, fTo, fDateField, daysCutoff]);

  // колонки + функция отнесения ноды к колонке для выбранной группировки
  const grouping = useMemo<{ columns: Column[]; keyOf: (n: Node) => string } | null>(() => {
    if (groupBy === "none" || !data) return null;
    const d = (n: Node) => n.data as GraphData["nodes"][number];
    if (groupBy === "milestone") {
      const ms = meta?.milestones ?? data.meta.milestones;
      return {
        columns: [
          ...ms.map((m) => ({ key: String(m.id), title: m.title })),
          { key: "none", title: "Без спринта" },
        ],
        keyOf: (n) => String(d(n).milestone?.id ?? "none"),
      };
    }
    if (groupBy === "label") {
      const labels = data.meta.labels;
      return {
        columns: [
          ...labels.map((l) => ({ key: l.name, title: l.name })),
          { key: "none", title: "Без метки" },
        ],
        keyOf: (n) => d(n).labels[0]?.name ?? "none",
      };
    }
    if (groupBy === "assignee") {
      const mem = meta?.members ?? [];
      return {
        columns: [
          ...mem.map((u) => ({ key: u.username, title: u.name })),
          { key: "none", title: "Не назначено" },
        ],
        keyOf: (n) => d(n).assignees[0]?.username ?? "none",
      };
    }
    if (groupBy === "project") {
      const projs = meta?.projects ?? [];
      return {
        columns: [
          ...projs.map((p) => ({ key: String(p.id), title: p.name })),
          { key: "none", title: "Прочее" },
        ],
        keyOf: (n) => String(d(n).project_id ?? "none"),
      };
    }
    // state
    return {
      columns: [
        { key: "opened", title: "Открытые" },
        { key: "closed", title: "Закрытые" },
      ],
      keyOf: (n) => d(n).state,
    };
  }, [groupBy, data, meta]);

  // соседи каждой ноды (для фокус-режима и затемнения при выделении)
  const neighborhood = useMemo(() => {
    const adj = new Map<string, Set<string>>();
    for (const e of data?.edges ?? []) {
      (adj.get(e.source) ?? adj.set(e.source, new Set()).get(e.source)!).add(
        e.target
      );
      (adj.get(e.target) ?? adj.set(e.target, new Set()).get(e.target)!).add(
        e.source
      );
    }
    return adj;
  }, [data]);

  // в радиальном виде центр = выбранная нода (для td/lr — null, без релейаута на клик)
  const radialCenter = graphLayout === "radial" ? selectedId : null;

  // ФОКУС-РЕЖИМ (радиальный вид): центр + только его прямые связи, остальное скрыто
  const focus = useMemo<{ center: string; ids: Set<string> } | null>(() => {
    if (graphLayout !== "radial" || groupBy !== "none" || !data) return null;
    let center =
      radialCenter && neighborhood.has(radialCenter) ? radialCenter : null;
    if (!center) {
      // ничего не выбрано → берём самую связанную ноду
      let best = -1;
      for (const [id, s] of neighborhood)
        if (s.size > best) {
          best = s.size;
          center = id;
        }
    }
    if (!center) return null;
    const ns = neighborhood.get(center) ?? new Set<string>();
    return { center, ids: new Set([center, ...ns]) };
  }, [graphLayout, groupBy, data, radialCenter, neighborhood]);

  // кандидаты для «связать с…»: подходят под поиск, кроме себя и уже связанных
  const linkMatches = useMemo<Set<string> | null>(() => {
    if (!linkingFrom || !data) return null;
    const q = linkQuery.trim().toLowerCase();
    const linked = neighborhood.get(linkingFrom) ?? new Set<string>();
    const out = new Set<string>();
    for (const n of data.nodes) {
      if (n.id === linkingFrom || linked.has(n.id)) continue;
      if (n.type !== "issue" || !n.project_id) continue;
      if (q && !n.title.toLowerCase().includes(q) && !`#${n.iid}`.includes(q))
        continue;
      out.add(n.id);
    }
    return out;
  }, [linkingFrom, linkQuery, data, neighborhood]);

  // Этап 8: компаратор вертикальной сортировки внутри колонки (Kanban). Ноды без
  // значения уходят вниз. null — авто-порядок (как пришли с бэка).
  const colComparator = useMemo<((a: Node, b: Node) => number) | undefined>(() => {
    if (colSort === "none") return undefined;
    const d = (n: Node) => n.data as GraphData["nodes"][number];
    const cmpNum = (av: number | null, bv: number | null) => {
      // отсутствующее значение — в конец независимо от направления
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return av - bv;
    };
    if (colSort === "due")
      return (a, b) =>
        cmpNum(
          d(a).due_date ? Date.parse(d(a).due_date!) : null,
          d(b).due_date ? Date.parse(d(b).due_date!) : null
        );
    if (colSort === "weight")
      return (a, b) => cmpNum(d(a).weight, d(b).weight);
    if (colSort === "iid") return (a, b) => d(a).iid - d(b).iid;
    // title
    return (a, b) => d(a).title.localeCompare(d(b).title);
  }, [colSort]);

  // Этап 2: раскладку считаем по ВСЕМ нодам один раз (мемо по data + grouping +
  // graphLayout + pinnedTick), фильтр поиска НЕ влияет на вход dagre/колонок.
  // Несоответствие фильтру → отдельный эффект ставит hidden/dimmed, позиции
  // остаются от полной раскладки. Так печать в поиске не вызывает релейаут.
  // Исключение — фокус-режим (радиальная «звезда»): он по природе показывает
  // подграф (центр + соседи), а не результат поиска, поэтому раскладывает только
  // свой набор и игнорирует ручные позиции.
  const positioned = useMemo<Node[]>(() => {
    if (!data) return [];
    // фокус-режим показывает только центр + соседей; иначе — все ноды
    const vis = focus
      ? data.nodes.filter((n) => focus.ids.has(n.id))
      : data.nodes;
    const visSet = new Set(vis.map((n) => n.id));
    // в раскладке «слева направо» хэндлы (и связи) — по бокам, иначе сверху/снизу
    const hdir: "lr" | "tb" = !grouping && graphLayout === "lr" ? "lr" : "tb";
    const base: Node[] = vis.map((n) => ({
      id: n.id,
      type: "issue",
      position: { x: 0, y: 0 },
      data: { ...n, dimmed: false, selected: false, dir: hdir },
    }));
    const rfEdges: Edge[] = data.edges
      .filter((e) => visSet.has(e.source) && visSet.has(e.target))
      .map((e) => ({ id: e.id, source: e.source, target: e.target }));
    const laid = grouping
      ? // Этап 8: Kanban — свёрнутые колонки сжаты, внутри колонки сортировка
        columnLayout(base, grouping.columns, grouping.keyOf, {
          collapsed: collapsedCols,
          sort: colComparator,
        })
      : focus
      ? radialLayout(base, rfEdges, focus.center)
      : // граф зависимостей: компоненты dagre + сетка одиночек + shelf-packing,
        // вместо вырождения dagre в горизонтальную «полосу» на слабосвязанных данных
        connectedComponentsLayout(
          base,
          rfEdges,
          graphLayout === "lr" ? "LR" : "TB"
        );
    // в фокус-режиме ручные позиции игнорируем (раскладка целиком вычисляемая)
    if (focus) return laid;
    // Этап 12 (D11): колоночные виды — теперь ПРОСМОТР (как граф): drag двигает
    // карточку и сохраняет ручную позицию (pinned), НЕ меняет данные задачи.
    // Вся смена данных переехала в отдельный режим «Доска». Поэтому ручные позиции
    // накладываем и здесь — единообразно с графом зависимостей.
    return laid.map((n) =>
      pinnedRef.current[n.id]
        ? { ...n, position: pinnedRef.current[n.id] }
        : n
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, grouping, focus, graphLayout, pinnedTick, collapsedCols, colComparator]);

  // ── ПОИСК КАК НАВИГАЦИЯ: центрируемся на совпадении, Enter — следующее ──
  // совпадения в порядке раскладки и только среди реально видимых нод (не отсеяны
  // прочими фильтрами и не скрыты), чтобы не наводить камеру на спрятанную ноду.
  const searchMatches = useMemo<string[]>(() => {
    const q = searchDebounced.trim().toLowerCase();
    if (!q) return [];
    return positioned
      .filter((n) => {
        if (!visibleIds.has(n.id)) return false;
        const d = n.data as GraphData["nodes"][number];
        return d.title.toLowerCase().includes(q) || `#${d.iid}`.includes(q);
      })
      .map((n) => n.id);
  }, [searchDebounced, positioned, visibleIds]);

  const searchHitRef = useRef(0);
  // плавно навести камеру на ноду по id (с учётом её центра)
  const centerOnNode = useCallback((id: string) => {
    const rf = rfRef.current;
    if (!rf) return;
    const n = rf.getNode(id);
    if (!n) return;
    const w = n.width ?? NODE_W;
    const h = n.height ?? 0;
    rf.setCenter(n.position.x + w / 2, n.position.y + h / 2, {
      zoom: Math.max(rf.getZoom(), 0.8),
      duration: 350,
    });
  }, []);

  // при новой строке поиска — на первое совпадение
  useEffect(() => {
    searchHitRef.current = 0;
    if (searchMatches.length) {
      const id = searchMatches[0];
      setSelectedId(id);
      // ждём, пока ноды лягут на холст после релейаута
      const t = setTimeout(() => centerOnNode(id), 60);
      return () => clearTimeout(t);
    }
  }, [searchMatches, centerOnNode]);

  // Enter в поле поиска — следующее совпадение по кругу
  const cycleSearch = useCallback(() => {
    if (!searchMatches.length) return;
    searchHitRef.current = (searchHitRef.current + 1) % searchMatches.length;
    const id = searchMatches[searchHitRef.current];
    setSelectedId(id);
    centerOnNode(id);
  }, [searchMatches, centerOnNode]);

  // Этап 2: базовый набор рёбер не зависит от поиска/фильтра — фильтр (hide/dim)
  // накладывает отдельный эффект через hidden/opacity. В фокус-режиме показываем
  // только рёбра подграфа (центр + соседи).
  const baseEdges = useMemo<Edge[]>(
    () =>
      (data?.edges ?? [])
        .filter((e) =>
          focus ? focus.ids.has(e.source) && focus.ids.has(e.target) : true
        )
        .map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          data: { type: e.type, link_id: e.link_id },
          ...edgeStyle(e.type, e.directed),
        })),
    [data, focus]
  );

  useEffect(() => setNodes(positioned), [positioned, setNodes]);
  useEffect(() => setEdges(baseEdges), [baseEdges, setEdges]);

  // Этап 2/F2: fitView перезапускаем ТОЛЬКО при смене области/группировки/геометрии
  // графа (раскладка меняется радикально), а не на поиск/фильтр — иначе вернётся
  // «прыгающий» вид. graphLayout в сигнатуре чинит баг «после смены ВИДа ноды за
  // экраном» (onlyRenderVisibleElements их анмаунтит). Первый монтаж покрывает
  // статический проп `fitView`, поэтому здесь его пропускаем.
  const fitSigRef = useRef<string | null>(null);
  const wantFitRef = useRef(false);
  useEffect(() => {
    if (!scope) return;
    // myTasks/loadState меняют сам набор нод (как и scope) → раскладка радикально
    // иная; включаем их в сигнатуру, чтобы авто-fit покрыл и F1-переключение
    // «мои → все», и догрузку closed.
    const sig = `${scope.kind}:${scope.id}|${groupBy}|${graphLayout}|${myTasks}|${loadState}`;
    if (fitSigRef.current === null) {
      fitSigRef.current = sig; // первый рендер — fitView сделает проп при монтаже
      return;
    }
    if (fitSigRef.current === sig) return;
    fitSigRef.current = sig;
    wantFitRef.current = true; // выполним, когда раскладка ляжет на холст
  }, [scope, groupBy, graphLayout, myTasks, loadState]);

  // выполняем отложенный fitView после того, как ноды для нового вида готовы
  useEffect(() => {
    if (!wantFitRef.current || !positioned.length) return;
    wantFitRef.current = false;
    const t = setTimeout(
      () => rfRef.current?.fitView({ padding: 0.15, maxZoom: 1, duration: 250 }),
      60
    );
    return () => clearTimeout(t);
  }, [positioned]);

  // Этап 2: оверлей фильтра/выделения поверх стабильной раскладки.
  //  • режим hide → ноду вне фильтра прячем через hidden:true (позиция от полной
  //    раскладки сохраняется, dagre не пересчитывается);
  //  • режим dim → затемняем (dimmed) вне фильтра;
  //  • плюс затемняем не-соседей выделения и подсвечиваем кандидатов «связать с…».
  // В фокус-режиме (радиальная «звезда») набор нод уже задан раскладкой —
  // фильтр поиска поверх него не прячем.
  useEffect(() => {
    const linking = !!linkingFrom;
    const neigh =
      !linking && selectedId
        ? neighborhood.get(selectedId) ?? new Set<string>()
        : null;
    // Этап 8: ноды свёрнутых колонок (Kanban) скрыты независимо от фильтра.
    const collapsedHidden = (n: Node) =>
      !!grouping &&
      collapsedCols.size > 0 &&
      collapsedCols.has(grouping.keyOf(n));
    // спрятана ли нода: свёрнутая колонка ИЛИ (режим hide и не в фокусе)
    const hide = (n: Node) =>
      collapsedHidden(n) ||
      (!focus && filterMode === "hide" && !visibleIds.has(n.id));
    const lit = (id: string) => {
      if (linking) return id === linkingFrom || !!linkMatches?.has(id);
      if (filterMode === "dim" && !visibleIds.has(id)) return false;
      return !neigh || id === selectedId || neigh.has(id);
    };
    // спрятанные id считаем заранее по стабильной раскладке (positioned), чтобы
    // setEdges не зависел от порядка применения апдейтеров setNodes/setEdges.
    const hiddenIds = new Set<string>();
    for (const n of positioned) if (hide(n)) hiddenIds.add(n.id);
    setNodes((ns) =>
      ns.map((n) => ({
        ...n,
        hidden: hiddenIds.has(n.id),
        data: {
          ...n.data,
          dimmed: !lit(n.id),
          selected: n.id === selectedId,
          linkTarget: linking && !!linkMatches?.has(n.id),
          pulse: n.id === pulseId,
        },
      }))
    );
    setEdges((es) =>
      es.map((e) => {
        const hidden = hiddenIds.has(e.source) || hiddenIds.has(e.target);
        const on = lit(e.source) && lit(e.target);
        return { ...e, hidden, style: { ...e.style, opacity: on ? 1 : 0.12 } };
      })
    );
  }, [
    selectedId,
    neighborhood,
    setNodes,
    setEdges,
    positioned,
    focus,
    filterMode,
    visibleIds,
    linkingFrom,
    linkMatches,
    pulseId,
    grouping,
    collapsedCols,
  ]);

  const selectedNode = data?.nodes.find((n) => n.id === selectedId) ?? null;

  // Этап 9 (D4): прямые связи выбранной задачи для вкладки «Связанные». Для
  // каждого ребра, касающегося выбранной ноды, берём «другую» ноду и выводим
  // метку по типу+направлению относительно выбранной (см. RELATED_KIND_LABEL).
  const relatedItems = useMemo<RelatedItem[]>(() => {
    if (!data || !selectedId) return [];
    const byId = new Map(data.nodes.map((n) => [n.id, n] as const));
    const out: RelatedItem[] = [];
    const seen = new Set<string>(); // (otherId|kind) — без дублей-параллельных рёбер
    for (const e of data.edges) {
      if (e.source !== selectedId && e.target !== selectedId) continue;
      const otherId = e.source === selectedId ? e.target : e.source;
      const other = byId.get(otherId);
      if (!other) continue;
      const outgoing = e.source === selectedId; // выбранная — источник ребра
      let kind: RelatedKind;
      if (e.type === "blocks") kind = outgoing ? "blocks" : "blocked";
      else if (e.type === "parent") kind = outgoing ? "child" : "parent";
      else if (e.type === "epic") kind = "epic";
      else kind = "relates";
      const dedupe = `${otherId}|${kind}`;
      if (seen.has(dedupe)) continue;
      seen.add(dedupe);
      out.push({ node: other, kind });
    }
    // порядок групп — по RELATED_ORDER (блокировки → иерархия → связи → эпик)
    out.sort((a, b) => RELATED_ORDER[a.kind] - RELATED_ORDER[b.kind]);
    return out;
  }, [data, selectedId]);

  // Этап 9 (D4): «⌖ Фокус» — навести камеру на ноду и кратко подсветить (pulse),
  // НЕ меняя выбор/панель. Переиспользует centerOnNode + pulse (как поиск/D3).
  const focusNode = useCallback(
    (id: string) => {
      centerOnNode(id);
      setPulseId(id);
    },
    [centerOnNode]
  );

  // Этап 6: при выборе issue-ноды подгружаем детали (description/MR/time_stats).
  // Эпики и ноды без project_id (graphql/особые) пропускаем — детального GET нет.
  // Гонку отменяем флагом: быстрый перещёлк не покажет данные прежней задачи.
  // Зависимости — по identity выбранной задачи (id/pid/iid), а не по объекту ноды:
  // точечные PATCH меняют ссылку selectedNode, но детали тогда обновляем вручную
  // (applyPatched/changeDescription), повторный fetch не нужен.
  const selProjectId = selectedNode?.project_id ?? null;
  const selIid = selectedNode?.iid ?? null;
  const selType = selectedNode?.type ?? null;
  useEffect(() => {
    setDetail(null);
    if (selType !== "issue" || !selProjectId || selIid == null) return;
    let alive = true;
    api
      .issueDetail(selProjectId, selIid)
      .then((d) => {
        if (alive) setDetail(d);
      })
      .catch(() => {
        /* детали — бонус; их отсутствие не ломает базовую карточку */
      });
    return () => {
      alive = false;
    };
  }, [selProjectId, selIid, selType]);

  // создать связь нужного типа — оптимистично, БЕЗ перезагрузки графа
  const linkNodes = useCallback(
    async (srcId: string, tgtId: string, kind: string) => {
      if (!scope) return;
      const src = data?.nodes.find((n) => n.id === srcId);
      const tgt = data?.nodes.find((n) => n.id === tgtId);
      if (!src || !tgt || !src.project_id || !tgt.project_id) return;
      try {
        const res = await api.createLink({
          project_id: src.project_id,
          iid: src.iid,
          target_project_id: tgt.project_id,
          target_iid: tgt.iid,
          kind,
        });
        // строим ребро локально по той же схеме, что и бэкенд (id по паре)
        const [a, b] = [srcId, tgtId].sort();
        const edge: GraphData["edges"][number] =
          kind === "relates"
            ? { id: `relates|${a}|${b}`, source: a, target: b,
                type: "relates_to", directed: false, link_id: res.link_id }
            : { id: `relates|${a}|${b}`, source: srcId, target: tgtId,
                type: kind === "blocks" ? "blocks" : "parent",
                directed: true, link_id: res.link_id };
        setData((d) =>
          d
            ? { ...d, edges: [...d.edges.filter((e) => e.id !== edge.id), edge] }
            : d
        );
        setToast("Связь создана");
      } catch (e) {
        setToast("Ошибка связи: " + e);
      }
    },
    [data, scope]
  );

  // перетаскивание связи → открываем поповер выбора типа
  const onConnect = useCallback((c: Connection) => {
    if (c.source && c.target && c.source !== c.target)
      setPendingLink({ source: c.source, target: c.target });
  }, []);

  // сменить тип/направление существующей связи — локально, без перезагрузки
  const retypeEdge = useCallback(
    async (source: string, target: string, kind: string) => {
      const pairKey = [source, target].sort().join("|");
      setData((d) =>
        d
          ? {
              ...d,
              edges: d.edges.map((e) => {
                if ([e.source, e.target].sort().join("|") !== pairKey) return e;
                const [a, b] = [source, target].sort();
                return kind === "relates"
                  ? { ...e, source: a, target: b, type: "relates_to", directed: false }
                  : {
                      ...e, source, target,
                      type: kind === "blocks" ? "blocks" : "parent",
                      directed: true,
                    };
              }),
            }
          : d
      );
      try {
        await api.setLinkMeta(source, target, kind);
      } catch (e) {
        setToast("Ошибка смены типа: " + e);
      }
    },
    []
  );

  // клик по ребру → контекстное меню (тип / удалить)
  const onEdgeClick = useCallback(
    (e: ReactMouseEvent, edge: Edge) => {
      const linkId = (edge.data as { link_id?: number })?.link_id;
      setEdgeMenu({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        link_id: linkId ?? undefined,
        x: e.clientX,
        y: e.clientY,
      });
    },
    []
  );

  const deleteEdge = useCallback(async () => {
    if (!edgeMenu?.link_id) {
      setToast("Эту связь нельзя удалить отсюда");
      setEdgeMenu(null);
      return;
    }
    const [pid, iid] = edgeMenu.source.split(":").map(Number);
    try {
      await api.deleteLink(pid, iid, edgeMenu.link_id, edgeMenu.target);
      setData((d) =>
        d ? { ...d, edges: d.edges.filter((e) => e.id !== edgeMenu.id) } : d
      );
      setToast("Связь удалена");
    } catch (e) {
      setToast("Ошибка удаления: " + e);
    }
    setEdgeMenu(null);
  }, [edgeMenu]);

  // точечное обновление одной ноды из ответа PATCH — без перезагрузки всего графа
  const applyNodePatch = useCallback((updated: GraphData["nodes"][number]) => {
    setData((d) => {
      if (!d) return d;
      // PATCH/PUT-ответ GitLab отдаёт метки БЕЗ цвета (только имена-строки) —
      // восстановим цвет по имени из meta.labels и из прежней ноды, иначе чипы
      // меток на доске/в графе теряют цвет после переноса/правки задачи.
      const colorByName = new Map<string, string>();
      for (const l of d.meta.labels) if (l.color) colorByName.set(l.name, l.color);
      const prev = d.nodes.find((n) => n.id === updated.id);
      if (prev)
        for (const l of prev.labels) if (l.color) colorByName.set(l.name, l.color);
      const recolored = {
        ...updated,
        labels: updated.labels.map((l) =>
          l.color ? l : { ...l, color: colorByName.get(l.name) ?? l.color }
        ),
      };
      return {
        ...d,
        nodes: d.nodes.map((n) => (n.id === updated.id ? recolored : n)),
      };
    });
  }, []);

  const changeSprint = async (msId: number | null) => {
    if (!selectedNode?.project_id) return;
    applyNodePatch(
      await api.patchIssue(selectedNode.project_id, selectedNode.iid, {
        milestone_id: msId,
      })
    );
  };

  const toggleState = async () => {
    if (!selectedNode?.project_id) return;
    applyNodePatch(
      await api.patchIssue(selectedNode.project_id, selectedNode.iid, {
        state_event: selectedNode.state === "closed" ? "reopen" : "close",
      })
    );
  };

  const changeAssignee = async (userId: number | null) => {
    if (!selectedNode?.project_id) return;
    applyNodePatch(
      await api.patchIssue(selectedNode.project_id, selectedNode.iid, {
        assignee_ids: userId ? [userId] : [],
      })
    );
  };

  const changeDue = async (date: string | null) => {
    if (!selectedNode?.project_id) return;
    applyNodePatch(
      await api.patchIssue(selectedNode.project_id, selectedNode.iid, {
        due_date: date || null,
      })
    );
  };

  // PATCH вернул обновлённую ноду — освежаем И граф, И открытые детали (Этап 6),
  // чтобы карточка не показывала устаревшие метки/исполнителей/вес после правки.
  const applyPatched = useCallback(
    (updated: GraphData["nodes"][number]) => {
      applyNodePatch(updated);
      setDetail((d) => (d && d.id === updated.id ? { ...d, ...updated } : d));
    },
    [applyNodePatch]
  );

  // Этап 12 (D11): drag на холсте больше НЕ меняет данные (бывший handleKanbanDrop
  // удалён). Колоночные виды — теперь ПРОСМОТР; вся смена данных задач (drag→PATCH)
  // живёт в отдельном режиме «Доска» (компонент Board.tsx).

  // Этап 6: несколько исполнителей (бэк уже list; на Free деградируем до одного
  // в UI). Передаём полный набор id — GitLab перезаписывает список целиком.
  const changeAssignees = async (userIds: number[]) => {
    if (!selectedNode?.project_id) return;
    applyPatched(
      await api.patchIssue(selectedNode.project_id, selectedNode.iid, {
        assignee_ids: userIds,
      })
    );
  };

  // Этап 6: инлайн-метки — add/remove инкрементально, без перетирания остальных.
  const changeLabels = async (add: string[], remove: string[]) => {
    if (!selectedNode?.project_id) return;
    if (add.length === 0 && remove.length === 0) return;
    applyPatched(
      await api.patchIssue(selectedNode.project_id, selectedNode.iid, {
        add_labels: add.length ? add : undefined,
        remove_labels: remove.length ? remove : undefined,
      })
    );
  };

  // Этап 6: правка описания (markdown-исходник). Также обновляем detail вручную —
  // PATCH-ответ описания не содержит (нода графа без него), а карточка показывает.
  const changeDescription = async (text: string) => {
    if (!selectedNode?.project_id) return;
    const updated = await api.patchIssue(
      selectedNode.project_id,
      selectedNode.iid,
      { description: text }
    );
    applyNodePatch(updated);
    setDetail((d) =>
      d && d.id === updated.id ? { ...d, ...updated, description: text } : d
    );
  };

  // D3 (Вариант 2): после создания ставим ноду туда, куда смотрит пользователь —
  // в центр вьюпорта (дочернюю — рядом с родителем), закрепляем позицию в
  // pinnedRef, выделяем, наводим камеру и кратко подсвечиваем (pulse). Глобальный
  // fitView на этот релоад не запускаем (fitView у ReactFlow срабатывает только на
  // монтировании, поэтому здесь достаточно навести камеру точечно).
  const handleCreated = useCallback(
    async (created: { id: string; iid: number; project_id: number }) => {
      const parent = parentNode; // захватываем до сброса
      setCreating(false);
      setParentNode(null);

      const rf = rfRef.current;
      let pos: { x: number; y: number } | null = null;
      if (rf) {
        if (parent && pinnedRef.current[parent.id]) {
          // дочерняя — рядом с родителем (со смещением вниз-вправо)
          const p = pinnedRef.current[parent.id];
          pos = { x: p.x + NODE_W + 60, y: p.y + NODE_H + 40 };
        } else if (parent) {
          // позиция родителя из текущей раскладки на холсте
          const pn = rf.getNode(parent.id);
          if (pn)
            pos = { x: pn.position.x + NODE_W + 60, y: pn.position.y + NODE_H + 40 };
        }
        if (!pos) {
          // центр текущего вьюпорта в координатах холста (screenToFlowPosition)
          const rect = document
            .querySelector(".react-flow")
            ?.getBoundingClientRect();
          const cx = rect ? rect.left + rect.width / 2 : window.innerWidth / 2;
          const cy = rect ? rect.top + rect.height / 2 : window.innerHeight / 2;
          const c = rf.screenToFlowPosition({ x: cx, y: cy });
          pos = { x: c.x - NODE_W / 2, y: c.y - NODE_H / 2 };
        }
      }
      if (pos) {
        pinnedRef.current[created.id] = pos;
        if (posKey)
          localStorage.setItem(posKey, JSON.stringify(pinnedRef.current));
        setHasPins(true);
        setPinnedTick((t) => t + 1);
      }
      setSelectedId(created.id);
      setPendingFocusId(created.id); // фокус наведём ПОСЛЕ пересчёта раскладки
      if (scope) await loadGraph(scope, true, loadState, myTasks);
      // P2: явный тост с номером — успех легко пропустить (панель/пульс неброские)
      setToast(`Задача создана #${created.iid}`);
    },
    [parentNode, posKey, scope, loadState, myTasks, loadGraph]
  );

  // фокус на новой ноде после того, как раскладка положила её на холст
  useEffect(() => {
    if (!pendingFocusId) return;
    const id = pendingFocusId;
    if (!nodes.some((n) => n.id === id)) return; // ещё не на холсте — ждём
    const t = setTimeout(() => {
      centerOnNode(id);
      setPulseId(id);
      setPendingFocusId(null);
    }, 60);
    return () => clearTimeout(t);
  }, [pendingFocusId, nodes, centerOnNode]);

  // pulse гаснет сам
  useEffect(() => {
    if (!pulseId) return;
    const t = setTimeout(() => setPulseId(null), 1600);
    return () => clearTimeout(t);
  }, [pulseId]);

  // ── Этап 7: команды холста (фокус на соседях / нода на холсте) ──
  // «Вписать всё в экран» (Fit) живёт в зум-контроле (низ-лево, рядом с −/%/+).

  // 🎯 фокус на соседях: bbox выбранной ноды + её прямых связей → fitBounds, без
  // релейаута (используем уже посчитанную раскладку из rf.getNodes()).
  const focusNeighbors = useCallback(() => {
    const rf = rfRef.current;
    if (!rf || !selectedId) return;
    const ids = new Set<string>([selectedId]);
    for (const id of neighborhood.get(selectedId) ?? new Set<string>())
      ids.add(id);
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const id of ids) {
      const n = rf.getNode(id);
      if (!n) continue;
      const w = n.width ?? NODE_W;
      const h = n.height ?? NODE_H;
      minX = Math.min(minX, n.position.x);
      minY = Math.min(minY, n.position.y);
      maxX = Math.max(maxX, n.position.x + w);
      maxY = Math.max(maxY, n.position.y + h);
    }
    if (!Number.isFinite(minX)) return;
    rf.fitBounds(
      { x: minX, y: minY, width: maxX - minX, height: maxY - minY },
      { duration: 300, padding: 0.25 }
    );
  }, [selectedId, neighborhood]);

  // ＋ нода на холсте: открыть форму создания «с нуля» (без родителя). Позицию в
  // центр вьюпорта поставит handleCreated (D3, screenToFlowPosition) после ответа.
  const addNodeOnCanvas = useCallback(() => {
    setSelectedId(null);
    setParentNode(null);
    setCreating(true);
    setSidebarOpen(true);
  }, []);

  // ── лупа (zoom-to-area): тянем рамку при tool='zoomArea', по отпусканию
  // переводим screen-точки в координаты холста и fitBounds в выделенную область,
  // затем авто-возврат к инструменту выбора. Рамку рисует штатный
  // .react-flow__selection (стилизована в CSS); здесь — только геометрия. ──
  const zoomAreaRef = useRef<{ x: number; y: number } | null>(null);
  const onCanvasMouseDown = useCallback(
    (e: ReactMouseEvent) => {
      if (tool !== "zoomArea" || e.button !== 0) return;
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return;
      zoomAreaRef.current = {
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
      };
    },
    [tool]
  );
  const onCanvasMouseUp = useCallback(
    (e: ReactMouseEvent) => {
      if (tool !== "zoomArea") return;
      const start = zoomAreaRef.current;
      zoomAreaRef.current = null;
      const rf = rfRef.current;
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!start || !rf || !rect) return;
      const end = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      // слишком маленькая рамка (клик/дрожь) — игнор, не дёргаем камеру
      if (Math.abs(end.x - start.x) < 8 || Math.abs(end.y - start.y) < 8) {
        setTool("select");
        return;
      }
      const p1 = rf.screenToFlowPosition({
        x: rect.left + start.x,
        y: rect.top + start.y,
      });
      const p2 = rf.screenToFlowPosition({
        x: rect.left + end.x,
        y: rect.top + end.y,
      });
      rf.fitBounds(
        {
          x: Math.min(p1.x, p2.x),
          y: Math.min(p1.y, p2.y),
          width: Math.abs(p2.x - p1.x),
          height: Math.abs(p2.y - p1.y),
        },
        { duration: 300, padding: 0.1 }
      );
      setTool("select"); // лупа одноразовая — вернулись к выбору
    },
    [tool]
  );

  // ── лассо + bulk: onSelectionChange копит выбранные ноды (только issue с
  // project_id — мета-ноды эпиков bulk'ом не правим). В режиме лупы (zoomArea)
  // рамка нужна только для зума, не для bulk — выбранное игнорируем. ──
  const onSelectionChange = useCallback(
    ({ nodes: sel }: { nodes: Node[] }) => {
      if (tool === "zoomArea") return;
      const ids = sel
        .filter((n) => {
          const d = n.data as GraphData["nodes"][number];
          return d?.type === "issue" && d.project_id;
        })
        .map((n) => n.id);
      setSelectedIds(ids);
    },
    [tool]
  );

  // node-id "P:I" → [project_id, iid] для bulk-таргетов
  const idToTarget = useCallback((id: string): [number, number] | null => {
    const n = data?.nodes.find((x) => x.id === id);
    if (!n || !n.project_id) return null;
    return [n.project_id, n.iid];
  }, [data]);

  // выполнить массовое действие лассо: POST /api/issues/bulk (gather по семафору,
  // частичные ошибки). После — релоад графа и тост с итогом.
  const runBulk = useCallback(
    async (patch: BulkPatchBody, label: string) => {
      const targets = selectedIds
        .map(idToTarget)
        .filter((t): t is [number, number] => !!t);
      if (!targets.length) return;
      setBulkBusy(true);
      try {
        const r = await api.bulkPatch(targets, patch);
        if (r.failed > 0)
          setToast(`${label}: готово ${r.succeeded}, ошибок ${r.failed}`);
        else setToast(`${label}: ${r.succeeded} задач`);
        if (scope) await loadGraph(scope, true, loadState, myTasks);
      } catch (e) {
        setToast("Ошибка массового действия: " + e);
      } finally {
        setBulkBusy(false);
      }
    },
    [selectedIds, idToTarget, scope, loadState, myTasks, loadGraph]
  );

  const logout = async () => {
    try {
      await api.logout();
    } catch {
      /* всё равно сбрасываем локально */
    }
    setLoggedIn(false);
    setUser(null);
    setData(null);
    setScope(null);
    setNamespaces(null);
    setSelectedId(null);
  };

  // Этап 9 (D6): необязательный комментарий к пинку; poked_by бэк подставит сам.
  const pokeAssignee = async (message?: string) => {
    if (!selectedNode?.project_id) return;
    try {
      const r = await api.poke(
        selectedNode.project_id,
        selectedNode.iid,
        message || undefined
      );
      setToast(`🔔 Напоминание отправлено: @${r.to}`);
    } catch {
      setToast("Не удалось отправить напоминание исполнителю");
    }
  };

  const onResizeStart = (e: ReactMouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = sidebarW;
    const move = (ev: MouseEvent) => {
      const w = Math.min(720, Math.max(280, startW + (startX - ev.clientX)));
      setSidebarW(w);
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const createSprint = async () => {
    const title = (newSprint ?? "").trim();
    if (!title || !scope) return;
    try {
      await api.createMilestone(scope, title);
      setNewSprint(null);
      await loadGraph(scope, true, loadState, myTasks);
      setToast(`Спринт «${title}» создан`);
    } catch (e) {
      setToast("Ошибка создания спринта: " + e);
    }
  };

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  // Esc — снять выделение/фокус; Этап 7 — горячие клавиши инструментов V/H/Z/L
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelectedId(null);
        setNewSprint(null);
        setLinkingFrom(null);
        setLinkQuery("");
        setTool("select"); // лупа/лассо/рука → обратно к выбору
        const el = document.activeElement as HTMLElement | null;
        el?.blur();
        return;
      }
      // горячие клавиши инструментов не должны срабатывать при печати в полях
      const t = e.target as HTMLElement | null;
      if (
        e.metaKey ||
        e.ctrlKey ||
        e.altKey ||
        (t &&
          (t.tagName === "INPUT" ||
            t.tagName === "TEXTAREA" ||
            t.tagName === "SELECT" ||
            t.isContentEditable))
      )
        return;
      const map: Record<string, typeof tool> = {
        v: "select",
        h: "pan",
        z: "zoomArea",
        l: "lasso",
      };
      const next = map[e.key.toLowerCase()];
      if (next) {
        e.preventDefault();
        setTool(next);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const allMilestones = meta?.milestones ?? data?.meta.milestones ?? [];
  // Этап 5: порог перегруза — баннер «сузьте» при > 150 загруженных задач
  // (критерий приёмки ROADMAP). Считаем по загруженному набору, не по видимым.
  const OVERLOAD_THRESHOLD = 150;
  const overloaded = !!data && data.meta.issue_count > OVERLOAD_THRESHOLD;
  // ключ закрытия плашек привязан к текущей области (см. dismissBanner)
  const scopeKey = scope ? `${scope.kind}:${scope.id}` : "none";
  const mineDismissed = dismissed.has(`mine:${scopeKey}`);
  const overloadDismissed = dismissed.has(`overload:${scopeKey}`);
  // исполнители: из задач графа (доступно всегда, без админских прав) + участники
  const assigneeOptions = (() => {
    const m = new Map<string, { id: number | null; name: string }>();
    for (const n of data?.nodes ?? [])
      for (const a of n.assignees)
        if (a.username) m.set(a.username, { id: a.id, name: a.name ?? a.username });
    for (const u of meta?.members ?? [])
      if (!m.has(u.username)) m.set(u.username, { id: u.id, name: u.name });
    return [...m.entries()]
      .map(([username, v]) => ({ username, id: v.id, name: v.name }))
      .sort((a, b) => a.name.localeCompare(b.name));
  })();
  const headerData = grouping
    ? (() => {
        const xs = columnHeaders(grouping.columns, collapsedCols);
        const counts = new Map<string, number>();
        // WIP-счётчики считаем по ВСЕМ нодам колонки (в т.ч. свёрнутым/скрытым) —
        // счётчик не должен «худеть» от сворачивания/фильтра.
        for (const n of positioned) {
          const k = grouping.keyOf(n);
          counts.set(k, (counts.get(k) || 0) + 1);
        }
        const all = grouping.columns.map((c, i) => ({
          key: c.key,
          title: c.title,
          x: xs[i].x,
          count: counts.get(c.key) || 0,
          collapsed: collapsedCols.has(c.key),
          // Этап 8: превышение WIP-лимита подсвечивает счётчик
          over: wipLimit > 0 && (counts.get(c.key) || 0) > wipLimit,
        }));
        // P3: в грид-режиме (columnLayout вырождает одну непустую колонку в сетку)
        // показываем шапку ТОЛЬКО непустой колонки на старте сетки (offsets[0]),
        // иначе пустые шапки висят над сеткой как визуальный мусор/наложение.
        const nonEmpty = all.filter((h) => h.count > 0);
        if (nonEmpty.length <= 1 && positioned.length > 1) {
          return nonEmpty.map((h) => ({ ...h, x: xs[0].x }));
        }
        return all;
      })()
    : [];
  // Этап 8: свернуть/развернуть колонку (toggle по ключу)
  const toggleColumn = useCallback((key: string) => {
    setCollapsedCols((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
  // подпись измерения группировки для шапки колонок
  const groupDimLabel =
    groupBy === "milestone"
      ? "Спринт"
      : groupBy === "label"
      ? "Метка"
      : groupBy === "assignee"
      ? "Исполнитель"
      : groupBy === "project"
      ? "Проект"
      : groupBy === "state"
      ? "Статус"
      : "";

  // Этап 12 (D11): дорожки/подсветка/подпись доски (Этап 11) удалены с холста —
  // колоночные виды стали ПРОСМОТРОМ. ColumnHeaders (шапки колонок) и грид-фолбэк
  // (Этап 10/P3) остаются как ориентир в просмотре. Настоящая Kanban — режим «Доска».

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-logo">◆</span> Issue Graph
        </div>
        <select
          className="scope-select"
          title="Область графа: группа или проект"
          value={scope ? `${scope.kind}:${scope.id}` : ""}
          onChange={(e) => {
            const [kind, id] = e.target.value.split(":");
            const label =
              kind === "group"
                ? namespaces?.groups.find((g) => String(g.id) === id)?.full_path
                : namespaces?.projects.find((p) => String(p.id) === id)
                    ?.full_path;
            setSelectedId(null);
            setScope({ kind: kind as "group" | "project", id, label: label ?? id });
          }}
        >
          <optgroup label="Группы">
            {namespaces?.groups.map((g) => (
              <option key={"g" + g.id} value={`group:${g.id}`}>
                {g.full_path}
              </option>
            ))}
          </optgroup>
          <optgroup label="Проекты">
            {namespaces?.projects.map((p) => (
              <option key={"p" + p.id} value={`project:${p.id}`}>
                {p.full_path}
              </option>
            ))}
          </optgroup>
        </select>

        {/* Этап 12 (D8): переключатель режима «Граф» ↔ «Доска» переехал в нижний
            левый док холста (.canvas-dock) — сегментированная пилюля. */}
        {!boardMode && (
          <label className="groupby" title="Как разложить ноды на холсте">
            <span>вид</span>
            <select
              value={groupBy}
              onChange={(e) => setGroupBy(e.target.value as typeof groupBy)}
            >
              <option value="none">граф зависимостей</option>
              <option value="milestone">колонки: спринты</option>
              <option value="label">колонки: метки</option>
              <option value="assignee">колонки: исполнители</option>
              <option value="project">колонки: проекты</option>
              <option value="state">колонки: статусы</option>
            </select>
          </label>
        )}

        {/* Этап 8 (Kanban): сортировка внутри колонки + WIP-лимит. Только в
            колоночном виде — для графа зависимостей бессмысленны. */}
        {!boardMode && groupBy !== "none" && (
          <>
            <label
              className="groupby"
              title="Порядок задач внутри колонки (Kanban)"
            >
              <span>сорт</span>
              <select
                value={colSort}
                onChange={(e) =>
                  setColSort(e.target.value as typeof colSort)
                }
              >
                <option value="none">как есть</option>
                <option value="due">по сроку</option>
                <option value="weight">по весу</option>
                <option value="iid">по #</option>
                <option value="title">по названию</option>
              </select>
            </label>
            <label
              className="wip-limit"
              title="WIP-лимит на колонку: превышение подсвечивается (0 — выкл)"
            >
              <span>WIP</span>
              <input
                type="number"
                min={0}
                max={99}
                value={wipLimit || ""}
                placeholder="0"
                onChange={(e) =>
                  setWipLimit(Math.max(0, Number(e.target.value) || 0))
                }
              />
            </label>
          </>
        )}

        {/* P2: поиск всегда виден в топбаре (тот же state search + дебаунс).
            Компактный инпут с лупой; счётчик совпадений n/m справа. */}
        <div className="topbar-search" title="Поиск по названию / #iid — Enter: следующее совпадение">
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
            <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.6" fill="none" />
            <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
          <input
            placeholder="поиск"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                cycleSearch();
              }
            }}
          />
          {search.trim() && (
            <span className="ts-hits" title="Найдено задач на холсте">
              {searchMatches.length
                ? `${(searchHitRef.current % searchMatches.length) + 1}/${searchMatches.length}`
                : "0"}
            </span>
          )}
          {search.trim() && (
            <button
              className="ts-clear"
              title="Очистить поиск"
              onClick={() => setSearch("")}
            >
              ✕
            </button>
          )}
        </div>

        <button
          className={`btn-pill ${filtersOpen ? "on" : ""}`}
          onClick={() => setFiltersOpen((v) => !v)}
          title="Показать/скрыть фильтры"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
            <path
              d="M1 3h14M3.5 8h9M6.5 13h3"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              fill="none"
            />
          </svg>
          Фильтры
          {activeFilters > 0 && <span className="badge">{activeFilters}</span>}
        </button>

        {/* D1: тумблер «открытые / все» — догружает закрытые с бэка */}
        <div
          className="state-toggle"
          title="Грузить только открытые задачи (быстрее, меньше нод) или все"
        >
          <button
            className={loadState === "opened" ? "on" : ""}
            onClick={() => setLoadState("opened")}
          >
            открытые
          </button>
          <button
            className={loadState === "all" ? "on" : ""}
            onClick={() => setLoadState("all")}
          >
            все
          </button>
        </div>

        {/* Этап 5: счётчик кликабелен — открывает фильтры (быстрый путь сузить).
            При перегрузе подсвечен, намекая, что объём стоит сузить. */}
        {data && (
          <button
            className={`counts-top${overloaded ? " warn" : ""}`}
            title={`Видно ${visibleIds.size} · загружено ${data.meta.issue_count} (${loadState === "opened" ? "только открытые" : "все"}) — нажмите, чтобы сузить фильтрами`}
            onClick={() => setFiltersOpen((v) => !v)}
          >
            {`${visibleIds.size} видно / ${data.meta.issue_count} ${loadState === "opened" ? "открытых" : "всего"}`}
          </button>
        )}

        <div className="spacer" />

        {caps && (
          <div className="caps" title="Возможности инстанса GitLab (тариф)">
            <span
              className={`tier tier-${caps.tier}`}
              title={`Тариф инстанса GitLab: ${caps.tier} (free — бесплатный, premium/ultimate — платные)`}
            >
              {caps.tier}
            </span>
            <span
              className={caps.epics ? "feat on" : "feat"}
              title={
                caps.epics
                  ? "Эпики доступны (функция GitLab Premium): нативные эпики GitLab"
                  : "Эпики недоступны (нужен GitLab Premium): на этом инстансе эмулируются связью parent→child"
              }
            >
              epics
            </span>
            <span
              className={caps.blocking_links ? "feat on" : "feat"}
              title={
                caps.blocking_links
                  ? "Блокирующие связи доступны (функция GitLab Premium): нативные blocks-зависимости"
                  : "Блокирующие связи недоступны (нужен GitLab Premium): тип «блокирует» ведёт приложение поверх relates"
              }
            >
              blocking
            </span>
          </div>
        )}
        <button
          className="btn-primary"
          disabled={!meta}
          title={
            meta
              ? "Создать новую задачу"
              : "Создание доступно после загрузки области (нужны проекты/спринты)"
          }
          onClick={() => {
            setParentNode(null);
            setCreating(true);
            setSelectedId(null);
            setSidebarOpen(true);
          }}
        >
          <span className="ic">＋</span> Задача
        </button>
        <button
          className="btn-icon"
          title={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
          onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
        <button
          className="btn-icon"
          title="Справка — как всё работает"
          onClick={() => setShowHelp(true)}
        >
          ?
        </button>
        {user && (
          <button
            className="user-chip"
            onClick={logout}
            title={`Вы вошли как @${user.username ?? ""} — нажмите, чтобы выйти`}
          >
            <span className="user-ava">
              {(user.name ?? user.username ?? "?")
                .split(" ")
                .map((s) => s[0])
                .join("")
                .slice(0, 2)
                .toUpperCase()}
            </span>
            <span className="user-text">
              <span className="user-name">{user.name ?? user.username}</span>
              <span className="user-logout-label">Выйти ⎋</span>
            </span>
          </button>
        )}
      </header>

      <div className={`filters ${filtersOpen ? "" : "hidden"}`}>
        {/* P2: строка поиска вынесена в топбар (всегда видна) — здесь дубль убран,
            чтобы не было двух источников правды; state search общий. */}
        <select value={fState} onChange={(e) => setFState(e.target.value)}>
          <option value="all">состояние: все</option>
          <option value="opened">открытые</option>
          <option value="closed">закрытые</option>
        </select>
        <select value={fMs} onChange={(e) => setFMs(e.target.value)}>
          <option value="all">спринт: все</option>
          {allMilestones.map((m) => (
            <option key={m.id} value={String(m.id)}>
              {m.title}
            </option>
          ))}
          <option value="none">без спринта</option>
        </select>
        {newSprint === null ? (
          <button
            className="btn-sprint"
            disabled={!scope}
            title="Создать новый спринт (milestone)"
            onClick={() => setNewSprint("")}
          >
            ＋ спринт
          </button>
        ) : (
          <span className="sprint-new">
            <input
              autoFocus
              placeholder="название спринта"
              value={newSprint}
              onChange={(e) => setNewSprint(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") createSprint();
                if (e.key === "Escape") setNewSprint(null);
              }}
            />
            <button onClick={createSprint} disabled={!newSprint.trim()}>
              ✓
            </button>
            <button onClick={() => setNewSprint(null)}>✕</button>
          </span>
        )}
        <select value={fLabel} onChange={(e) => setFLabel(e.target.value)}>
          <option value="all">метка: все</option>
          {data?.meta.labels.map((l) => (
            <option key={l.name} value={l.name}>
              {l.name}
            </option>
          ))}
        </select>
        <select value={fAssignee} onChange={(e) => setFAssignee(e.target.value)}>
          <option value="all">исполнитель: все</option>
          {user?.username && <option value={user.username}>⭐ Мои</option>}
          {assigneeOptions.map((u) => (
            <option key={u.username} value={u.username}>
              {u.name}
            </option>
          ))}
        </select>
        <span className="date-filter">
          <select
            value={fDateField}
            onChange={(e) => setFDateField(e.target.value as typeof fDateField)}
            title="По какому полю даты фильтровать"
          >
            <option value="due_date">срок</option>
            <option value="created_at">создано</option>
            <option value="updated_at">обновлено</option>
          </select>
          <input
            type="date"
            value={fFrom}
            onChange={(e) => setFFrom(e.target.value)}
            title="с даты"
          />
          <span className="date-dash">–</span>
          <input
            type="date"
            value={fTo}
            onChange={(e) => setFTo(e.target.value)}
            title="по дату"
          />
          {(fFrom || fTo) && (
            <button
              className="date-clear"
              title="Сбросить диапазон"
              onClick={() => {
                setFFrom("");
                setFTo("");
              }}
            >
              ✕
            </button>
          )}
        </span>
        <span className="fmode" title="Что делать с задачами вне фильтра">
          <button
            className={filterMode === "hide" ? "on" : ""}
            onClick={() => setFilterMode("hide")}
          >
            скрывать
          </button>
          <button
            className={filterMode === "dim" ? "on" : ""}
            onClick={() => setFilterMode("dim")}
          >
            затемнять
          </button>
        </span>
        {activeFilters > 0 && (
          <button className="btn-reset" onClick={resetFilters} title="Сбросить все фильтры">
            ✕ сбросить ({activeFilters})
          </button>
        )}
      </div>

      <div className="main">
        <div
          className={`canvas tool-${tool}`}
          ref={canvasRef}
          onMouseDown={onCanvasMouseDown}
          onMouseUp={onCanvasMouseUp}
        >
          {error && <div className="error">{error}</div>}
          {loggedIn && (loading || (scope && !data) || (!namespaces && !error)) && (
            <div className="loading-overlay">
              <div className="spinner" />
              <div className="loading-text">Загрузка графа…</div>
            </div>
          )}

          {/* Этап 5: плашки над холстом — «Мои задачи», «слишком много», D2.
              Этап 12: в режиме «Доска» не показываем (плашки про плотность графа). */}
          {data && !boardMode && (
            <div className="canvas-banners">
              {/* «Мои задачи»: видимая плашка + быстрый выход в общий вид */}
              {myTasks && !mineDismissed && (
                <div className="cv-banner mine">
                  <span className="cb-ic">⭐</span>
                  <span className="cb-text">
                    Ваши задачи{scope?.label ? ` в ${scope.label}` : ""}
                  </span>
                  <button
                    className="cb-action"
                    onClick={() => setMyTasks(false)}
                    title="Показать все задачи области (не только ваши)"
                  >
                    показать все
                  </button>
                  <button
                    className="cb-close"
                    onClick={() => dismissBanner(`mine:${scopeKey}`)}
                    title="Скрыть плашку"
                    aria-label="Скрыть плашку"
                  >
                    ✕
                  </button>
                </div>
              )}

              {/* «слишком много — сузьте»: порог перегруза issue_count > 150.
                  × скрывает плашку (и парный хинт «активные за N дней») для этой
                  области; индикатор перегруза остаётся в счётчике справа сверху. */}
              {overloaded && !overloadDismissed && (
                <div className="cv-banner warn">
                  <span className="cb-ic">⚠</span>
                  <span className="cb-text">
                    Слишком много задач ({data.meta.issue_count}) — граф трудно
                    читать. Сузьте область:
                  </span>
                  <span className="cb-cta">
                    {user?.username && !myTasks && (
                      <button onClick={() => setMyTasks(true)}>
                        Показать мои
                      </button>
                    )}
                    {groupBy !== "milestone" && allMilestones.length > 0 && (
                      <button onClick={() => setGroupBy("milestone")}>
                        По спринту
                      </button>
                    )}
                    <button onClick={() => setFiltersOpen(true)}>
                      Выбрать фильтр
                    </button>
                  </span>
                  <button
                    className="cb-close"
                    onClick={() => dismissBanner(`overload:${scopeKey}`)}
                    title="Скрыть предупреждение"
                    aria-label="Скрыть предупреждение"
                  >
                    ✕
                  </button>
                </div>
              )}

              {/* D2: видимая плашка «активные за N дней» — НЕ молчаливый дефолт */}
              {daysActive > 0 ? (
                <div className="cv-banner days">
                  <span className="cb-ic">🕘</span>
                  <span className="cb-text">
                    Показаны активные за {daysActive} дн. ({visibleIds.size} из{" "}
                    {data.meta.issue_count})
                  </span>
                  <button
                    className="cb-action"
                    onClick={() => setDaysActive(0)}
                    title="Снять ограничение по дате обновления"
                  >
                    показать все
                  </button>
                </div>
              ) : (
                overloaded &&
                !overloadDismissed && (
                  <div className="cv-banner days hint">
                    <span className="cb-ic">🕘</span>
                    <span className="cb-text">
                      Или показать только недавно активные:
                    </span>
                    <span className="cb-cta">
                      <button onClick={() => setDaysActive(30)}>30 дней</button>
                      <button onClick={() => setDaysActive(90)}>90 дней</button>
                    </span>
                  </div>
                )
              )}
            </div>
          )}

          {/* Этап 12 (D8): режим «Доска» — HTML-колонки вместо ReactFlow-холста.
              Карточки берём из data.nodes (там labels/state/assignees/due/iid).
              Клик по карточке открывает ту же правую панель (selectedId). */}
          {/* Item 5: доска уважает ТЕ ЖЕ фильтры, что и граф — отдаём в BoardView
              только прошедшие visibleIds ноды (поиск + панель «Фильтры» +
              «активные за N дней»). Счётчики колонок считаются по отфильтрованному
              набору. Логика «поиск-как-навигация» — только в граф-режиме, её не
              трогаем. */}
          {boardMode && data ? (
            <BoardView
              nodes={data.nodes.filter((n) => visibleIds.has(n.id))}
              boards={boardsData}
              boardsLoading={boardsLoading}
              graphLoading={loading}
              hasData={!!data}
              selectedId={selectedId}
              onSelect={(id) => {
                setSelectedId(id);
                setCreating(false);
                setSidebarOpen(true);
              }}
              applyPatched={applyPatched}
              setToast={setToast}
              allLabels={data.meta.labels}
              scopeLabel={scope?.label}
            />
          ) : boardMode ? null : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onInit={(inst) => {
              rfRef.current = inst;
            }}
            onNodesChange={onNodesChange}
            onNodeDragStart={onNodeDragStart}
            onNodeDragStop={onNodeDragStop}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onEdgeClick={onEdgeClick}
            nodeTypes={nodeTypes}
            onNodeClick={(_, n) => {
              // режим «связать с…»: клик по подсвеченному кандидату создаёт связь
              if (linkingFrom) {
                if (n.id !== linkingFrom && linkMatches?.has(n.id)) {
                  setPendingLink({ source: linkingFrom, target: n.id });
                  setLinkingFrom(null);
                  setLinkQuery("");
                }
                return;
              }
              setSelectedId(n.id);
              setCreating(false);
              setSidebarOpen(true);
            }}
            onPaneClick={() => {
              setSelectedId(null);
              setLinkingFrom(null);
            }}
            onSelectionChange={onSelectionChange}
            fitView
            fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
            minZoom={0.2}
            maxZoom={2}
            onlyRenderVisibleElements
            connectionMode={ConnectionMode.Loose}
            connectionRadius={75}
            proOptions={{ hideAttribution: true }}
            // Этап 7: режим холста зависит от инструмента левой панели.
            //  select — обычный (панорама ЛКМ, ноды двигаются);
            //  pan — «рука» (только панорама, без выделения/перетаскивания нод);
            //  zoomArea — лупа: рамкой выделяем, по отпусканию зумим (panOnDrag off);
            //  lasso — выделение пачкой рамкой (panOnDrag off, selectionOnDrag).
            panOnDrag={tool === "select" || tool === "pan"}
            selectionOnDrag={tool === "zoomArea" || tool === "lasso"}
            selectionMode={SelectionMode.Partial}
            nodesDraggable={tool === "select"}
            nodesConnectable={tool === "select"}
            elementsSelectable={tool !== "pan"}
          >
            <Background
              gap={18}
              size={1.5}
              color={theme === "dark" ? "#2c3346" : "#b9c0d4"}
            />
            {/* Этап 7: левая панель — курсор/камера + команды холста */}
            <LeftToolbar
              tool={tool}
              setTool={setTool}
              onFocusNeighbors={focusNeighbors}
              canFocus={!!selectedId}
              onAddNode={addNodeOnCanvas}
              canAddNode={!!data}
              showMiniMap={showMiniMap}
              onToggleMiniMap={() => setShowMiniMap((v) => !v)}
            />
            <ZoomControl />
            {showMiniMap && (
              <MiniMap
                pannable
                zoomable
                nodeStrokeWidth={2}
                nodeColor={(n) =>
                  nodeAccent(n.data as GraphData["nodes"][number])
                }
              />
            )}
            {/* Этап 7: лассо → плавающая панель массовых действий */}
            {selectedIds.length > 1 && (
              <BulkBar
                count={selectedIds.length}
                busy={bulkBusy}
                milestones={allMilestones}
                assignees={assigneeOptions}
                onSetMilestone={(id) =>
                  runBulk({ milestone_id: id }, "Сменён спринт")
                }
                onSetAssignee={(uid) =>
                  runBulk(
                    { assignee_ids: uid ? [uid] : [] },
                    "Сменён исполнитель"
                  )
                }
                onClose={() =>
                  runBulk({ state_event: "close" }, "Закрыто")
                }
                onClear={() => {
                  setSelectedIds([]);
                  // снимаем штатное выделение RF (n.selected) у всех нод
                  setNodes((ns) =>
                    ns.map((n) =>
                      n.selected ? { ...n, selected: false } : n
                    )
                  );
                  setTool("select");
                }}
              />
            )}
            {grouping && (
              <ColumnHeaders
                headers={headerData}
                dim={groupDimLabel}
                wipLimit={wipLimit}
                onToggle={toggleColumn}
              />
            )}
            {focus && (
              <Panel position="top-center">
                <div className="focus-banner">
                  <span>★ Фокус:</span>
                  <b>{data?.nodes.find((n) => n.id === focus.center)?.title}</b>
                  <span className="fb-hint">
                    — клик по задаче меняет центр
                  </span>
                </div>
              </Panel>
            )}
          </ReactFlow>
          )}

          {/* Нижний ЦЕНТРАЛЬНЫЙ док холста: сегментный переключатель «Граф | Доска»
              + рядом кнопки раскладки графа. Виден в ОБОИХ режимах (вне
              ReactFlow/BoardView); раскладка — только в режиме «Граф». */}
          <div className="canvas-dock">
            <div className="mode-toggle" role="group" aria-label="Режим отображения">
              <button
                className={`mt-seg ${!boardMode ? "on" : ""}`}
                onClick={() => {
                  setSelectedId(null);
                  setBoardMode(false);
                }}
                title="Граф / холст зависимостей"
              >
                <span className="mt-ic">◆</span> Граф
              </button>
              <button
                className={`mt-seg ${boardMode ? "on" : ""}`}
                onClick={() => {
                  setSelectedId(null);
                  setBoardMode(true);
                }}
                title="Режим «Доска» (GitLab-style, импорт из GitLab)"
              >
                <span className="mt-ic">▦</span> Доска
              </button>
            </div>

            {/* кнопки раскладки графа — РЯДОМ с тумблером (общий док по центру внизу);
                только режим «Граф» и вид «граф зависимостей»; сброс — при пинах */}
            {!boardMode && (groupBy === "none" || hasPins) && (
              <div className="layout-dock">
                {groupBy === "none" &&
                  (
                    [
                      { k: "td", t: "Сверху вниз", icon: "↓" },
                      { k: "lr", t: "Слева направо", icon: "→" },
                      { k: "radial", t: "Радиальная (звезда)", icon: "✸" },
                    ] as const
                  ).map((o) => (
                    <button
                      key={o.k}
                      className={graphLayout === o.k ? "on" : ""}
                      title={
                        o.k === "radial"
                          ? "Радиальная: выбранная задача в центре, связанные — вокруг"
                          : o.t
                      }
                      onClick={() => setGraphLayout(o.k)}
                    >
                      <span className="ld-ic">{o.icon}</span>
                      {o.t}
                    </button>
                  ))}
                {hasPins && (
                  <>
                    {groupBy === "none" && <span className="ld-sep" />}
                    <button
                      className="ld-reset"
                      title="Сбросить ручные позиции и вернуть авто-раскладку"
                      onClick={resetLayout}
                    >
                      <span className="ld-ic">⟲</span> Авто-раскладка
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        {!sidebarOpen && (
          <button
            className="sb-reopen"
            title="Показать панель"
            onClick={() => setSidebarOpen(true)}
          >
            ‹
          </button>
        )}
        {sidebarOpen && (
          <>
            <div
              className="sb-resizer"
              onMouseDown={onResizeStart}
              title="Потяните, чтобы изменить ширину"
            />
            <div className="sidebar-wrap" style={{ width: sidebarW }}>
              <button
                className="sb-collapse"
                title="Скрыть панель"
                onClick={() => setSidebarOpen(false)}
              >
                ›
              </button>
              <Sidebar
                creating={creating}
                parent={parentNode}
                extraLabels={data?.meta.labels ?? []}
                assigneeOptions={assigneeOptions}
                milestoneOptions={allMilestones}
                onCloseCreate={() => {
                  setCreating(false);
                  setParentNode(null);
                }}
                onCreateChild={(n) => {
                  setParentNode(n);
                  setCreating(true);
                }}
                node={selectedNode}
                detail={detail}
                meta={meta}
                caps={caps}
                scope={scope}
                onChangeSprint={changeSprint}
                onChangeAssignee={changeAssignee}
                onChangeAssignees={changeAssignees}
                onChangeLabels={changeLabels}
                onChangeDescription={changeDescription}
                onChangeDue={changeDue}
                onToggleState={toggleState}
                botConfigured={botConfigured}
                onPoke={pokeAssignee}
                relatedItems={relatedItems}
                onOpenNode={(id) => {
                  setSelectedId(id);
                  setCreating(false);
                  setSidebarOpen(true);
                }}
                onFocusNode={focusNode}
                linkingActive={linkingFrom !== null && linkingFrom === selectedId}
                linkQuery={linkQuery}
                onStartLink={() => {
                  if (selectedNode) {
                    setLinkingFrom(selectedNode.id);
                    setLinkQuery("");
                  }
                }}
                onLinkQuery={setLinkQuery}
                onCancelLink={() => {
                  setLinkingFrom(null);
                  setLinkQuery("");
                }}
                onCreated={handleCreated}
              />
            </div>
          </>
        )}
      </div>

      {!appEnabled ? (
        <div className="modal-backdrop login-backdrop">
          <div className="login-card">
            <div className="login-logo">⏻</div>
            <h1>Приложение отключено</h1>
            <p className="modal-sub">
              Администратор временно отключил Issue Graph. Загляните позже.
            </p>
            {adminEnabled && (
              <a className="login-btn" href="#admin">
                Открыть админку
              </a>
            )}
          </div>
        </div>
      ) : (
        !loggedIn && (
          <LoginGate
            instanceReady={instanceReady}
            oauthAvailable={oauthAvailable}
            adminEnabled={adminEnabled}
            onHelp={() => setShowHelp(true)}
          />
        )
      )}

      {showHelp && <HelpModal onClose={() => setShowHelp(false)} boardMode={boardMode} />}

      {/* выбор типа после броска связи */}
      {pendingLink && (
        <div className="modal-backdrop" onClick={() => setPendingLink(null)}>
          <div className="linktype-pop" onClick={(e) => e.stopPropagation()}>
            <div className="lt-title">
              Тип связи:{" "}
              <b>#{pendingLink.source.split(":")[1]}</b> →{" "}
              <b>#{pendingLink.target.split(":")[1]}</b>
            </div>
            <div className="lt-opts">
              <button
                className="lt-blocks"
                onClick={() => {
                  linkNodes(pendingLink.source, pendingLink.target, "blocks");
                  setPendingLink(null);
                }}
              >
                ⛔ Блокирует <span>первая мешает второй</span>
              </button>
              <button
                className="lt-parent"
                onClick={() => {
                  linkNodes(pendingLink.source, pendingLink.target, "parent");
                  setPendingLink(null);
                }}
              >
                ⬇ Родитель → дочерняя <span>первая — родитель</span>
              </button>
              <button
                className="lt-relates"
                onClick={() => {
                  linkNodes(pendingLink.source, pendingLink.target, "relates");
                  setPendingLink(null);
                }}
              >
                🔗 Просто связана <span>без направления</span>
              </button>
            </div>
            <button className="lt-cancel" onClick={() => setPendingLink(null)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      {/* меню на ребре: сменить тип / удалить */}
      {edgeMenu && (
        <>
          <div className="ctx-backdrop" onClick={() => setEdgeMenu(null)} />
          <div className="ctx-menu" style={{ left: edgeMenu.x, top: edgeMenu.y }}>
            <div className="ctx-head">Связь</div>
            {(
              [
                ["blocks", "⛔ Блокирует"],
                ["parent", "⬇ Родитель → дочерняя"],
                ["relates", "🔗 Просто связана"],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => {
                  retypeEdge(edgeMenu.source, edgeMenu.target, k);
                  setEdgeMenu(null);
                }}
              >
                {label}
              </button>
            ))}
            <div className="ctx-sep" />
            <button className="ctx-del" onClick={deleteEdge}>
              ✕ Удалить связь
            </button>
          </div>
        </>
      )}

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

// ── справка: как всё работает ───────────────────────────────────────────────
function HelpModal(props: { onClose: () => void; boardMode: boolean }) {
  return (
    <div className="modal-backdrop" onClick={props.onClose}>
      <div className="modal modal-help" onClick={(e) => e.stopPropagation()}>
        <h2>Как это работает — {props.boardMode ? "Доска" : "Граф"}</h2>
        <p className="help-lead">
          {props.boardMode ? (
            <>
              Краткая шпаргалка по Kanban-доске. Колонки и карточки берутся из
              задач GitLab; всё, что меняешь здесь, сразу пишется в GitLab.
            </>
          ) : (
            <>
              Краткая шпаргалка по основным возможностям. Граф строится по задачам
              GitLab; всё, что меняешь здесь, сразу пишется в GitLab.
            </>
          )}
        </p>
        <div className="help-body">
          {props.boardMode ? (
            <>
              <section>
                <h3>Что такое «Доска»</h3>
                <p>
                  <b>Kanban-доска</b> по задачам GitLab. Колонки берутся из{" "}
                  <b>доски GitLab</b> выбранной области; если доски нет — колонки{" "}
                  <b>выводятся автоматически</b> из самых частых scoped-меток
                  (напр. <code>status::</code> / <code>priority::</code>), либо
                  добавляются вручную кнопкой <b>＋ Список</b>. На GitLab CE
                  групповые доски — функция EE, поэтому для группы доска может не
                  подтянуться (тогда показываются авто-колонки или{" "}
                  <b>Open | Closed</b>).
                </p>
              </section>
              <section>
                <h3>Колонки</h3>
                <p>
                  <b>«Open»</b> — открытые задачи без метки-списка; затем
                  label-колонки (по доске или меткам); <b>«Closed»</b> — закрытые.
                </p>
              </section>
              <section>
                <h3>Перетаскивание карточек</h3>
                <p>
                  Перетаскивание <b>сразу меняет данные задачи в GitLab</b>: в
                  label-колонку — ставит её метку и снимает прежнюю; в <b>«Open»</b>{" "}
                  — снимает метку колонки-источника; в <b>«Closed»</b> — закрывает
                  задачу; обратно из Closed — переоткрывает.
                </p>
              </section>
              <section>
                <h3>Scoped-метки</h3>
                <p>
                  Метки вида <code>pref::value</code> (напр.{" "}
                  <code>status::todo</code>) — <b>взаимоисключающие</b> внутри
                  группы: перенос ставит новую метку группы и снимает прежнюю (одна{" "}
                  <code>status::</code> за раз).
                </p>
              </section>
              <section>
                <h3>Несколько досок</h3>
                <p>
                  Если у области несколько досок GitLab — <b>селектор доски</b>{" "}
                  вверху панели доски.
                </p>
              </section>
              <section>
                <h3>Карточка</h3>
                <p>
                  <b>Клик</b> открывает панель справа — там правка{" "}
                  <b>меток / исполнителя / срока / состояния / описания</b> (как в
                  графе).
                </p>
              </section>
              <section>
                <h3>Если перенос «не сработал»</h3>
                <p>
                  Всплывает предупреждение — обычно это значит, что в GitLab есть{" "}
                  <b>две метки с одинаковым именем</b> (групповая и проектная);
                  снимите лишнюю вручную.
                </p>
              </section>
            </>
          ) : (
            <>
              <section>
                <h3>Область</h3>
                <p>
                  Слева вверху выбираешь <b>группу</b> (со всеми проектами) или
                  отдельный <b>проект</b> — граф строится по его задачам.
                </p>
              </section>
              <section>
                <h3>Граф / Доска</h3>
                <p>
                  Внизу по центру — сегментный переключатель{" "}
                  <b>«Граф | Доска»</b> (рядом — кнопки раскладки графа).
                </p>
              </section>
              <section>
                <h3>Вид / группировка</h3>
                <p>
                  <b>Граф зависимостей</b> — иерархическая раскладка по связям.
                  Остальные виды (<b>по спринту / метке / исполнителю / проекту /
                  статусу</b>) раскладывают задачи в колонки — удобно планировать.
                </p>
              </section>
              <section>
                <h3>Инструменты холста</h3>
                <p>
                  Слева внизу — выбор инструмента: <b>курсор</b> — выбор и
                  перетаскивание (<b>V</b>); <b>панорама</b> — таскать холст (
                  <b>H</b>); <b>лупа</b> — рамкой приблизить область (<b>Z</b>);{" "}
                  <b>лассо</b> — выделить задачи пачкой (<b>L</b>). Рядом:{" "}
                  <b>🎯</b> фокус на соседях выбранной, <b>＋</b> новая нода,{" "}
                  <b>🗺</b> миникарта. Внизу-слева — зум <b>«− % +»</b> и{" "}
                  <b>⤢</b> «вписать всё в экран».
                </p>
              </section>
              <section>
                <h3>Раскладка графа</h3>
                <p>
                  Внизу по центру — <b>сверху-вниз</b>, <b>слева-направо</b> или{" "}
                  <b>радиальная</b> (выбранная задача в центре, связанные вокруг).{" "}
                  <b>Клик</b> по карточке — открыть её, <b>перетащить тело</b> —
                  подвинуть (позиции сохраняются; кнопка <b>⟲ Авто-раскладка</b>{" "}
                  сбрасывает). <b>Esc</b> — снять выделение.
                </p>
              </section>
              <section>
                <h3>Связи</h3>
                <p>
                  <b>Создать:</b> наведи на карточку → у края появится
                  кнопка-стрелка <b>↗</b>, потяни от неё к другой карточке
                  (целиться в неё не надо — притянется) → выбери тип. <b>Типы:</b>{" "}
                  <b>⛔ Блокирует</b> (красная стрелка),{" "}
                  <b>⬇ Родитель → дочерняя</b> (фиолетовая стрелка),{" "}
                  <b>🔗 Связана</b> (серый пунктир, без направления).
                </p>
                <p>
                  Направление: задача у <b>начала</b> стрелки — главная/раньше (её
                  кладём <b>выше-левее</b>). <b>Сменить тип или удалить</b> — клик
                  по линии связи. На Free в GitLab всё хранится как «relates to», а
                  тип и направление (blocks/родитель) ведёт само приложение; на PRO
                  работают нативные blocks/epic.
                </p>
              </section>
              <section>
                <h3>Цвета нод</h3>
                <p>
                  Левый край — цвет первой метки.{" "}
                  <span className="hl-over">Красный фон</span> — просрочено,{" "}
                  <span className="hl-soon">жёлтый</span> — срок сегодня/завтра,
                  серая — закрыта. Бейдж со сроком — в шапке ноды; саму дату задаёшь
                  в панели справа (поле <b>«Срок»</b>).
                </p>
              </section>
              <section>
                <h3>Спринты</h3>
                <p>
                  Спринт = GitLab <b>milestone</b>. Создать новый — кнопка{" "}
                  <b>＋ спринт</b> в фильтрах. Назначить задаче — в её панели справа
                  или перетащив в нужную колонку (вид «по спринту»).
                </p>
              </section>
              <section>
                <h3>Создание</h3>
                <p>
                  <b>＋ Задача</b> — новая задача. На выбранной ноде —{" "}
                  <b>＋ Дочерняя задача</b> (создаст и свяжет). Исполнителя, спринт,
                  статус и описание меняешь в панели справа.
                </p>
              </section>
            </>
          )}
          <section>
            <h3>Фильтры</h3>
            <p>
              Кнопка <b>Фильтры</b> разворачивает панель (состояние / спринт /
              метка / исполнитель / даты); поиск и <b>«активные за N дней»</b> тоже
              фильтруют.{" "}
              {props.boardMode ? (
                <>
                  В режиме «Доска» фильтры тоже применяются — карточки скрываются,
                  счётчики колонок обновляются.
                </>
              ) : (
                <>
                  В графе вне фильтра задачи можно <b>скрывать</b> или{" "}
                  <b>затемнять</b> (переключатель).
                </>
              )}{" "}
              Цифра на кнопке — сколько фильтров активно.
            </p>
          </section>
          <section>
            <h3>Вход и подключение</h3>
            <p>
              Вход — <b>через GitLab OAuth</b> («Войти через GitLab»), видишь свои
              задачи. Настройки инстанса (host, OAuth, бот, тариф PRO, рубильник)
              задаёт администратор в админке (<code>#admin</code>). Приложение не
              хранит задачи — работает на API GitLab.
            </p>
          </section>
        </div>
        <div className="modal-actions">
          <button className="primary" onClick={props.onClose}>
            Понятно
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Этап 7: левая панель инструментов (курсор/камера) + команды холста ───────
function LeftToolbar(props: {
  tool: "select" | "pan" | "zoomArea" | "lasso";
  setTool: (t: "select" | "pan" | "zoomArea" | "lasso") => void;
  onFocusNeighbors: () => void;
  canFocus: boolean;
  onAddNode: () => void;
  canAddNode: boolean;
  showMiniMap: boolean;
  onToggleMiniMap: () => void;
}) {
  // Этап 7: SVG-иконки инструментов (вместо эмодзи) — курсор/панорама/лупа/лассо.
  const tools: {
    k: "select" | "pan" | "zoomArea" | "lasso";
    icon: ReactNode;
    t: string;
  }[] = [
    {
      k: "select",
      t: "Выбор / перетаскивание (V)",
      icon: (
        <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden>
          <path
            d="M3 2l8.6 4.7-3.7 1 2 3.8-1.6.8-2-3.8-2.6 2.8z"
            fill="currentColor"
          />
        </svg>
      ),
    },
    {
      k: "pan",
      t: "Панорама холста — перетаскивание (H)",
      icon: (
        <svg
          width="15"
          height="15"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M8 1.6v12.8M1.6 8h12.8" />
          <path d="M8 1.6 6.4 3.4M8 1.6l1.6 1.8M8 14.4l-1.6-1.8M8 14.4l1.6-1.8M1.6 8l1.8-1.6M1.6 8l1.8 1.6M14.4 8l-1.8-1.6M14.4 8l-1.8 1.6" />
        </svg>
      ),
    },
    {
      k: "zoomArea",
      t: "Лупа — рамкой приблизить область (Z)",
      icon: (
        <svg
          width="15"
          height="15"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          aria-hidden
        >
          <circle cx="7" cy="7" r="4.3" />
          <path d="M10.3 10.3 14 14M5.3 7h3.4M7 5.3v3.4" />
        </svg>
      ),
    },
    {
      k: "lasso",
      t: "Лассо — выделить задачи пачкой (L)",
      icon: (
        <svg
          width="15"
          height="15"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          aria-hidden
        >
          <ellipse cx="8" cy="6" rx="6" ry="4" strokeDasharray="2.2 1.8" />
          <path
            d="M4.7 9.5c-1 1-1.1 2.2-.3 3 .6.7.5 1.5-.3 2"
            strokeLinecap="round"
          />
          <circle cx="3.9" cy="14.4" r="1.05" fill="currentColor" stroke="none" />
        </svg>
      ),
    },
  ];
  return (
    <Panel position="top-left">
      <div className="left-toolbar">
        <div className="lt-group">
          {tools.map((o) => (
            <button
              key={o.k}
              className={props.tool === o.k ? "on" : ""}
              title={o.t}
              onClick={() => props.setTool(o.k)}
            >
              {o.icon}
            </button>
          ))}
        </div>
        <span className="lt-sep" />
        <div className="lt-group">
          <button
            title="Фокус на выбранной задаче и её связях"
            onClick={props.onFocusNeighbors}
            disabled={!props.canFocus}
          >
            🎯
          </button>
          <button
            title="Новая задача в центр холста"
            onClick={props.onAddNode}
            disabled={!props.canAddNode}
          >
            ＋
          </button>
          <button
            className={props.showMiniMap ? "on" : ""}
            title="Миникарта (вкл/выкл)"
            onClick={props.onToggleMiniMap}
          >
            🗺
          </button>
        </div>
      </div>
    </Panel>
  );
}

// ── Этап 7: плавающая панель массовых действий лассо ─────────────────────────
function BulkBar(props: {
  count: number;
  busy: boolean;
  milestones: { id: number; title: string }[];
  assignees: { username: string; id: number | null; name: string }[];
  onSetMilestone: (id: number | null) => void;
  onSetAssignee: (uid: number | null) => void;
  onClose: () => void;
  onClear: () => void;
}) {
  // приподнимаем над низ-центральным доком раскладки, чтобы не перекрывать его
  return (
    <Panel position="bottom-center" style={{ bottom: 56 }}>
      <div className="bulk-bar">
        <span className="bb-count">{props.count} выбрано</span>
        <span className="bb-sep" />
        <select
          className="bb-select"
          defaultValue=""
          disabled={props.busy}
          title="Назначить спринт выбранным"
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") return;
            props.onSetMilestone(v === "__none" ? null : Number(v));
            e.target.value = "";
          }}
        >
          <option value="" disabled>
            🏁 Спринт…
          </option>
          <option value="__none">— без спринта</option>
          {props.milestones.map((m) => (
            <option key={m.id} value={m.id}>
              {m.title}
            </option>
          ))}
        </select>
        <select
          className="bb-select"
          defaultValue=""
          disabled={props.busy}
          title="Назначить исполнителя выбранным"
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") return;
            const a = props.assignees.find((x) => x.username === v);
            props.onSetAssignee(v === "__none" ? null : a?.id ?? null);
            e.target.value = "";
          }}
        >
          <option value="" disabled>
            👤 Исполнитель…
          </option>
          <option value="__none">— снять исполнителя</option>
          {props.assignees.map((a) => (
            <option key={a.username} value={a.username}>
              {a.name}
            </option>
          ))}
        </select>
        <button
          className="bb-close"
          disabled={props.busy}
          title="Закрыть выбранные задачи"
          onClick={props.onClose}
        >
          ✓ Закрыть
        </button>
        <span className="bb-sep" />
        <button
          className="bb-clear"
          disabled={props.busy}
          title="Снять выделение"
          onClick={props.onClear}
        >
          ✕
        </button>
        {props.busy && <span className="bb-spin" />}
      </div>
    </Panel>
  );
}

// ── заголовки колонок: прилипают к верху, едут/масштабируются с графом ───────
// Шапки колонок (Kanban). Этап 8: клик по шапке сворачивает/разворачивает колонку
// (свёрнутая — узкая полоса с вертикальным заголовком), счётчик подсвечивается при
// превышении WIP-лимита. Координаты следуют за вьюпортом (зум/панорама).
function ColumnHeaders({
  headers,
  dim,
  wipLimit,
  onToggle,
}: {
  headers: {
    key: string;
    title: string;
    x: number;
    count: number;
    collapsed: boolean;
    over: boolean;
  }[];
  dim: string;
  wipLimit: number;
  onToggle: (key: string) => void;
}) {
  const vp = useViewport();
  void dim; // Этап 12 (D11): подпись доски убрана с холста; колонки — просмотр
  return (
    <div className="col-headers">
      {headers.map((h) => (
        <div
          key={h.key}
          className={`col-header${h.collapsed ? " collapsed" : ""}${
            h.over ? " over-wip" : ""
          }`}
          style={{
            left: vp.x + h.x * vp.zoom,
            // ширину капим до «шаг колонки − зазор», чтобы шапки НЕ слипались
            // на отдалении (шаг = COL_W у обычной, COLLAPSED_W у свёрнутой).
            width: Math.max(
              8,
              Math.min(
                (h.collapsed ? COLLAPSED_W : NODE_W) * vp.zoom,
                (h.collapsed ? COLLAPSED_W : COL_W) * vp.zoom - 8
              )
            ),
          }}
          onClick={() => onToggle(h.key)}
          title={
            h.collapsed
              ? `${h.title} (${h.count}) — развернуть`
              : `${h.title} (${h.count}) — свернуть колонку` +
                (h.over ? ` · превышен WIP-лимит ${wipLimit}` : "")
          }
        >
          <span className="ch-title">{h.title}</span>
          <span className={`ch-count${h.over ? " over" : ""}`}>{h.count}</span>
        </div>
      ))}
    </div>
  );
}

// Этап 12 (D11): компонент дорожек KanbanLanes удалён (откат Этапа 11). Холст —
// чистый просмотр; настоящая Kanban теперь в режиме «Доска» (Board.tsx).

// ── контрол масштаба (как в Figma/Miro): − [%] + ⤢ ───────────────────────────
// Этап 7 (консолидация контролов): низ-лево = инкрементальный зум + «вписать всё
// в экран» (Fit) рядом — привычное место для навигации по холсту.
function ZoomControl() {
  const { zoom } = useViewport(); // реактивно — процент обновляется и при зуме колёсиком
  const { zoomIn, zoomOut, zoomTo, fitView } = useReactFlow();
  const pct = Math.round(zoom * 100);
  return (
    <Panel position="bottom-left">
      <div className="zoom-ctl">
        <button onClick={() => zoomOut({ duration: 150 })} title="Отдалить">
          −
        </button>
        <button
          className="zoom-pct"
          onClick={() => zoomTo(1, { duration: 150 })}
          title="Сбросить к 100%"
        >
          {pct}%
        </button>
        <button onClick={() => zoomIn({ duration: 150 })} title="Приблизить">
          +
        </button>
        <span className="zoom-sep" />
        <button
          className="zoom-fit"
          onClick={() => fitView({ duration: 300, padding: 0.15, maxZoom: 1 })}
          title="Вписать всё в экран"
        >
          ⤢
        </button>
      </div>
    </Panel>
  );
}

// ── Этап 6: паритет с GitLab — подкомпоненты карточки ───────────────────────

// human-метки health_status GitLab
const HEALTH_LABEL: Record<string, string> = {
  on_track: "🟢 в графике",
  needs_attention: "🟡 нужно внимание",
  at_risk: "🔴 под угрозой",
};

// MR-статус → бейдж
const MR_STATE_LABEL: Record<string, string> = {
  opened: "открыт",
  closed: "закрыт",
  merged: "влит",
  locked: "заблокирован",
};

// секунды → «2ч 30м» (трекинг времени)
function fmtSeconds(sec: number): string {
  if (!sec) return "0";
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return [h ? `${h}ч` : "", m ? `${m}м` : ""].filter(Boolean).join(" ") || "0";
}

// Инлайн-метки: чипы с ✕ + «＋ метка» из меток области. add/remove — инкрементально.
function LabelEditor(props: {
  labels: Label[];
  options: Label[];
  onChange: (add: string[], remove: string[]) => void;
}) {
  const [adding, setAdding] = useState(false);
  const have = new Set(props.labels.map((l) => l.name));
  const candidates = props.options.filter((l) => !have.has(l.name));
  return (
    <div className="sb-labels editable">
      {props.labels.map((l) => (
        <span
          key={l.name}
          className="chip"
          style={{ background: l.color ?? "#888", color: readableText(l.color) }}
        >
          {l.name}
          <button
            className="chip-x"
            title="Снять метку"
            onClick={() => props.onChange([], [l.name])}
          >
            ✕
          </button>
        </span>
      ))}
      {!adding ? (
        <button
          className="chip add"
          title="Добавить метку"
          onClick={() => setAdding(true)}
          disabled={candidates.length === 0}
        >
          ＋ метка
        </button>
      ) : (
        <select
          autoFocus
          className="chip-add-select"
          defaultValue=""
          onChange={(e) => {
            if (e.target.value) props.onChange([e.target.value], []);
            setAdding(false);
          }}
          onBlur={() => setAdding(false)}
        >
          <option value="">— выбрать метку —</option>
          {candidates.map((l) => (
            <option key={l.name} value={l.name}>
              {l.name}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

// Исполнители: стопка-список + добавление. На multi=true набор любого размера,
// иначе (Free) — одиночный выбор (деградация по caps).
function AssigneeEditor(props: {
  assignees: Assignee[];
  options: { id: number | null; username: string; name: string }[];
  multi: boolean;
  onChange: (ids: number[]) => void;
  onChangeOne: (id: number | null) => void;
}) {
  const [adding, setAdding] = useState(false);
  const currentIds = props.assignees
    .map((a) => a.id)
    .filter((x): x is number => x != null);
  const have = new Set(currentIds);
  const candidates = props.options.filter((u) => u.id && !have.has(u.id));

  if (!props.multi) {
    // Free: один исполнитель — прежний одиночный select
    return (
      <div className="sb-row">
        <label>Исполнитель</label>
        <select
          value={props.assignees[0]?.username ?? ""}
          onChange={(e) => {
            const u = props.options.find((m) => m.username === e.target.value);
            props.onChangeOne(u && u.id ? u.id : null);
          }}
        >
          <option value="">— никто —</option>
          {props.options.map((u) => (
            <option key={u.username} value={u.username}>
              {u.name}
            </option>
          ))}
        </select>
      </div>
    );
  }

  return (
    <div className="sb-assignees">
      <label>Исполнители</label>
      <div className="sb-assignee-list">
        {props.assignees.map((a) => (
          <span key={a.username ?? a.id} className="sb-assignee-chip">
            <span className="ava" title={a.name ?? ""}>
              {(a.name ?? "?")
                .split(" ")
                .map((s) => s[0])
                .join("")
                .slice(0, 2)}
            </span>
            {a.name}
            <button
              className="chip-x"
              title="Снять исполнителя"
              onClick={() =>
                props.onChange(currentIds.filter((id) => id !== a.id))
              }
            >
              ✕
            </button>
          </span>
        ))}
        {props.assignees.length === 0 && (
          <span className="sb-assignee-none">— никто —</span>
        )}
        {!adding ? (
          <button
            className="chip add"
            title="Добавить исполнителя"
            onClick={() => setAdding(true)}
            disabled={candidates.length === 0}
          >
            ＋
          </button>
        ) : (
          <select
            autoFocus
            className="chip-add-select"
            defaultValue=""
            onChange={(e) => {
              if (e.target.value) props.onChange([...currentIds, Number(e.target.value)]);
              setAdding(false);
            }}
            onBlur={() => setAdding(false)}
          >
            <option value="">— выбрать —</option>
            {candidates.map((u) => (
              <option key={u.username} value={String(u.id)}>
                {u.name}
              </option>
            ))}
          </select>
        )}
      </div>
    </div>
  );
}

// Описание (markdown): просмотр (react-markdown) ↔ редактирование (textarea → PUT).
function Description(props: {
  detail: IssueDetail | null;
  onSave: (text: string) => Promise<void> | void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const desc = props.detail?.description ?? "";

  const startEdit = () => {
    setText(desc);
    setEditing(true);
  };
  const save = async () => {
    setBusy(true);
    try {
      await props.onSave(text);
      setEditing(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="sb-desc">
      <div className="sb-desc-head">
        <label>Описание</label>
        {!editing && props.detail && (
          <button className="sb-desc-edit" title="Редактировать" onClick={startEdit}>
            ✎
          </button>
        )}
      </div>
      {editing ? (
        <div className="sb-desc-edit-box">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={6}
            placeholder="Описание (markdown)…"
          />
          <div className="sb-desc-actions">
            <button className="primary" disabled={busy} onClick={save}>
              {busy ? "…" : "Сохранить"}
            </button>
            <button disabled={busy} onClick={() => setEditing(false)}>
              Отмена
            </button>
          </div>
        </div>
      ) : props.detail === null ? (
        <div className="sb-desc-loading">загрузка…</div>
      ) : desc.trim() ? (
        <Markdown source={desc} />
      ) : (
        <div className="sb-desc-empty">нет описания</div>
      )}
    </div>
  );
}

// Доп. метаданные: вес, трекинг времени, start_date. Показываем лишь заполненное.
function IssueMeta(props: { detail: IssueDetail }) {
  const { detail } = props;
  const ts = detail.time_stats;
  const rows: { label: string; value: string }[] = [];
  if (detail.weight != null) rows.push({ label: "Вес", value: String(detail.weight) });
  if (ts.time_estimate || ts.total_time_spent) {
    rows.push({
      label: "Время",
      value: `${ts.human_total_time_spent ?? fmtSeconds(ts.total_time_spent)} / ${
        ts.human_time_estimate ?? fmtSeconds(ts.time_estimate)
      }`,
    });
  }
  if (detail.start_date) rows.push({ label: "Старт", value: detail.start_date });
  if (rows.length === 0) return null;
  return (
    <div className="sb-meta">
      {rows.map((r) => (
        <div key={r.label} className="sb-meta-row">
          <span className="sb-meta-label">{r.label}</span>
          <span className="sb-meta-value">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

// Связанные / закрывающие MR: статус + ссылка в GitLab.
function RelatedMRs(props: { mrs: IssueDetail["merge_requests"] }) {
  return (
    <div className="sb-mrs">
      <div className="sb-mrs-head">Связанные MR ({props.mrs.length})</div>
      {props.mrs.map((mr) => (
        <a
          key={mr.id}
          className="sb-mr"
          href={mr.web_url ?? "#"}
          target="_blank"
          rel="noreferrer"
          title={mr.closes ? "Закроет задачу при мердже" : "Связанный merge request"}
        >
          <span className={`mr-state ${mr.state ?? ""}`}>
            {MR_STATE_LABEL[mr.state ?? ""] ?? mr.state}
          </span>
          <span className="mr-title">{mr.title}</span>
          {mr.closes && <span className="mr-closes" title="Закрывает задачу">✓</span>}
        </a>
      ))}
    </div>
  );
}

// ── боковая панель: детали ноды + форма создания ────────────────────────────
function Sidebar(props: {
  creating: boolean;
  parent: GraphData["nodes"][number] | null;
  extraLabels: { name: string; color: string | null }[];
  assigneeOptions: { id: number | null; username: string; name: string }[];
  milestoneOptions: { id: number; title: string }[];
  onCloseCreate: () => void;
  onCreateChild: (n: GraphData["nodes"][number]) => void;
  node: GraphData["nodes"][number] | null;
  detail: IssueDetail | null;
  meta: ScopeMeta | null;
  caps: Capabilities | null;
  scope: Scope | null;
  onChangeSprint: (id: number | null) => void;
  onChangeAssignee: (userId: number | null) => void;
  onChangeAssignees: (userIds: number[]) => void;
  onChangeLabels: (add: string[], remove: string[]) => void;
  onChangeDescription: (text: string) => Promise<void> | void;
  onChangeDue: (date: string | null) => void;
  onToggleState: () => void;
  botConfigured: boolean;
  onPoke: (message?: string) => void;
  // Этап 9 (D4): прямые связи выбранной задачи + открыть/сфокусировать другую ноду
  relatedItems: RelatedItem[];
  onOpenNode: (id: string) => void;
  onFocusNode: (id: string) => void;
  linkingActive: boolean;
  linkQuery: string;
  onStartLink: () => void;
  onLinkQuery: (q: string) => void;
  onCancelLink: () => void;
  onCreated: (created: { id: string; iid: number; project_id: number }) => void;
}) {
  const { creating, node, meta } = props;
  const [showSummary, setShowSummary] = useState(false);
  // Этап 9 (D4): вкладки панели — «Детали» / «Связанные (N)»
  const [tab, setTab] = useState<"details" | "related">("details");
  const relatedN = props.relatedItems.length;
  // при смене выбранной задачи возвращаемся на «Детали»
  useEffect(() => {
    setTab("details");
  }, [node?.id]);

  if (creating && meta)
    return (
      <CreateForm
        meta={meta}
        parent={props.parent}
        caps={props.caps}
        extraLabels={props.extraLabels}
        assigneeOptions={props.assigneeOptions}
        milestoneOptions={props.milestoneOptions}
        onCreated={props.onCreated}
        onCancel={props.onCloseCreate}
      />
    );

  if (!node)
    return (
      <aside className="sidebar empty">
        <p>Выберите задачу на графе, чтобы увидеть детали.</p>
        <ul className="legend">
          <li>
            <span className="ln relates" /> relates_to
          </li>
          <li>
            <span className="ln blocks" /> blocks {props.caps && !props.caps.blocking_links && "(Premium)"}
          </li>
          <li>
            <span className="ln epic" /> epic {props.caps && !props.caps.epics && "(Premium)"}
          </li>
        </ul>
        <p className="hint">
          Потяните от точки на краю одной ноды к другой — создастся связь.
          Клик по линии связи — удалить её.
        </p>
      </aside>
    );

  return (
    <aside className="sidebar">
      <div className="sb-ref">
        {node.type === "epic" ? "EPIC" : `#${node.iid}`} ·{" "}
        <span className={node.state === "closed" ? "st closed" : "st open"}>
          {node.state}
        </span>
        {node.web_url && (
          <a
            className="sb-gitlab"
            href={node.web_url}
            target="_blank"
            rel="noreferrer"
            title="Открыть задачу в GitLab"
          >
            ↗ GitLab
          </a>
        )}
      </div>
      <h2>{node.title}</h2>

      {/* Этап 9 (D4): вкладки «Детали» / «Связанные (N)» */}
      <div className="sb-tabs" role="tablist">
        <button
          role="tab"
          className={`sb-tab ${tab === "details" ? "on" : ""}`}
          aria-selected={tab === "details"}
          onClick={() => setTab("details")}
        >
          Детали
        </button>
        <button
          role="tab"
          className={`sb-tab ${tab === "related" ? "on" : ""}`}
          aria-selected={tab === "related"}
          disabled={relatedN === 0}
          onClick={() => setTab("related")}
        >
          Связанные{relatedN > 0 ? ` (${relatedN})` : ""}
        </button>
      </div>

      {tab === "related" ? (
        <RelatedTab
          items={props.relatedItems}
          onOpen={props.onOpenNode}
          onFocus={props.onFocusNode}
        />
      ) : (
        <>

      {/* Этап 6: бейджи confidential / health_status (если инстанс отдаёт) */}
      {(props.detail?.confidential || props.detail?.health_status) && (
        <div className="sb-badges">
          {props.detail?.confidential && (
            <span className="sb-badge confidential" title="Конфиденциальная задача">
              🔒 конфиденциально
            </span>
          )}
          {props.detail?.health_status && (
            <span
              className={`sb-badge health ${props.detail.health_status}`}
              title="Состояние (health status)"
            >
              {HEALTH_LABEL[props.detail.health_status] ??
                props.detail.health_status}
            </span>
          )}
        </div>
      )}

      {/* Этап 6: инлайн-метки — чипы с ✕ и «＋ метка» (read-only для эпиков) */}
      {node.type === "issue" && node.project_id ? (
        <LabelEditor
          labels={node.labels}
          options={meta?.labels ?? []}
          onChange={props.onChangeLabels}
        />
      ) : (
        node.labels.length > 0 && (
          <div className="sb-labels">
            {node.labels.map((l) => (
              <span
                key={l.name}
                className="chip"
                style={{ background: l.color ?? "#888", color: readableText(l.color) }}
              >
                {l.name}
              </span>
            ))}
          </div>
        )
      )}

      {/* Этап 6: описание (markdown) — просмотр и редактирование. */}
      {node.type === "issue" && node.project_id && (
        <Description
          key={node.id}
          detail={props.detail}
          onSave={props.onChangeDescription}
        />
      )}

      {/* Этап 6: несколько исполнителей — стопка + мультивыбор. На Free тариф
          разрешает одного исполнителя → деградируем UI до одиночного выбора. */}
      <AssigneeEditor
        assignees={node.assignees}
        options={props.assigneeOptions}
        multi={!props.caps || props.caps.tier !== "free"}
        onChange={props.onChangeAssignees}
        onChangeOne={props.onChangeAssignee}
      />

      {/* Этап 9 (D5/D6): «🔔 Напомнить» рядом с исполнителем — с подтверждением */}
      {props.botConfigured && node.assignees.length > 0 && (
        <PokeBox
          username={node.assignees[0]?.username ?? node.assignees[0]?.name ?? ""}
          onPoke={props.onPoke}
        />
      )}

      <div className="sb-row">
        <label>Спринт</label>
        <select
          value={node.milestone ? String(node.milestone.id) : ""}
          onChange={(e) =>
            props.onChangeSprint(e.target.value ? Number(e.target.value) : null)
          }
        >
          <option value="">— без спринта —</option>
          {props.milestoneOptions.map((m) => (
            <option key={m.id} value={String(m.id)}>
              {m.title}
            </option>
          ))}
        </select>
      </div>

      <div className="sb-row">
        <label>Срок</label>
        <div className="sb-due">
          <input
            type="date"
            value={node.due_date ?? ""}
            onChange={(e) => props.onChangeDue(e.target.value || null)}
          />
          {node.due_date && (
            <button
              className="sb-due-clear"
              title="Снять срок"
              onClick={() => props.onChangeDue(null)}
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Этап 6: вес / трекинг времени / start_date — только просмотр (вес и на
          графе тянется). Показываем лишь то, что инстанс реально отдал. */}
      {props.detail && (
        <IssueMeta detail={props.detail} />
      )}

      {/* Этап 6: связанные / закрывающие MR */}
      {props.detail && props.detail.merge_requests.length > 0 && (
        <RelatedMRs mrs={props.detail.merge_requests} />
      )}

      <div className="sb-icontools">
        {node.type === "issue" && node.project_id && (
          <button
            className="sb-icon accent"
            title="Создать дочернюю задачу (создаст и свяжет)"
            onClick={() => props.onCreateChild(node)}
          >
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden>
              <path d="M10 5v10M5 10h10" stroke="currentColor" strokeWidth="2"
                fill="none" strokeLinecap="round" />
            </svg>
          </button>
        )}
        <button
          className="sb-icon"
          title={node.state === "closed" ? "Переоткрыть задачу" : "Закрыть задачу"}
          onClick={props.onToggleState}
        >
          {node.state === "closed" ? (
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden>
              <path d="M16 10a6 6 0 11-1.9-4.4M16 4v3.2h-3.2" stroke="currentColor"
                strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          ) : (
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden>
              <path d="M4.5 10.5l3.5 3.5 7.5-8.5" stroke="currentColor" strokeWidth="2"
                fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
        </button>
        {node.web_url && (
          <a
            className="sb-icon"
            href={node.web_url}
            target="_blank"
            rel="noreferrer"
            title="Открыть в GitLab"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden>
              <path d="M8 4.5H4v11.5h11.5v-4" stroke="currentColor"
                strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M12 4.5h4v4M16 4.5L9.5 11" stroke="currentColor"
                strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </a>
        )}
        <button
          className={`sb-icon ${showSummary ? "on" : ""}`}
          title="Что значат поля и иконки карточки"
          onClick={() => setShowSummary((v) => !v)}
        >
          <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden>
            <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.6" fill="none" />
            <path d="M8.2 7.8a1.8 1.8 0 113 1.4c-.7.5-1.2.9-1.2 1.8M10 14h.01"
              stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>

      {showSummary && (
        <div className="modal-backdrop" onClick={() => setShowSummary(false)}>
          <div className="modal card-summary-modal" onClick={(e) => e.stopPropagation()}>
            <button
              className="modal-x"
              onClick={() => setShowSummary(false)}
              title="Закрыть"
            >
              ✕
            </button>
            <h2>Сводка по карточке</h2>
            <ul className="cs-list">
              <li><b>＋</b> — дочерняя задача (создаст и свяжет с этой)</li>
              <li><b>✓ / ↻</b> — закрыть / переоткрыть задачу</li>
              <li><b>🔔 Напомнить</b> — напоминание исполнителю в Matrix (у поля «Исполнитель»)</li>
              <li><b>↗</b> — открыть задачу в GitLab</li>
              <li>Цветная точка у заголовка — статус (открыта / закрыта)</li>
              <li>Метки — цветные чипы; срок — бейдж и фон ноды на графе</li>
              <li><b>Исполнитель / Спринт</b> — меняются прямо здесь</li>
              <li><b>«Связать с…»</b> — поиск задачи и связь кликом на графе</li>
            </ul>
            <div className="modal-actions">
              <span className="ma-spacer" />
              <button className="primary" onClick={() => setShowSummary(false)}>
                Понятно
              </button>
            </div>
          </div>
        </div>
      )}

      {node.type === "issue" && node.project_id && (
        <div className="link-with">
          {!props.linkingActive ? (
            <button className="link-start" onClick={props.onStartLink}>
              🔗 Связать с…
            </button>
          ) : (
            <div className="linking-box">
              <input
                autoFocus
                placeholder="поиск задачи для связи…"
                value={props.linkQuery}
                onChange={(e) => props.onLinkQuery(e.target.value)}
              />
              <p className="hint">
                Подходящие задачи подсвечены на графе — кликните нужную. Esc —
                отмена.
              </p>
              <button onClick={props.onCancelLink}>Отмена</button>
            </div>
          )}
        </div>
      )}

      {node.type === "issue" && node.project_id && (
        <Comments projectId={node.project_id} iid={node.iid} />
      )}
        </>
      )}
    </aside>
  );
}

// Этап 9 (D4): вкладка «Связанные» — прямые связи выбранной задачи. Клик по
// строке открывает ту задачу в этой же панели; «⌖ Фокус» наводит камеру, не
// меняя выбор. Иконка/метка типа берётся из RELATED_KIND_LABEL.
function RelatedTab(props: {
  items: RelatedItem[];
  onOpen: (id: string) => void;
  onFocus: (id: string) => void;
}) {
  if (props.items.length === 0)
    return <p className="sb-related-empty">Нет связей.</p>;
  return (
    <ul className="sb-related">
      {props.items.map(({ node, kind }) => {
        const k = RELATED_KIND_LABEL[kind];
        return (
          <li key={`${node.id}|${kind}`} className="sb-related-row">
            <button
              className="sb-related-open"
              title="Открыть в этой панели"
              onClick={() => props.onOpen(node.id)}
            >
              <span className="sb-related-icon" aria-hidden>
                {k.icon}
              </span>
              <span className="sb-related-ref">
                {node.type === "epic" ? "EPIC" : `#${node.iid}`}
              </span>
              <span className="sb-related-title">{node.title}</span>
              <span className="sb-related-kind">{k.label}</span>
            </button>
            <button
              className="sb-related-focus"
              title="Навести камеру на задачу"
              onClick={() => props.onFocus(node.id)}
            >
              ⌖ Фокус
            </button>
          </li>
        );
      })}
    </ul>
  );
}

// Этап 9 (D5/D6): «🔔 Напомнить» рядом с исполнителем + подтверждение с
// необязательным комментарием. Esc / клик вне — отмена.
function PokeBox(props: {
  username: string;
  onPoke: (message?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState("");
  const boxRef = useRef<HTMLDivElement | null>(null);

  // закрыть по Esc и по клику вне поп-овера
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onDown = (e: MouseEvent) => {
      // Node из reactflow перекрывает DOM-Node — приводим через HTMLElement
      const tgt = e.target as HTMLElement | null;
      if (boxRef.current && tgt && !boxRef.current.contains(tgt))
        setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  const send = () => {
    props.onPoke(msg.trim() || undefined);
    setOpen(false);
    setMsg("");
  };

  return (
    <div className="sb-row sb-poke" ref={boxRef}>
      <button
        className="sb-poke-btn"
        title="Напомнить исполнителю — сообщение в Matrix"
        onClick={() => setOpen((v) => !v)}
      >
        🔔 Напомнить
      </button>
      {open && (
        <div className="sb-poke-pop">
          <div className="sb-poke-title">Напомнить @{props.username}?</div>
          <textarea
            autoFocus
            className="sb-poke-msg"
            placeholder="комментарий (необязательно)"
            value={msg}
            onChange={(e) => setMsg(e.target.value)}
          />
          <div className="sb-poke-actions">
            <button className="primary" onClick={send}>
              Отправить
            </button>
            <button onClick={() => setOpen(false)}>Отмена</button>
          </div>
        </div>
      )}
    </div>
  );
}

function CreateForm(props: {
  meta: ScopeMeta;
  parent: GraphData["nodes"][number] | null;
  caps: Capabilities | null;
  extraLabels: { name: string; color: string | null }[];
  assigneeOptions: { id: number | null; username: string; name: string }[];
  milestoneOptions: { id: number; title: string }[];
  onCreated: (created: { id: string; iid: number; project_id: number }) => void;
  onCancel: () => void;
}) {
  const { meta, parent } = props;
  // метки: объединяем метки области (scope-meta) и реально встречающиеся в графе
  // — на групповом scope /groups/:id/labels часто пуст (метки заведены в проектах)
  const availLabels = (() => {
    const m = new Map<string, { name: string; color: string | null }>();
    for (const l of [...meta.labels, ...props.extraLabels]) m.set(l.name, l);
    return [...m.values()];
  })();
  // проект по умолчанию: родителя (для дочерней) → первый из группы
  const [projectId, setProjectId] = useState(
    parent?.project_id ?? meta.projects[0]?.id ?? 0
  );
  const [title, setTitle] = useState("");
  const [milestoneId, setMilestoneId] = useState<string>(
    parent?.milestone ? String(parent.milestone.id) : ""
  );
  const [assignee, setAssignee] = useState<string>("");
  const [labels, setLabels] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const projects = meta.projects.length > 0 ? meta.projects : [];

  const submit = async () => {
    if (!title.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const created = await api.createIssue({
        project_id: projectId,
        title: title.trim(),
        labels: [...labels],
        milestone_id: milestoneId ? Number(milestoneId) : null,
        assignee_ids: assignee ? [Number(assignee)] : [],
      });
      // дочерняя задача → связь parent→child (родитель = source)
      if (parent?.project_id && created?.project_id) {
        await api.createLink({
          project_id: parent.project_id,
          iid: parent.iid,
          target_project_id: created.project_id,
          target_iid: created.iid,
          kind: "parent",
        });
      }
      props.onCreated(created);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside className="sidebar">
      <h2>{parent ? "Дочерняя задача" : "Новая задача"}</h2>
      {parent && (
        <div className="parent-hint">
          ↳ дочерняя для <b>#{parent.iid}</b> {parent.title}
        </div>
      )}
      {projects.length > 0 && (
        <div className="sb-row col">
          <label>Проект</label>
          <select
            value={String(projectId)}
            onChange={(e) => setProjectId(Number(e.target.value))}
          >
            {projects.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="sb-row col">
        <label>Название</label>
        <input
          autoFocus
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Что нужно сделать"
        />
      </div>
      <div className="sb-row col">
        <label>Спринт</label>
        <select
          value={milestoneId}
          onChange={(e) => setMilestoneId(e.target.value)}
        >
          <option value="">— без спринта —</option>
          {props.milestoneOptions.map((m) => (
            <option key={m.id} value={String(m.id)}>
              {m.title}
            </option>
          ))}
        </select>
      </div>
      <div className="sb-row col">
        <label>Исполнитель</label>
        <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
          <option value="">— никто —</option>
          {props.assigneeOptions
            .filter((u) => u.id != null)
            .map((u) => (
              <option key={u.username} value={String(u.id)}>
                {u.name}
              </option>
            ))}
        </select>
      </div>
      <div className="sb-row col">
        <label>Метки</label>
        <div className="label-grid">
          {availLabels.map((l) => (
            <label key={l.name} className="lbl-check">
              <input
                type="checkbox"
                checked={labels.has(l.name)}
                onChange={(e) => {
                  const s = new Set(labels);
                  e.target.checked ? s.add(l.name) : s.delete(l.name);
                  setLabels(s);
                }}
              />
              <span
                className="chip"
                style={{ background: l.color ?? "#888", color: readableText(l.color) }}
              >
                {l.name}
              </span>
            </label>
          ))}
        </div>
      </div>
      {err && <div className="error small">{err}</div>}
      <div className="sb-actions">
        <button className="primary" disabled={busy || !title.trim()} onClick={submit}>
          {busy ? "Создаю…" : "Создать"}
        </button>
        <button onClick={props.onCancel}>Отмена</button>
      </div>
    </aside>
  );
}

// ── комментарии задачи ──────────────────────────────────────────────────────
function Comments(props: { projectId: number; iid: number }) {
  const [notes, setNotes] = useState<
    Awaited<ReturnType<typeof api.notes>> | null
  >(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    setNotes(null);
    api
      .notes(props.projectId, props.iid)
      .then(setNotes)
      .catch(() => setNotes([]));
  }, [props.projectId, props.iid]);

  useEffect(() => reload(), [reload]);

  const send = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try {
      await api.addNote(props.projectId, props.iid, text.trim());
      setText("");
      reload();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="comments">
      <div className="cm-head">Комментарии {notes ? `(${notes.length})` : ""}</div>
      {notes === null && <div className="cm-loading">загрузка…</div>}
      {notes?.map((n) => (
        <div key={n.id} className="cm-item">
          <div className="cm-meta">
            <span className="cm-author">{n.author.name}</span>
            <span className="cm-date">{n.created_at.slice(0, 10)}</span>
          </div>
          <div className="cm-body">
            <Markdown source={n.body} />
          </div>
        </div>
      ))}
      {notes && notes.length === 0 && (
        <div className="cm-empty">пока нет комментариев</div>
      )}
      <div className="cm-add">
        <textarea
          placeholder="Написать комментарий…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
        />
        <button
          className="primary"
          disabled={busy || !text.trim()}
          onClick={send}
        >
          {busy ? "…" : "Отправить"}
        </button>
      </div>
    </div>
  );
}


// ── экран входа (OAuth) / приглашение настроить инстанс ─────────────────────
function LoginGate(props: {
  instanceReady: boolean;
  oauthAvailable: boolean;
  adminEnabled: boolean;
  onHelp: () => void;
}) {
  const login = () => {
    const base = import.meta.env.VITE_API ?? "";
    window.location.href = base + "/api/oauth/start";
  };
  return (
    <div className="modal-backdrop login-backdrop">
      <div className="login-card">
        <div className="login-logo">◆</div>
        <h1>Issue Graph</h1>
        {!props.instanceReady ? (
          <>
            <p className="modal-sub">
              Приложение ещё не настроено администратором — подключение к GitLab
              не задано.
            </p>
            {props.adminEnabled && (
              <a className="login-btn" href="#admin">
                Открыть админку
              </a>
            )}
            <p className="login-hint">
              Обратитесь к администратору, чтобы он указал GitLab и OAuth в
              разделе <code>#admin</code>.
            </p>
          </>
        ) : props.oauthAvailable ? (
          <>
            <p className="modal-sub">Войдите под своим аккаунтом GitLab.</p>
            <button className="login-btn primary" onClick={login}>
              Войти через GitLab ↗
            </button>
          </>
        ) : (
          <>
            <p className="modal-sub">
              OAuth не настроен администратором. Вход недоступен.
            </p>
            {props.adminEnabled && (
              <a className="login-btn" href="#admin">
                Открыть админку
              </a>
            )}
          </>
        )}
        <button className="login-help" onClick={props.onHelp}>
          Как это работает?
        </button>
      </div>
    </div>
  );
}
