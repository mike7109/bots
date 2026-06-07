/**
 * Семантический акцент ноды (левый край карточки + цвет на миникарте):
 * просрочено → красный, эпик → фиолетовый, закрыта → серый, иначе цвет
 * первой метки, без меток — нейтральный светло-серый.
 * Один источник правды для карточки (IssueNode) и MiniMap (nodeColor).
 */
export function nodeAccent(data: {
  type?: string;
  state?: string;
  due_date?: string | null;
  labels?: { color: string | null }[];
}): string {
  const closed = data.state === "closed";
  const isEpic = data.type === "epic";
  // просрочка важнее цвета метки — на миникарте видно «горящие» задачи
  if (!closed && isOverdue(data.due_date ?? null)) return "#d9534f";
  if (closed) return "#9aa0ad";
  if (isEpic) return "#6f42c1";
  return data.labels?.[0]?.color ?? "#d2d6e2";
}

/** Просрочена ли дата (строго в прошлом, по локальной полуночи). */
export function isOverdue(due: string | null): boolean {
  if (!due) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const d = new Date(due + "T00:00:00");
  return d.getTime() - today.getTime() < 0;
}

/** Контрастный цвет текста (тёмный/светлый) для заданного фона метки. */
export function readableText(bg: string | null): string {
  if (!bg) return "#1a1a1a";
  const h = bg.replace("#", "");
  if (h.length < 6) return "#1a1a1a";
  const r = parseInt(h.slice(0, 2), 16),
    g = parseInt(h.slice(2, 4), 16),
    b = parseInt(h.slice(4, 6), 16);
  // относительная яркость → тёмный текст на светлом фоне и наоборот
  return r * 0.299 + g * 0.587 + b * 0.114 > 150 ? "#1a1a1a" : "#ffffff";
}
