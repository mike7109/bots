import dagre from "dagre";
import type { Node, Edge } from "reactflow";

export const NODE_W = 240;
export const NODE_H = 92;

export type GraphLayout = "td" | "lr" | "radial";

/** Иерархическая раскладка зависимостей (dagre). dir: TB сверху-вниз, LR слева-направо. */
export function dagreLayout(
  nodes: Node[],
  edges: Edge[],
  dir: "TB" | "LR" = "TB"
): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: dir, nodesep: 36, ranksep: 90, marginx: 20, marginy: 20 });
  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return nodes.map((n) => {
    const p = g.node(n.id);
    return { ...n, position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 } };
  });
}

/**
 * Радиальная раскладка: центр — выбранная нода (или самая связанная),
 * остальные — концентрическими кольцами по расстоянию в графе.
 * Даёт «звезду»: задача в центре, зависимые вокруг.
 */
export function radialLayout(
  nodes: Node[],
  edges: Edge[],
  centerId?: string | null
): Node[] {
  if (!nodes.length) return nodes;
  const adj = new Map<string, string[]>();
  nodes.forEach((n) => adj.set(n.id, []));
  edges.forEach((e) => {
    adj.get(e.source)?.push(e.target);
    adj.get(e.target)?.push(e.source);
  });

  let center = centerId && adj.has(centerId) ? centerId : null;
  if (!center) {
    let best = -1;
    for (const n of nodes) {
      const d = adj.get(n.id)!.length;
      if (d > best) {
        best = d;
        center = n.id;
      }
    }
  }

  // BFS — кольцо = дистанция от центра
  const ring = new Map<string, number>([[center!, 0]]);
  const queue = [center!];
  while (queue.length) {
    const cur = queue.shift()!;
    const r = ring.get(cur)!;
    for (const nb of adj.get(cur)!) {
      if (!ring.has(nb)) {
        ring.set(nb, r + 1);
        queue.push(nb);
      }
    }
  }
  // несвязанные ноды — во внешнее кольцо
  let maxRing = 0;
  for (const r of ring.values()) maxRing = Math.max(maxRing, r);
  nodes.forEach((n) => {
    if (!ring.has(n.id)) ring.set(n.id, maxRing + 1);
  });

  const byRing = new Map<number, string[]>();
  for (const [id, r] of ring) {
    if (!byRing.has(r)) byRing.set(r, []);
    byRing.get(r)!.push(id);
  }

  const STEP = 300; // радиус между кольцами
  const pos = new Map<string, { x: number; y: number }>();
  for (const [r, ids] of byRing) {
    if (r === 0) {
      pos.set(ids[0], { x: 0, y: 0 });
      continue;
    }
    const radius = STEP * r;
    const ang = (2 * Math.PI) / ids.length;
    // лёгкий поворот нечётных колец, чтобы не выстраивались в линии
    const offset = r % 2 ? ang / 2 : 0;
    ids.forEach((id, i) => {
      const a = i * ang + offset;
      pos.set(id, { x: Math.cos(a) * radius, y: Math.sin(a) * radius });
    });
  }

  return nodes.map((n) => {
    const p = pos.get(n.id) ?? { x: 0, y: 0 };
    return { ...n, position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 } };
  });
}

/**
 * Раскладка слабосвязанного графа без вырождения в «полосу».
 *
 * Шаги:
 *  1. неориентированный adjacency → связные компоненты (union-find);
 *  2. каждую компоненту ≥2 нод — своим dagreLayout, нормализуем в её bbox;
 *  3. singleton-ноды (degree 0) — в сетку gridPack по ширине вьюпорта;
 *  4. bbox-ы компонент + блок сетки раскладываем shelf/next-fit-packing
 *     (по ширине, перенос на новую полку).
 *
 * Возвращает абсолютные позиции (left-top), как и остальные раскладки.
 */
