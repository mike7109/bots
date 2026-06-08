import type {
  BoardsResponse,
  BulkPatchBody,
  BulkResult,
  GNode,
  GraphData,
  IssueDetail,
  Namespaces,
  Scope,
  ScopeMeta,
} from "./types";

// Если задан VITE_API — ходим туда напрямую, иначе через vite-прокси /api.
const BASE = import.meta.env.VITE_API ?? "";

// Сессия — в httpOnly-cookie на бэкенде (из JS недоступна). Браузер шлёт её
// автоматически; credentials:"include" нужен на случай cross-origin (VITE_API).
async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

const scopeQuery = (s: Scope) => `${s.kind}=${encodeURIComponent(s.id)}`;

export const api = {
  namespaces: () => j<Namespaces>("/api/namespaces"),
  capabilities: () => j<GraphData["meta"]["capabilities"]>("/api/capabilities"),
  // state: "opened" (по умолчанию, D1) или "all" — догрузить закрытые.
  // assignee: "me" (Этап 5: «Мои задачи») или username; пусто = все.
  graph: (
    s: Scope,
    nocache = false,
    state: "opened" | "all" = "opened",
    assignee?: string | null
  ) =>
    j<GraphData>(
      `/api/graph?${scopeQuery(s)}&state=${state}` +
        (assignee ? `&assignee=${encodeURIComponent(assignee)}` : "") +
        (nocache ? "&nocache=true" : "")
    ),
  scopeMeta: (s: Scope) => j<ScopeMeta>(`/api/scope-meta?${scopeQuery(s)}`),
  // Этап 12 (D7): импорт Kanban-досок области из GitLab (режим «Доска»).
  // Бэк деградирует к {boards: []} при отсутствии досок/прав — не бросает.
  boards: (s: Scope) => j<BoardsResponse>(`/api/boards?${scopeQuery(s)}`),

  createIssue: (body: {
    project_id: number;
    title: string;
    description?: string;
    labels?: string[];
    milestone_id?: number | null;
    assignee_ids?: number[];
  }) =>
    j<{ id: string; iid: number; project_id: number }>("/api/issues", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  createMilestone: (s: Scope, title: string) =>
    j<{ id: number; title: string }>(
      `/api/milestones?${scopeQuery(s)}`,
      { method: "POST", body: JSON.stringify({ title }) }
    ),

  createLink: (body: {
    project_id: number;
    iid: number;
    target_project_id: number;
    target_iid: number;
    kind: string; // relates | blocks | parent
  }) =>
    j<{ ok: boolean; kind: string; link_id: number | null }>("/api/links", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  setLinkMeta: (source: string, target: string, kind: string) =>
    j<{ ok: boolean; kind: string }>("/api/links/meta", {
      method: "POST",
      body: JSON.stringify({ source, target, kind }),
    }),

  // Этап 6: детали задачи (description/weight/time_stats/связанные MR и т.д.).
  issueDetail: (project_id: number, iid: number) =>
    j<IssueDetail>(`/api/issues/${project_id}/${iid}`),

  patchIssue: (
    project_id: number,
    iid: number,
    body: {
      milestone_id?: number | null;
      state_event?: string;
      assignee_ids?: number[];
      due_date?: string | null;
      // Этап 6: инлайн-метки (инкрементально), вес, описание
      add_labels?: string[];
      remove_labels?: string[];
      weight?: number | null;
      description?: string;
    }
  ) =>
    j<GNode>(`/api/issues/${project_id}/${iid}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  // Этап 7: массовое действие лассо. targets — [[project_id, iid], …]; patch —
  // те же поля, что у одиночного PATCH. Бэк применяет под семафором и отдаёт
  // результат по каждой задаче (частичные ошибки не валят всю операцию).
  bulkPatch: (targets: [number, number][], patch: BulkPatchBody) =>
    j<BulkResult>("/api/issues/bulk", {
      method: "POST",
      body: JSON.stringify({ targets, patch }),
    }),

  // Этап 8 (Kanban): перенос задачи в другой проект — отдельная операция GitLab
  // (меняется iid/project_id), не обычный PATCH. Возвращает новую ноду.
  moveIssue: (project_id: number, iid: number, to_project_id: number) =>
    j<GNode>(`/api/issues/${project_id}/${iid}/move`, {
      method: "POST",
      body: JSON.stringify({ to_project_id }),
    }),

  notes: (project_id: number, iid: number) =>
    j<
      {
        id: number;
        body: string;
        created_at: string;
        author: { name: string; username: string; avatar_url: string | null };
      }[]
    >(`/api/issues/${project_id}/${iid}/notes`),

  addNote: (project_id: number, iid: number, body: string) =>
    j(`/api/issues/${project_id}/${iid}/notes`, {
      method: "POST",
      body: JSON.stringify({ body }),
    }),

  getConfig: () =>
    j<{
      app_enabled: boolean;
      instance_configured: boolean;
      oauth_available: boolean;
      bot_available: boolean;
      admin_enabled: boolean;
      configured: boolean;
      url: string | null;
      auth_type: "pat" | "oauth" | null;
      capabilities: GraphData["meta"]["capabilities"] | null;
      user: {
        username: string | null;
        name: string | null;
        avatar_url: string | null;
        web_url: string | null;
      } | null;
    }>("/api/config"),

  logout: () => j<{ ok: boolean }>("/api/logout", { method: "POST" }),

  poke: (project_id: number, iid: number, message?: string) =>
    j<{ ok: boolean; to: string }>(`/api/notify/${project_id}/${iid}`, {
      method: "POST",
      body: JSON.stringify({ message: message ?? null }),
    }),

  deleteLink: (project_id: number, iid: number, link_id: number, target?: string) =>
    j<{ ok: boolean }>(
      `/api/links/${project_id}/${iid}/${link_id}` +
        (target ? `?target=${encodeURIComponent(target)}` : ""),
      { method: "DELETE" }
    ),

  // ── админка (инстанс-настройки) ──
  adminStatus: () =>
    j<{ enabled: boolean; logged_in: boolean }>("/api/admin/status"),
  adminLogin: (password: string) =>
    j<{ ok: boolean }>("/api/admin/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  adminSetEnabled: (enabled: boolean) =>
    j<{ enabled: boolean }>("/api/admin/enabled", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  adminSetPro: (pro: boolean) =>
    j<{ pro: boolean }>("/api/admin/pro", {
      method: "POST",
      body: JSON.stringify({ pro }),
    }),
  adminGetConfig: () =>
    j<{
      app_enabled: boolean;
      pro: boolean;
      gitlab_url: string | null;
      oauth_client_id: string | null;
      has_oauth_secret: boolean;
      bot_url: string | null;
      has_bot_secret: boolean;
      redirect_uri: string;
    }>("/api/admin/config"),
  adminSetConfig: (body: {
    gitlab_url: string;
    oauth_client_id?: string;
    oauth_client_secret?: string;
    bot_url?: string;
    bot_secret?: string;
  }) =>
    j<{ ok: boolean }>("/api/admin/config", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
