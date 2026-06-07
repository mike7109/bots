import { useMemo, useState } from "react";
import { api } from "./api";
import { readableText, isOverdue } from "./colors";
import type { Board, GNode, Label } from "./types";

// ── Этап 12 (D8): режим «Доска» (GitLab-style) ──────────────────────────────
// Полноценная HTML-доска (колонки со скроллом, НЕ на ReactFlow-холсте):
//   Open | label-колонки (по порядку доски) | Closed.
// Раскладка (D9): Closed = закрытые; label-колонка = открытые с этой меткой (в
// ПЕРВУЮ подходящую по порядку, без дублей); Open = открытые без меток-списков.
// Drag (D10) меняет данные через api.patchIssue (готовые эндпоинты Этапа 0/6).

// Колонка доски: спец-колонка Open/Closed или label-список (label!=null).
type BoardCol = {
  key: string; // "__open__" | "__closed__" | имя метки
  title: string;
  color: string | null; // акцент шапки/полосы (для label = цвет метки)
  label: string | null; // имя метки-списка (null для Open/Closed)
  kind: "open" | "closed" | "label";
};

const OPEN_KEY = "__open__";
const CLOSED_KEY = "__closed__";

// scoped-метка вида "pref::value" — префикс группы (для взаимоисключения D10)
function scopedPrefix(name: string): string | null {
  const i = name.indexOf("::");
  return i > 0 ? name.slice(0, i) : null;
}

// Срок-бейдж: тот же расчёт, что в IssueNode (overdue/soon/обычный).
function dueClass(due: string | null, closed: boolean): "overdue" | "soon" | "none" {
  if (!due || closed) return "none";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const d = new Date(due + "T00:00:00");
  const diff = Math.round((d.getTime() - today.getTime()) / 86_400_000);
  if (diff < 0) return "overdue";
  if (diff <= 1) return "soon";
  return "none";
}