export function connectedComponentsLayout(
  nodes: Node[],
  edges: Edge[],
  dir: "TB" | "LR" = "TB",
  viewportW = 1600
): Node[] {
  if (!nodes.length) return nodes;

  // union-find по неориентированным рёбрам
  const parent = new Map<string, string>();
  nodes.forEach((n) => parent.set(n.id, n.id));
  const find = (x: string): string => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    // сжатие пути
    while (parent.get(x) !== r) {
      const nx = parent.get(x)!;
      parent.set(x, r);
      x = nx;
    }
    return r;
  };
  const union = (a: string, b: string) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  };
  const idSet = new Set(nodes.map((n) => n.id));
  const degree = new Map<string, number>();
  nodes.forEach((n) => degree.set(n.id, 0));
  for (const e of edges) {
    if (!idSet.has(e.source) || !idSet.has(e.target)) continue;
    union(e.source, e.target);
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
  }

  // группируем ноды по корню компоненты
  const comps = new Map<string, Node[]>();
  for (const n of nodes) {
    const r = find(n.id);
    (comps.get(r) ?? comps.set(r, []).get(r)!).push(n);
  }

  // одиночки (degree 0) собираем в один блок-сетку, многонодовые — раскладываем dagre
  const singletons: Node[] = [];
  // блок раскладки: ноды с уже нормализованными в (0,0) координатами + размер
  type Block = { nodes: Node[]; w: number; h: number };
  const blocks: Block[] = [];

  for (const group of comps.values()) {
    if (group.length === 1 && (degree.get(group[0].id) ?? 0) === 0) {
      singletons.push(group[0]);
      continue;
    }
    const groupEdges = edges.filter(
      (e) => idSet.has(e.source) && idSet.has(e.target) &&
        find(e.source) === find(group[0].id)
    );
    const laid = dagreLayout(group, groupEdges, dir);
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of laid) {
      minX = Math.min(minX, n.position.x);
      minY = Math.min(minY, n.position.y);
      maxX = Math.max(maxX, n.position.x + NODE_W);
      maxY = Math.max(maxY, n.position.y + NODE_H);
    }
    // нормализуем в (0,0)
    const norm = laid.map((n) => ({
      ...n,
      position: { x: n.position.x - minX, y: n.position.y - minY },
    }));
    blocks.push({ nodes: norm, w: maxX - minX, h: maxY - minY });
  }

  // одиночки → собственная сетка (как один блок), пакуем рядами по ширине вьюпорта
  if (singletons.length) {
    const grid = gridPack(singletons, viewportW);
    blocks.push(grid);
  }

  // shelf-packing блоков: next-fit по ширине вьюпорта, перенос на новую полку
  const GAP = 56; // зазор между блоками/полками
  const out: Node[] = [];
  let shelfX = 0;
  let shelfY = 0;
  let shelfH = 0;
  // большие блоки вперёд — компактнее полки
  blocks.sort((a, b) => b.h - a.h);
  for (const b of blocks) {
    if (shelfX > 0 && shelfX + b.w > viewportW) {
      // новая полка
      shelfX = 0;
      shelfY += shelfH + GAP;
      shelfH = 0;
    }
    for (const n of b.nodes) {
      out.push({
        ...n,
        position: { x: shelfX + n.position.x, y: shelfY + n.position.y },
      });
    }
    shelfX += b.w + GAP;
    shelfH = Math.max(shelfH, b.h);
  }
  return out;
}

/**
 * Сетка одиночных нод аккуратными рядами в пределах ширины вьюпорта.
 * Возвращает блок с нормализованными в (0,0) координатами и его размером.
 */
function gridPack(
  nodes: Node[],
  viewportW: number
): { nodes: Node[]; w: number; h: number } {
  const GAP_X = 24;
  const GAP_Y = 24;
  const cellW = NODE_W + GAP_X;
  const cellH = NODE_H + GAP_Y;
  const cols = Math.max(1, Math.floor((viewportW + GAP_X) / cellW));
  const rows = Math.ceil(nodes.length / cols);
  const laid = nodes.map((n, i) => {
    const c = i % cols;
    const r = Math.floor(i / cols);
    return { ...n, position: { x: c * cellW, y: r * cellH } };
  });
  const usedCols = Math.min(cols, nodes.length);
  return {
    nodes: laid,
    w: usedCols * cellW - GAP_X,
    h: rows * cellH - GAP_Y,
  };
}

export interface Column {
  key: string;
  title: string;
}

// размеры колоночной (Kanban) раскладки. Экспортируем — Этап 8 считает по ним
// колонку под точкой дропа (drag меняет данные) и рисует шапки/строки.
export const COL_W = NODE_W + 60;
export const ROW_H = NODE_H + 28;
export const HEADER = 56;
// Этап 8: ширина свёрнутой колонки — узкая полоса (только заголовок, без нод).
export const COLLAPSED_W = 44;

/** Опции колоночной раскладки (Этап 8: Kanban). */
export interface ColumnLayoutOpts {
  // ключи свёрнутых колонок: их ноды на холст не выкладываем
  collapsed?: Set<string>;
  // вертикальная сортировка внутри колонки (по due/weight/iid/title…)
  sort?: (a: Node, b: Node) => number;
}

