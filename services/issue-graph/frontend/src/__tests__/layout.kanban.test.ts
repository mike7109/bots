/**
 * Юнит-тесты колоночной (Kanban) геометрии — Этап 8 ROADMAP. Без сети и без DOM:
 * проверяем чистые функции раскладки, на которых держится «drag меняет данные»,
 * сворачивание колонок и вертикальная сортировка.
 *
 * Запуск (без vitest — его в проекте нет): бандлим esbuild'ом и гоняем в node.
 * См. scripts/run-tests.mjs.
 */
import type { Node } from "reactflow";
import {
  columnLayout,
  columnOffsets,
  columnAtX,
  columnHeaders,
  COL_W,
  COLLAPSED_W,
  HEADER,
  ROW_H,
  NODE_W,
  type Column,
} from "../layout";

// ── микро-харнесс (без зависимостей) ─────────────────────────────────────────
let passed = 0;
let failed = 0;
function eq(actual: unknown, expected: unknown, msg: string) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${msg}\n  ожидалось ${e}\n  получено  ${a}`);
  }
}
function ok(cond: boolean, msg: string) {
  if (cond) passed++;
  else {
    failed++;
    console.error(`FAIL: ${msg}`);
  }
}

const COLS: Column[] = [
  { key: "a", title: "A" },
  { key: "b", title: "B" },
  { key: "none", title: "Без" },
];

function mk(id: string, col: string, extra: Record<string, unknown> = {}): Node {
  return {
    id,
    type: "issue",
    position: { x: 0, y: 0 },
    data: { id, col, ...extra },
  } as unknown as Node;
}
const keyOf = (n: Node) => (n.data as { col: string }).col;

// ── columnOffsets: свёрнутые колонки сжимают X последующих ───────────────────
{
  const open = columnOffsets(COLS);
  eq(open, [20, 20 + COL_W, 20 + 2 * COL_W], "offsets без свёрнутых");

  const col = columnOffsets(COLS, new Set(["a"]));
  eq(
    col,
    [20, 20 + COLLAPSED_W, 20 + COLLAPSED_W + COL_W],
    "offsets со свёрнутой первой колонкой"
  );
}

// ── columnAtX: какая колонка под точкой X (детект дропа) ─────────────────────
{
  // центр первой колонки → "a"
  ok(columnAtX(COLS, 20 + NODE_W / 2) === "a", "X в первой колонке → a");
  // центр второй → "b"
  ok(columnAtX(COLS, 20 + COL_W + NODE_W / 2) === "b", "X во второй → b");
  // далеко справа → последняя колонка
  ok(columnAtX(COLS, 99999) === "none", "X справа → последняя колонка");
  // граница: ровно правый край первой колонки уходит во вторую
  ok(columnAtX(COLS, 20 + COL_W) === "b", "правый край первой → вторая");
  // со свёрнутой первой колонкой границы сдвигаются
  ok(
    columnAtX(COLS, 20 + COLLAPSED_W + 5, new Set(["a"])) === "b",
    "после свёрнутой первой следующий X → b"
  );
}

// ── columnLayout: раскладка по колонкам + сортировка + сворачивание ──────────
{
  const nodes = [
    mk("n1", "a", { iid: 3 }),
    mk("n2", "a", { iid: 1 }),
    mk("n3", "b", { iid: 2 }),
    mk("n4", "zzz", { iid: 9 }), // несуществующая колонка → fallback (последняя)
  ];
  const laid = columnLayout(nodes, COLS, keyOf);
  const byId = Object.fromEntries(laid.map((n) => [n.id, n]));

  // X колонки A
  eq(byId.n1.position.x, 20, "n1 в колонке A по X");
  eq(byId.n2.position.x, 20, "n2 в колонке A по X");
  // X колонки B
  eq(byId.n3.position.x, 20 + COL_W, "n3 в колонке B по X");
  // fallback → последняя колонка "none"
  eq(byId.n4.position.x, 20 + 2 * COL_W, "n4 (неизв. колонка) → последняя");

  // две ноды в одной колонке стоят на разных строках (Y)
  ok(
    byId.n1.position.y !== byId.n2.position.y,
    "две ноды в колонке A на разных строках"
  );
  ok(
    byId.n1.position.y === HEADER || byId.n2.position.y === HEADER,
    "первая строка колонки начинается с HEADER"
  );
}

// ── сортировка внутри колонки: по возрастанию iid (≥2 непустых колонки) ───────
// При нескольких непустых колонках сортировка раскладывает строки вертикально.
{
  const nodes = [
    mk("hi", "a", { iid: 30 }),
    mk("lo", "a", { iid: 10 }),
    mk("mid", "a", { iid: 20 }),
    mk("other", "b", { iid: 5 }), // вторая непустая колонка → канбан, не грид
  ];
  const laid = columnLayout(nodes, COLS, keyOf, {
    sort: (x, y) =>
      (x.data as { iid: number }).iid - (y.data as { iid: number }).iid,
  });
  const byId = Object.fromEntries(laid.map((n) => [n.id, n]));
  // меньший iid — выше (меньший Y)
  ok(byId.lo.position.y < byId.mid.position.y, "сорт: lo выше mid");
  ok(byId.mid.position.y < byId.hi.position.y, "сорт: mid выше hi");
  eq(byId.lo.position.y, HEADER, "сорт: первая строка = HEADER");
  eq(byId.mid.position.y, HEADER + ROW_H, "сорт: вторая строка = HEADER+ROW_H");
}

// ── P3: вырождение «одна непустая колонка» → грид, а не вертикальная полоса ────
{
  const nodes = [
    mk("hi", "a", { iid: 30 }),
    mk("lo", "a", { iid: 10 }),
    mk("mid", "a", { iid: 20 }),
  ];
  const laid = columnLayout(nodes, COLS, keyOf, {
    sort: (x, y) =>
      (x.data as { iid: number }).iid - (y.data as { iid: number }).iid,
  });
  const byId = Object.fromEntries(laid.map((n) => [n.id, n]));
  // НЕ одна вертикаль: задействован более чем один столбец по X
  const xs = new Set(laid.map((n) => n.position.x));
  ok(xs.size > 1, "P3: одна непустая колонка раскладывается сеткой (>1 столбца)");
  // порядок сортировки сохраняется в порядке чтения (row-major): lo→mid→hi
  const order = (n: Node) => n.position.y * 1e6 + n.position.x;
  ok(order(byId.lo) < order(byId.mid), "P3: lo раньше mid в порядке чтения");
  ok(order(byId.mid) < order(byId.hi), "P3: mid раньше hi в порядке чтения");
  // первая карточка — на старте сетки (HEADER сверху, X первой колонки)
  eq(byId.lo.position.y, HEADER, "P3: первая строка сетки = HEADER");
  eq(byId.lo.position.x, 20, "P3: сетка стартует с X первой колонки");
}

// ── сворачивание колонки: её ноды скрыты (hidden) ────────────────────────────
{
  const nodes = [mk("c1", "a"), mk("c2", "b")];
  const laid = columnLayout(nodes, COLS, keyOf, {
    collapsed: new Set(["a"]),
  });
  const byId = Object.fromEntries(laid.map((n) => [n.id, n]));
  ok(byId.c1.hidden === true, "нода свёрнутой колонки скрыта");
  ok(byId.c2.hidden === false, "нода открытой колонки видима");
  // X колонки B сдвинут влево из-за узкой свёрнутой A
  eq(byId.c2.position.x, 20 + COLLAPSED_W, "колонка B сдвинута к свёрнутой A");
}

// ── columnHeaders: координаты + флаг свёрнутости ─────────────────────────────
{
  const hs = columnHeaders(COLS, new Set(["b"]));
  eq(hs.length, 3, "три шапки");
  ok(hs[1].collapsed === true, "шапка B помечена свёрнутой");
  ok(hs[0].collapsed === false, "шапка A не свёрнута");
  // X шапки C учитывает узкую B
  eq(hs[2].x, 20 + COL_W + COLLAPSED_W, "X шапки C после узкой B");
}

console.log(`\nlayout.kanban: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
