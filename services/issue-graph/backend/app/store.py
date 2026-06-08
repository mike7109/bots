"""Локальное хранилище (SQLite) для «своего слоя» поверх GitLab.

GitLab на Free хранит только симметричную relates_to. Поверх неё мы держим
СВОЙ тип связи и направление:
  - relates  — симметричная (без направления);
  - blocks   — направленная (source блокирует target);
  - parent   — иерархия (source — родитель, target — дочерняя), single-parent.

В GitLab связь всегда создаётся как relates_to; наш слой говорит, чем она
является на самом деле и куда направлена. Ключ — неупорядоченная пара нод
("a|b"): между парой задач максимум одна связь.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(
    os.environ.get("ISSUE_GRAPH_DB", Path(__file__).parent.parent / "issue_graph.db")
)
KINDS = ("relates", "blocks", "parent")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.execute(
        "CREATE TABLE IF NOT EXISTS link_meta ("
        " pair TEXT PRIMARY KEY, kind TEXT NOT NULL,"
        " source TEXT NOT NULL, target TEXT NOT NULL)"
    )
    return c


def _pair(a: str, b: str) -> str:
    return "|".join(sorted([a, b]))


def set_link(kind: str, source: str, target: str) -> None:
    """Задать тип+направление связи. kind=relates → запись удаляем (дефолт)."""
    if kind not in KINDS:
        kind = "relates"
    with _conn() as c:
        if kind == "relates":
            c.execute("DELETE FROM link_meta WHERE pair=?", (_pair(source, target),))
        else:
            c.execute(
                "INSERT INTO link_meta(pair, kind, source, target) VALUES(?,?,?,?) "
                "ON CONFLICT(pair) DO UPDATE SET kind=excluded.kind, "
                "source=excluded.source, target=excluded.target",
                (_pair(source, target), kind, source, target),
            )


def delete_link(source: str, target: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM link_meta WHERE pair=?", (_pair(source, target),))


def get_links() -> dict[str, dict]:
    """Все оверлеи: pair -> {kind, source, target}."""
    with _conn() as c:
        rows = c.execute("SELECT pair, kind, source, target FROM link_meta").fetchall()
    return {r[0]: {"kind": r[1], "source": r[2], "target": r[3]} for r in rows}