/**
 * X-смещение левого края колонки с учётом свёрнутых слева (Этап 8).
 * Свёрнутые колонки занимают узкую полосу COLLAPSED_W вместо полной COL_W,
 * поэтому абсолютные X колонок «съезжают» — общий хелпер для раскладки,
 * шапок и детекта колонки под дропом.
 */
export function columnOffsets(
  columns: Column[],
  collapsed?: Set<string>
): number[] {
  const out: number[] = [];
  let x = 20;
  for (const c of columns) {
    out.push(x);
    x += collapsed?.has(c.key) ? COLLAPSED_W : COL_W;
  }
  return out;
}

/**
 * Раскладка по колонкам — универсальная группировка (по спринту, метке,
 * исполнителю, проекту, статусу…). keyOf возвращает ключ колонки для ноды.
 *
 * Этап 8 (Kanban): свёрнутые колонки сжимаются в полосу (их ноды не
 * выкладываем), внутри колонки можно задать вертикальную сортировку.
 */
export function columnLayout(
  nodes: Node[],
  columns: Column[],
  keyOf: (n: Node) => string,
  opts?: ColumnLayoutOpts
): Node[] {
  const colIndex = new Map(columns.map((c, i) => [c.key, i]));
  const fallback = columns.length - 1; // последняя колонка — «без …»
  const offsets = columnOffsets(columns, opts?.collapsed);

  // группируем по колонке, чтобы применить сортировку внутри неё
  const byCol = new Map<number, Node[]>();
  for (const n of nodes) {
    const ci = colIndex.get(keyOf(n)) ?? fallback;
    (byCol.get(ci) ?? byCol.set(ci, []).get(ci)!).push(n);
  }

  // P3: фикс вырождения «колонки → одна длинная вертикальная полоса». Если
  // фактически непуста только одна колонка (напр. вид по статусу, но загружены
  // только открытые), раскладываем ВСЕ карточки сеткой, а не одной вертикалью —
  // иначе при Fit это нечитаемая тонкая полоса. При ≥2 непустых колонках —
  // обычное канбан-поведение (drag между колонками, шапки) без изменений.
  const nonEmpty = [...byCol.values()].filter((g) => g.length > 0).length;
  if (nonEmpty <= 1 && nodes.length > 1) {
    // сетка по ширине вьюпорта — как блок одиночных нод в графе зависимостей
    const sorted = opts?.sort ? [...nodes].sort(opts.sort) : nodes;
    const GAP_X = 24;
    const GAP_Y = 24;
    const cellW = NODE_W + GAP_X;
    const cellH = NODE_H + GAP_Y;
    const cols = Math.max(1, Math.round(Math.sqrt(sorted.length)));
    const startX = offsets[0] ?? 20;
    return sorted.map((n, i) => {
      const c = i % cols;
      const r = Math.floor(i / cols);
      return {
        ...n,
        hidden: false,
        position: { x: startX + c * cellW, y: HEADER + r * cellH },
      };
    });
  }

  const out: Node[] = [];
  for (const [ci, group] of byCol) {
    const col = columns[ci];
    if (opts?.sort) group.sort(opts.sort);
    // свёрнутую колонку прячем: ноды не на холсте (их рисовать незачем)
    if (col && opts?.collapsed?.has(col.key)) {
      for (const n of group) out.push({ ...n, hidden: true });
      continue;
    }
    group.forEach((n, row) => {
      out.push({
        ...n,
        hidden: false,
        position: { x: offsets[ci] ?? 20, y: HEADER + row * ROW_H },
      });
    });
  }
  return out;
}

export function columnHeaders(
  columns: Column[],
  collapsed?: Set<string>
): { title: string; x: number; collapsed: boolean }[] {
  const offsets = columnOffsets(columns, collapsed);
  return columns.map((c, i) => ({
    title: c.title,
    x: offsets[i],
    collapsed: collapsed?.has(c.key) ?? false,
  }));
}

/**
 * Колонка под точкой дропа (Этап 8): какой колонке принадлежит координата X
 * холста. Возвращает ключ колонки или null, если X левее первой колонки.
 * Зеркалит раскладку columnLayout/columnOffsets — единый источник геометрии.
 */
export function columnAtX(
  columns: Column[],
  x: number,
  collapsed?: Set<string>
): string | null {
  const offsets = columnOffsets(columns, collapsed);
  // центр ноды кладём в её колонку: сравниваем по правой границе колонки
  for (let i = 0; i < columns.length; i++) {
    const left = offsets[i];
    const w = collapsed?.has(columns[i].key) ? COLLAPSED_W : COL_W;
    if (x < left + w) return columns[i].key;
  }
  return columns.length ? columns[columns.length - 1].key : null;
}
