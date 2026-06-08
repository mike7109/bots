"""Тесты SWR-кэша и точечной инвалидации (этап 4 ROADMAP) — без сети.

Проверяем поведение кэша графа в пределах сессии:
  * `_spawn_refresh` перестраивает ключ в фоне и обновляет cache[key], не плодя
    дублей при параллельных вызовах;
  * `_invalidate_cache` НЕ очищает кэш (как раньше), а помечает ключи stale
    (ts=0) — чтобы следующий запрос отдал stale мгновенно и перестроил фоном;
  * точечная инвалидация по scope трогает только ключи этого scope (оба
    состояния), не задевая чужие.

Сеть/сессии не нужны: работаем с фейковым sess-dict и подменяем _build_graph.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# изолируем store-БД до импорта пакета (как в test_graph_parity)
_TMP_DB = Path("/tmp/issue-graph-swr-test.db")
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["ISSUE_GRAPH_DB"] = str(_TMP_DB)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main  # noqa: E402
from app.gitlab_client import Scope  # noqa: E402


def _fresh_sess() -> dict:
    return {"client": object(), "caps": None, "cache": {}, "cache_refreshing": set()}


def _set_default(sess: dict | None):
    """Подменить _default_session (резолвится, когда нет X-Session/cookie)."""
    main._default_session = sess


def test_scope_key_distinguishes_state():
    assert main._scope_key(Scope("group", "acme"), "opened") == "group:acme:opened"
    assert main._scope_key(Scope("group", "acme"), "all") == "group:acme:all"


def test_scope_key_distinguishes_assignee():
    """Этап 5: «Мои задачи» — отдельный, более узкий граф → отдельный ключ кэша."""
    full = main._scope_key(Scope("group", "acme"), "opened")
    mine = main._scope_key(Scope("group", "acme"), "opened", "alice")
    assert mine == "group:acme:opened:@alice"
    assert mine != full


def test_invalidate_scoped_touches_assignee_keys():
    """Инвалидация по scope помечает stale и узкие ключи по исполнителю."""
    sess = _fresh_sess()
    acme = Scope("group", "acme")
    k_full = main._scope_key(acme, "opened")
    k_mine = main._scope_key(acme, "opened", "alice")
    sess["cache"][k_full] = (100.0, {"nodes": ["full"], "edges": []})
    sess["cache"][k_mine] = (100.0, {"nodes": ["mine"], "edges": []})
    _set_default(sess)
    try:
        main._invalidate_cache(acme)
    finally:
        _set_default(None)
    assert sess["cache"][k_full][0] == 0.0
    assert sess["cache"][k_mine][0] == 0.0  # узкий ключ тоже stale


def test_spawn_refresh_updates_cache_in_background(monkeypatch):
    sess = _fresh_sess()
    scope = Scope("group", "acme")
    key = main._scope_key(scope, "opened")
    # старое (stale) значение в кэше
    sess["cache"][key] = (0.0, {"nodes": ["old"], "edges": []})

    built = {"nodes": ["new"], "edges": []}

    async def fake_build(s, sc, st, assignee=None):
        assert s is sess and sc is scope and st == "opened"
        return built

    monkeypatch.setattr(main, "_build_graph", fake_build)

    async def go():
        main._spawn_refresh(sess, scope, "opened")
        # дать фоновой задаче доработать
        for _ in range(50):
            await asyncio.sleep(0)
            if sess["cache"][key][1] is built:
                break

    asyncio.run(go())
    assert sess["cache"][key][1] is built
    assert sess["cache"][key][0] > 0  # ts обновился (стал свежим)
    assert key not in sess["cache_refreshing"]  # флаг снят


def test_spawn_refresh_dedupes_concurrent(monkeypatch):
    sess = _fresh_sess()
    scope = Scope("group", "acme")
    calls = 0
    release = asyncio.Event()

    async def fake_build(s, sc, st, assignee=None):
        nonlocal calls
        calls += 1
        await release.wait()  # держим первую задачу «в работе»
        return {"nodes": [], "edges": []}

    monkeypatch.setattr(main, "_build_graph", fake_build)

    async def go():
        # пока первый refresh «в работе», второй не должен создать дубль
        main._spawn_refresh(sess, scope, "opened")
        main._spawn_refresh(sess, scope, "opened")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        release.set()
        for _ in range(50):
            await asyncio.sleep(0)

    asyncio.run(go())
    assert calls == 1, f"ожидали один билд (дедуп), было {calls}"


def test_spawn_refresh_swallows_build_error(monkeypatch):
    sess = _fresh_sess()
    scope = Scope("group", "acme")
    key = main._scope_key(scope, "opened")
    sess["cache"][key] = (0.0, {"nodes": ["old"], "edges": []})

    async def boom(s, sc, st, assignee=None):
        raise RuntimeError("build failed")

    monkeypatch.setattr(main, "_build_graph", boom)

    async def go():
        main._spawn_refresh(sess, scope, "opened")
        for _ in range(50):
            await asyncio.sleep(0)

    asyncio.run(go())
    # stale-значение осталось, флаг снят (повторим при следующем чтении)
    assert sess["cache"][key][1] == {"nodes": ["old"], "edges": []}
    assert key not in sess["cache_refreshing"]


def test_invalidate_marks_stale_not_clears():
    """Инвалидация не выкидывает данные, а делает их stale (ts=0)."""
    sess = _fresh_sess()
    scope = Scope("group", "acme")
    k_open = main._scope_key(scope, "opened")
    k_all = main._scope_key(scope, "all")
    sess["cache"][k_open] = (100.0, {"nodes": ["o"], "edges": []})
    sess["cache"][k_all] = (100.0, {"nodes": ["a"], "edges": []})
    _set_default(sess)
    try:
        main._invalidate_cache()  # без scope → все ключи stale
    finally:
        _set_default(None)
    # данные на месте, но ts обнулён (stale)
    assert sess["cache"][k_open][1] == {"nodes": ["o"], "edges": []}
    assert sess["cache"][k_open][0] == 0.0
    assert sess["cache"][k_all][0] == 0.0


def test_invalidate_scoped_only_touches_that_scope():
    sess = _fresh_sess()
    acme = Scope("group", "acme")
    other = Scope("group", "other")
    k_acme = main._scope_key(acme, "opened")
    k_other = main._scope_key(other, "opened")
    sess["cache"][k_acme] = (100.0, {"nodes": ["acme"], "edges": []})
    sess["cache"][k_other] = (100.0, {"nodes": ["other"], "edges": []})
    _set_default(sess)
    try:
        main._invalidate_cache(acme)  # точечно по scope
    finally:
        _set_default(None)
    assert sess["cache"][k_acme][0] == 0.0       # затронут
    assert sess["cache"][k_other][0] == 100.0    # чужой scope не тронут