// инициалы исполнителя (как в IssueNode/топбаре)
function initials(name: string): string {
  return name
    .split(" ")
    .map((s) => s[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

export function BoardView({
  nodes,
  boards,
  boardsLoading,
  graphLoading,
  hasData,
  selectedId,
  onSelect,
  applyPatched,
  setToast,
  allLabels,
  scopeLabel,
}: {
  nodes: GNode[];
  boards: Board[];
  // структура доски (fetch /api/boards) ещё грузится → не показывать фолбэк
  boardsLoading: boolean;
  // граф (state=all) ещё грузится — карточки колонок берутся из него
  graphLoading: boolean;
  // граф-данные уже получены (иначе нечем заполнять колонки)
  hasData: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  // оптимистичное обновление ноды (+ открытых деталей) после PATCH
  applyPatched: (n: GNode) => void;
  setToast: (s: string) => void;
  allLabels: Label[]; // фолбэк-колонки: выбор метки вручную
  scopeLabel?: string;
}) {
  // если досок несколько — простой селектор (первая по умолчанию)
  const [boardIdx, setBoardIdx] = useState(0);
  const board = boards[boardIdx] ?? null;

  // фолбэк (досок нет): локально выбираемые label-колонки (D8)
  const [manualCols, setManualCols] = useState<BoardColumn>([]);
  const [picking, setPicking] = useState(false);

  // какую карточку сейчас тащим / над какой колонкой — для подсветки (D10)
  const [overKey, setOverKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // ── колонки доски: Open + label-колонки (по порядку) + Closed (D8) ──
  const columns = useMemo<BoardCol[]>(() => {
    const cols: BoardCol[] = [];
    if (board) {
      if (!board.hide_backlog)
        cols.push({ key: OPEN_KEY, title: "Open", color: null, label: null, kind: "open" });
      for (const c of board.columns)
        cols.push({
          key: c.label,
          title: c.label,
          color: c.color,
          label: c.label,
          kind: "label",
        });
      if (!board.hide_closed)
        cols.push({ key: CLOSED_KEY, title: "Closed", color: null, label: null, kind: "closed" });
    } else {
      // фолбэк: Open | (ручные метки) | Closed
      cols.push({ key: OPEN_KEY, title: "Open", color: null, label: null, kind: "open" });
      for (const m of manualCols)
        cols.push({
          key: m.label,
          title: m.label,
          color: m.color,
          label: m.label,
          kind: "label",
        });
      cols.push({ key: CLOSED_KEY, title: "Closed", color: null, label: null, kind: "closed" });
    }
    return cols;
  }, [board, manualCols]);

  // набор имён меток-списков (для определения «Open = без меток-списков»)
  const listLabels = useMemo(
    () => new Set(columns.filter((c) => c.label).map((c) => c.label as string)),
    [columns]
  );

  // ── раскладка задач по колонкам (D9) ──
  // Closed = закрытые; label-колонка = открытые с этой меткой (в ПЕРВУЮ
  // подходящую по порядку, чтобы карточка не дублировалась); Open = открытые
  // без ни одной метки-списка.
  const byCol = useMemo(() => {
    const m = new Map<string, GNode[]>();
    for (const c of columns) m.set(c.key, []);
    const labelOrder = columns.filter((c) => c.label).map((c) => c.label as string);
    for (const n of nodes) {
      if (n.type !== "issue") continue;
      if (n.state === "closed") {
        m.get(CLOSED_KEY)?.push(n); // если Closed скрыта — карточка просто не видна
        continue;
      }
      const names = new Set(n.labels.map((l) => l.name));
      const hit = labelOrder.find((ln) => names.has(ln)); // первая подходящая
      if (hit) m.get(hit)?.push(n);
      else m.get(OPEN_KEY)?.push(n);
    }
    return m;
  }, [columns, nodes]);

  // ── drag → PATCH (D10) ──
  async function moveTo(node: GNode, col: BoardCol) {
    if (!node.project_id) return;
    const pid = node.project_id;
    const iid = node.iid;
    let body: Parameters<typeof api.patchIssue>[2] | null = null;
    let label = "";

    if (col.kind === "closed") {
      if (node.state === "closed") return; // уже здесь
      body = { state_event: "close" };
      label = "Задача закрыта";
    } else if (col.kind === "open") {
      // Open: переоткрыть закрытую; снять метку колонки-источника + scoped-сиблинги
      const remove = new Set(removeFromSource(node));
      for (const src of [...remove]) {
        const pref = scopedPrefix(src);
        if (pref)
          for (const l of node.labels)
            if (scopedPrefix(l.name) === pref) remove.add(l.name);
      }
      body = {
        ...(node.state === "closed" ? { state_event: "reopen" } : {}),
        ...(remove.size ? { remove_labels: [...remove] } : {}),
      };
      if (!Object.keys(body).length) return; // открытая задача без меток-списков
      label = node.state === "closed" ? "Задача открыта" : "Метка снята";
    } else {
      // label-колонка: добавить её метку, снять метку-источник + scoped-сиблинги.
      const target = col.label as string;
      if (node.state !== "closed") {
        const names = new Set(node.labels.map((l) => l.name));
        const labelOrder = columns.filter((c) => c.label).map((c) => c.label as string);
        const cur = labelOrder.find((ln) => names.has(ln));
        if (cur === target) return; // уже в этой колонке
      }
      const remove = new Set(removeFromSource(node));
      remove.delete(target);
      // scoped (target = "pref::x"): снять ВСЕ метки задачи той же группы, кроме target
      const pref = scopedPrefix(target);
      if (pref)
        for (const l of node.labels) {
          if (l.name !== target && scopedPrefix(l.name) === pref) remove.add(l.name);
        }
      body = {
        add_labels: [target],
        ...(remove.size ? { remove_labels: [...remove] } : {}),
        ...(node.state === "closed" ? { state_event: "reopen" } : {}),
      };
      label = "Метка изменена";
    }

    // снять метку колонки-источника: только если источник — label-колонка списка
    function removeFromSource(n: GNode): string[] {
      const labelOrder = columns.filter((c) => c.label).map((c) => c.label as string);
      const names = new Set(n.labels.map((l) => l.name));
      const cur = labelOrder.find((ln) => names.has(ln));
      return cur ? [cur] : [];
    }

    if (!body) return;
    setBusy(true);
    try {
      const updated = await api.patchIssue(pid, iid, body);
      applyPatched(updated); // оптимистично: карточка переедет (byCol зависит от nodes)
      setToast(label);
    } catch (e) {
      setToast("Не удалось перенести: " + e);
    } finally {
      setBusy(false);
    }
  }

  function onDrop(e: React.DragEvent, col: BoardCol) {
    e.preventDefault();
    setOverKey(null);
    const id = e.dataTransfer.getData("text/plain");
    const node = nodes.find((n) => n.id === id);
    if (node) void moveTo(node, col);
  }

  // Пока структура доски ещё едет (boardsLoading) ИЛИ граф (state=all) грузится
  // и данных колонок ещё нет — показываем СОСТОЯНИЕ ЗАГРУЗКИ, а не фолбэк
  // «доски не найдены» (иначе мелькает ложный Open|Closed на ~7с, см. баг).
  if (boardsLoading || (graphLoading && !hasData)) {
    return (
      <div className="board-view">
        <div className="loading-overlay">
          <div className="spinner" />
          <div className="loading-text">Загрузка доски…</div>
        </div>
      </div>
    );
  }

  return (
    <div className="board-view">
      <div className="board-bar">
        <span className="bv-title">
          ▦ Доска{board ? `: ${board.name}` : scopeLabel ? `: ${scopeLabel}` : ""}
        </span>
        {boards.length > 1 && (
          <select
            className="bv-select"
            value={boardIdx}
            onChange={(e) => setBoardIdx(Number(e.target.value))}
            title="Выбрать доску GitLab"
          >
            {boards.map((b, i) => (
              <option key={b.id} value={i}>
                {b.name}
              </option>
            ))}
          </select>
        )}
        {!board && (
          <span className="bv-hint">
            доски GitLab не найдены — колонки можно добавить вручную
          </span>
        )}
      </div>

      <div className="board-cols">
        {columns.map((c) => {
          const items = byCol.get(c.key) ?? [];
          return (
            <div
              key={c.key}
              className={`board-col${overKey === c.key ? " drag-over" : ""}`}
              onDragOver={(e) => {
                e.preventDefault();
                if (overKey !== c.key) setOverKey(c.key);
              }}
              onDragLeave={(e) => {
                // покидаем колонку (а не её ребёнка) → гасим подсветку
                if (e.currentTarget === e.target) setOverKey(null);
              }}
              onDrop={(e) => onDrop(e, c)}
            >
              <div
                className="board-col-head"
                style={c.color ? { borderTopColor: c.color } : undefined}
              >
                <span className="bch-dot" style={{ background: c.color ?? "#9aa0ad" }} />
                <span className="bch-title">{c.title}</span>
                <span className="bch-count">{items.length}</span>
              </div>
              <div className="board-col-body">
                {items.map((n) => (
                  <BoardCard
                    key={n.id}
                    node={n}
                    selected={n.id === selectedId}
                    onClick={() => onSelect(n.id)}
                  />
                ))}
              </div>
            </div>
          );
        })}

        {/* фолбэк: добавить label-колонку вручную (выбор метки области) */}
        {!board && (
          <div className="board-addcol">
            {picking ? (
              <select
                className="bv-select"
                autoFocus
                defaultValue=""
                onChange={(e) => {
                  const name = e.target.value;
                  if (!name) return;
                  const la = allLabels.find((l) => l.name === name);
                  setManualCols((cols) =>
                    cols.some((c) => c.label === name)
                      ? cols
                      : [...cols, { label: name, color: la?.color ?? null }]
                  );
                  setPicking(false);
                }}
                onBlur={() => setPicking(false)}
              >
                <option value="">выберите метку…</option>
                {allLabels
                  .filter((l) => !manualCols.some((c) => c.label === l.name))
                  .map((l) => (
                    <option key={l.name} value={l.name}>
                      {l.name}
                    </option>
                  ))}
              </select>
            ) : (
              <button
                className="board-addcol-btn"
                onClick={() => setPicking(true)}
                title="Добавить колонку-метку"
              >
                ＋ Список
              </button>
            )}
          </div>
        )}
      </div>

      {busy && <div className="board-busy" />}
    </div>
  );
}

// одна колонка в фолбэк-режиме хранит только имя метки + цвет
type BoardColumn = { label: string; color: string | null }[];

// ── карточка задачи (как в GitLab: заголовок, #ref, метки, срок, аватар) ──
function BoardCard({
  node,
  selected,
  onClick,
}: {
  node: GNode;
  selected: boolean;
  onClick: () => void;
}) {
  const closed = node.state === "closed";
  const due = dueClass(node.due_date, closed);
  const overdue = !closed && isOverdue(node.due_date ?? null);
  const a = node.assignees[0];
  return (
    <div
      className={`board-card${selected ? " selected" : ""}${closed ? " closed" : ""}${
        overdue ? " overdue" : ""
      }`}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", node.id);
        e.dataTransfer.effectAllowed = "move";
      }}
      onClick={onClick}
      title={node.title}
    >
      <div className="bc-title">{node.title}</div>
      <div className="bc-labels">
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
      <div className="bc-foot">
        <span className="bc-ref">#{node.iid}</span>
        {node.due_date && (
          <span className={`bc-due due-${due}`} title="Срок выполнения">
            {due === "overdue" ? "⚠ " : due === "soon" ? "⏰ " : "📅 "}
            {node.due_date.slice(5)}
          </span>
        )}
        <span className="bc-spacer" />
        {a && (
          <span className="bc-ava" title={a.name ?? ""}>
            {initials(a.name ?? "?")}
          </span>
        )}
      </div>
    </div>
  );
}
