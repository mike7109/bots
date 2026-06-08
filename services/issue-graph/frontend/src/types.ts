export interface Label {
  name: string;
  color: string | null;
}
export interface Assignee {
  id: number | null;
  name: string | null;
  username: string | null;
  avatar_url: string | null;
}
export interface GNode {
  id: string;
  iid: number;
  project_id: number | null;
  title: string;
  state: string; // opened | closed
  web_url: string | null;
  labels: Label[];
  milestone: { id: number; title: string } | null;
  due_date: string | null;
  created_at: string | null;
  updated_at: string | null;
  assignees: Assignee[];
  weight: number | null;
  type: "issue" | "epic";
}
// Этап 6 (паритет): детали задачи, подгружаемые при клике на ноду.
export interface TimeStats {
  time_estimate: number;
  total_time_spent: number;
  human_time_estimate: string | null;
  human_total_time_spent: string | null;
}
export interface RelatedMR {
  id: number;
  iid: number | null;
  title: string | null;
  state: string | null; // opened | closed | merged | locked
  web_url: string | null;
  reference: string | null;
  closes: boolean; // закроет задачу при мердже (из /closed_by)
}
export interface IssueDetail extends GNode {
  description: string | null;
  confidential: boolean;
  health_status: string | null;
  start_date: string | null;
  reference: string | null;
  time_stats: TimeStats;
  merge_requests: RelatedMR[];
}
// Этап 7: массовое действие лассо — изменения ко многим задачам разом, с
// частичными ошибками (бэк отдаёт результат по каждой задаче).
export interface BulkPatchBody {
  milestone_id?: number | null;
  state_event?: string;
  assignee_ids?: number[];
  add_labels?: string[];
  remove_labels?: string[];
}
export interface BulkResult {
  ok: boolean;
  succeeded: number;
  failed: number;
  results: {
    project_id: number;
    iid: number;
    ok: boolean;
    error?: string;
    node?: GNode;
  }[];
}
export interface GEdge {
  id: string;
  source: string;
  target: string;
  type: "relates_to" | "blocks" | "epic" | "parent";
  directed?: boolean;
  link_id?: number | null;
}
export interface Capabilities {
  version: string;
  enterprise: boolean;
  tier: string;
  epics: boolean;
  blocking_links: boolean;
  iterations: boolean;
  milestones: boolean;
  graphql?: boolean;
}
export interface GraphData {
  nodes: GNode[];
  edges: GEdge[];
  meta: {
    issue_count: number;
    edge_count: number;
    capabilities: Capabilities;
    milestones: { id: number; title: string }[];
    labels: Label[];
  };
}
export interface Namespaces {
  groups: { id: number; full_path: string; name: string }[];
  projects: { id: number; full_path: string; name: string }[];
}
export interface ScopeMeta {
  milestones: { id: number; title: string }[];
  members: { id: number; name: string; username: string }[];
  labels: Label[];
  projects: { id: number; name: string; full_path: string }[];
}
export type Scope = { kind: "group" | "project"; id: string; label: string };
// Этап 12 (D7): импорт Kanban-доски из GitLab. columns — только label-списки
// (по position); Open/Closed — спец-колонки, заданные флагами hide_*.
export interface BoardColumn {
  label: string;
  color: string | null;
  position: number;
}
export interface Board {
  id: number;
  name: string;
  hide_backlog: boolean;
  hide_closed: boolean;
  columns: BoardColumn[];
}
export interface BoardsResponse {
  boards: Board[];
}
