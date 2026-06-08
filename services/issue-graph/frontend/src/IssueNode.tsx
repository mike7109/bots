import { memo } from "react";
import { Handle, Position, useViewport, type NodeProps } from "reactflow";
import { nodeAccent, readableText } from "./colors";
import type { GNode } from "./types";

// ниже этого зума детальная карточка нечитаема → рисуем компакт (LOD)
const LOD_ZOOM = 0.45;

export interface NodeData extends GNode {
  dimmed: boolean;
  selected: boolean;
  linkTarget?: boolean;
  pulse?: boolean; // D3: кратко подсветить только что созданную ноду
  dir?: "lr" | "tb";
}

function dueStatus(due: string | null, closed: boolean): "overdue" | "soon" | "none" {
  if (!due || closed) return "none";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const d = new Date(due + "T00:00:00");
  const diff = Math.round((d.getTime() - today.getTime()) / 86_400_000);
  if (diff < 0) return "overdue";
  if (diff <= 1) return "soon"; // сегодня или завтра
  return "none";
}

export const IssueNode = memo(({ data }: NodeProps<NodeData>) => {
  const isEpic = data.type === "epic";
  const closed = data.state === "closed";
  const due = dueStatus(data.due_date, closed);
  // общий семантический акцент (карточка + миникарта): просрочка/эпик/закрыта/метка
  const accent = nodeAccent(data);
  // LOD: при сильном отдалении карточка нечитаема → компактный режим
  const { zoom } = useViewport();
  const lod = zoom < LOD_ZOOM;
  return (
    <div
      className={[
        "node-card",
        lod ? "lod-min" : "",
        isEpic ? "node-epic" : "",
        closed ? "node-closed" : "",
        due === "overdue" ? "node-overdue" : "",
        due === "soon" ? "node-soon" : "",
        data.dimmed ? "node-dimmed" : "",
        data.selected ? "node-selected" : "",
        data.linkTarget ? "node-linktarget" : "",
        data.pulse ? "node-pulse" : "",
      ].join(" ")}
      style={{ borderLeftColor: accent }}
    >
      {/* приём связи: невидим, целиться не надо (радиус притяжения) */}
      <Handle
        type="target"
        position={data.dir === "lr" ? Position.Left : Position.Top}
        className="conn-target"
        isConnectableStart={false}
      />
      {/* старт связи: явная кнопка у края, видна при наведении.
          тело карточки = выбрать (клик) / двигать (тянуть) */}
      <Handle
        type="source"
        position={data.dir === "lr" ? Position.Right : Position.Bottom}
        className="conn-btn"
        title="Потяните, чтобы связать с другой задачей"
      >
        <span className="conn-ic" aria-hidden>↗</span>
      </Handle>
      {lod ? (
        // компакт: точка статуса + #iid + цветная полоса метки (без title/чипов)
        <div className="lod-row">
          <span className={`state-dot ${closed ? "closed" : "open"}`} />
          <span className="node-ref">{isEpic ? "EPIC" : `#${data.iid}`}</span>
        </div>
      ) : (
      <>
      <div className="node-head">
        <span className={`state-dot ${closed ? "closed" : "open"}`} />
        <span className="node-ref">{isEpic ? "EPIC" : `#${data.iid}`}</span>
        {data.due_date && (
          <span className={`node-due due-${due}`} title="Срок выполнения">
            {due === "overdue" ? "⚠ " : due === "soon" ? "⏰ " : "📅 "}
            {data.due_date.slice(5)}
          </span>
        )}
        {data.milestone && (
          <span className="node-ms" title="Спринт / milestone">
            🏁 {data.milestone.title}
          </span>
        )}
      </div>
      <div className="node-title">{data.title}</div>
      <div className="node-foot">
        <div className="node-labels">
          {data.labels.slice(0, 3).map((l) => (
            <span
              key={l.name}
              className="chip"
              style={{
                background: l.color ?? "#888",
                color: readableText(l.color),
              }}
            >
              {l.name}
            </span>
          ))}
          {data.labels.length > 3 && (
            <span className="chip more">+{data.labels.length - 3}</span>
          )}
        </div>
        {data.assignees[0] && (
          <span className="node-assignee" title={data.assignees[0].name ?? ""}>
            {(data.assignees[0].name ?? "?")
              .split(" ")
              .map((s) => s[0])
              .join("")
              .slice(0, 2)}
          </span>
        )}
      </div>
      </>
      )}
    </div>
  );
});
