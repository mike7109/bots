"""FastAPI-прокси: фронт ходит только сюда, токен GitLab живёт на сервере."""
from __future__ import annotations

import asyncio
import contextvars
import time
from contextlib import asynccontextmanager

import secrets

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .config import (
    ADMIN_PASSWORD,
    FRONTEND_URL,
    Connection,
    Settings,
    get_bot,
    get_instance_url,
    get_oauth_app,
    get_pro,
    initial_connection,
    is_enabled,
    redirect_uri,
    save_instance,
    set_enabled,
    set_pro,
)
from . import store
from .gitlab_client import Capabilities, GitLabClient, Scope
from .graph import GraphBuilder, _issue_detail, _issue_to_node

settings = Settings.load()
# многопользовательский режим: у каждого браузера своя сессия (заголовок X-Session).
# _default_session — общее подключение из env/файла (если задано), для тех, у кого
# своей сессии ещё нет.
_sessions: dict[str, dict] = {}
_default_session: dict | None = None
_current_sid: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ig_sid", default=None
)
_oauth_pending: dict[str, str] = {}  # oauth state -> session id
SESSION_TTL = int(__import__("os").environ.get("SESSION_TTL", str(12 * 3600)))
COOKIE = "ig_sid"


def _resolve_session() -> dict | None:
    sid = _current_sid.get()
    if sid and sid in _sessions:
        sess = _sessions[sid]
        if time.time() > sess.get("expires", 0):  # TTL истёк
            _sessions.pop(sid, None)
            return None
        sess["expires"] = time.time() + SESSION_TTL  # sliding-продление
        return sess
    return _default_session


def _set_session_cookie(resp: Response, sid: str) -> None:
    resp.set_cookie(COOKIE, sid, max_age=SESSION_TTL, httponly=True,
                    samesite="lax", path="/")


async def make_session(conn: Connection, refresh_token: str | None = None) -> dict:
    """Создать сессию-подключение (свой GitLabClient, caps, кэш)."""
    cl = GitLabClient(conn.url, conn.token, settings.concurrency, conn.token_type)
    sess: dict = {"client": cl, "caps": None, "conn": conn, "cache": {},
                  # SWR (этап 4): ключи, чья фоновая перестройка уже запущена —
                  # чтобы параллельные читатели не плодили дублей create_task.
                  "cache_refreshing": set(),
                  "refresh_token": refresh_token,
                  "expires": time.time() + SESSION_TTL}
    if conn.token_type == "oauth":
        async def _refresh() -> str | None:  # refresh привязан к этой сессии
            app_ = get_oauth_app()
            gl_url = get_instance_url()
            rt = sess.get("refresh_token")
            if not app_ or not gl_url or not rt:
                return None
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post(f"{gl_url}/oauth/token", data={
                    "grant_type": "refresh_token", "refresh_token": rt,
                    "client_id": app_["client_id"],
                    "client_secret": app_["client_secret"],
                    "redirect_uri": redirect_uri()})
            if r.status_code >= 300:
                return None
            tok = r.json()
            sess["refresh_token"] = tok.get("refresh_token", rt)
            return tok["access_token"]
        cl.set_refresh_cb(_refresh)
    sess["caps"] = await cl.detect_capabilities(get_pro())  # бросит, если невалидно
    try:
        u = await cl.get_json("/user")
        sess["user"] = {
            "username": u.get("username"),
            "name": u.get("name"),
            "avatar_url": u.get("avatar_url"),
            "web_url": u.get("web_url"),
        }
    except httpx.HTTPError:
        sess["user"] = None
    return sess


async def attach_session(sid: str, conn: Connection,
                         refresh_token: str | None = None) -> Capabilities:
    """Подключить сессию (только в памяти; токены пользователей не пишем в файл)."""
    sess = await make_session(conn, refresh_token)
    old = _sessions.get(sid)
    _sessions[sid] = sess
    if old and old.get("client"):
        await old["client"].aclose()
    return sess["caps"]


async def _default_scope(cl: GitLabClient) -> Scope | None:
    """Дефолтный scope для прогрева: первая доступная группа (как и фронт).

    Фронт по умолчанию открывает первую группу — греем её же, чтобы первый заход
    попал в тёплый кэш. Любая ошибка → None (прогрев просто не делаем).
    """
    try:
        groups = await cl.paginate(
            "/groups", params={"all_available": "true", "order_by": "path"})
    except httpx.HTTPError:
        return None
    if not groups:
        return None
    return Scope("group", _enc(groups[0]["full_path"]))


async def _warm_default_scope(sess: dict) -> None:
    """Фоновый прогрев дефолтного scope (этап 4): собрать граф и положить в кэш.

    Запускается фоном из lifespan — не блокирует старт сервера. Греем только
    `state=opened` (дефолт D1). Ошибки глотаем: прогрев — оптимизация, не гарантия.
    """
    try:
        cl = sess.get("client")
        if not cl:
            return
        scope = await _default_scope(cl)
        if not scope:
            return
        data = await _build_graph(sess, scope, "opened")
        sess["cache"][_scope_key(scope, "opened")] = (time.monotonic(), data)
    except Exception:  # noqa: BLE001 — прогрев не должен ронять старт
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _default_session
    conn = initial_connection()
    if conn:
        try:
            _default_session = await make_session(conn)
            # этап 4: прогрев дефолтного scope в фоне (старт не блокируем)
            asyncio.create_task(_warm_default_scope(_default_session))
        except Exception:  # noqa: BLE001 — стартуем «не настроенными»
            _default_session = None
    yield
    for s in list(_sessions.values()):
        if s.get("client"):
            await s["client"].aclose()
    if _default_session and _default_session.get("client"):
        await _default_session["client"].aclose()


app = FastAPI(title="issue-graph", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


_ALWAYS_ON = ("/api/config", "/api/admin", "/api/oauth", "/api/health", "/api/logout")


@app.middleware("http")
async def _session_mw(request, call_next):
    # сессия — из httpOnly-cookie; X-Session как fallback (curl/тесты)
    sid = request.cookies.get(COOKIE) or request.headers.get("X-Session")
    path = request.url.path
    # глобальный рубильник: когда выключено — недоступно всё, кроме служебного/админки
    if (
        path.startswith("/api/")
        and not path.startswith(_ALWAYS_ON)
        and not is_enabled()
    ):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Приложение временно отключено"}, status_code=503)
    tok = _current_sid.set(sid)
    try:
        return await call_next(request)
    finally:
        _current_sid.reset(tok)


def client() -> GitLabClient:
    s = _resolve_session()
    if not s or not s.get("client"):
        raise HTTPException(409, "GitLab не настроен — войдите в настройках")
    return s["client"]


def _caps_for(sess: dict | None) -> Capabilities:
    """Caps конкретной сессии + динамический PRO-оверлей (без contextvar).

    Нужен фону (SWR-refresh/прогрев): там contextvar уже сброшен, и `caps()` мог бы
    взять не ту сессию. Принимаем sess явно.
    """
    c = sess["caps"] if sess and sess.get("caps") else Capabilities()
    # режим PRO задаёт админ — применяем динамически (переключение без релогина)
    pro = get_pro()
    c.tier = "premium" if pro else "free"
    c.epics = pro
    c.blocking_links = pro
    c.iterations = pro
    return c


def caps() -> Capabilities:
    return _caps_for(_resolve_session())


def _cache() -> dict:
    s = _resolve_session()
    return s["cache"] if s else {}


def _scope_key(scope: Scope, state: str, assignee: str | None = None) -> str:
    """Ключ кэша графа: «открытые»/«все» и набор по исполнителю — разные графы.

    Этап 5: «Мои задачи» (assignee) — отдельный, более узкий граф, поэтому
    входит в ключ кэша, иначе бы перетирал/отдавал полный набор группы.
    """
    base = f"{scope.kind}:{scope.id}:{state}"
    return f"{base}:@{assignee}" if assignee else base


def _build_graph(sess: dict, scope: Scope, state: str,
                 assignee: str | None = None):
    """Собрать граф конкретной сессии (не зависит от contextvar — нужен фону)."""
    cl = sess.get("client")
    if not cl:
        raise HTTPException(409, "GitLab не настроен")
    return GraphBuilder(cl, _caps_for(sess)).build(scope, state, assignee)


def _spawn_refresh(sess: dict, scope: Scope, state: str,
                   assignee: str | None = None) -> None:
    """SWR: перестроить ключ в фоне и обновить cache[key]. Дубли отсекаем по ключу.

    Ошибки фона не валят запрос пользователя — оставляем прежний stale-результат
    и просто снимаем флаг (попробуем снова при следующем чтении).
    """
    key = _scope_key(scope, state, assignee)
    refreshing: set = sess.setdefault("cache_refreshing", set())
    if key in refreshing:
        return
    refreshing.add(key)

    async def run() -> None:
        try:
            data = await _build_graph(sess, scope, state, assignee)
            sess["cache"][key] = (time.monotonic(), data)
        except Exception:  # noqa: BLE001 — фон не должен ронять процесс
            pass
        finally:
            refreshing.discard(key)

    asyncio.create_task(run())


def _invalidate_cache(scope: Scope | None = None) -> None:
    """Инвалидация кэша сессии после мутации.

    Этап 4: вместо полной очистки помечаем затронутые ключи stale (ts в прошлое),
    чтобы следующий запрос отдал stale мгновенно и перестроил фоном (SWR), а не
    блокировал пользователя синхронным релоудом. Если `scope` известен — точечно
    по обоим состояниям этого scope; иначе (issue-мутации, попадающие в разные
    групповые scope) — по всем ключам сессии.
    """
    s = _resolve_session()
    if not s:
        return
    cache: dict = s["cache"]
    if scope is not None:
        # Этап 5: помечаем stale ВСЕ ключи этого scope — и общие («открытые»/
        # «все»), и узкие по исполнителю (`...:@username`), иначе вид «Мои задачи»
        # остался бы с устаревшим набором после своей же правки.
        prefix = f"{scope.kind}:{scope.id}:"
        keys = [k for k in cache if k.startswith(prefix)]
    else:
        keys = list(cache.keys())
    for k in keys:
        entry = cache.get(k)
        if entry:
            _ts, data = entry
            cache[k] = (0.0, data)  # ts=0 → гарантированно stale при следующем чтении


@app.get("/api/health")
async def health():
    return {"status": "ok", "gitlab_version": caps().version}


@app.get("/api/capabilities")
async def capabilities():
    return caps().as_dict()


@app.get("/api/config")
async def get_config():
    """Статус для пользователя: настроен ли инстанс админом и вошёл ли я."""
    s = _resolve_session()
    conn = s.get("conn") if s else None
    instance_url = get_instance_url()
    oauth = get_oauth_app()
    return {
        "app_enabled": is_enabled(),
        "instance_configured": bool(instance_url),
        "oauth_available": bool(instance_url and oauth and oauth.get("client_id")),
        "bot_available": bool(get_bot()),
        "admin_enabled": bool(ADMIN_PASSWORD),
        "configured": bool(s and s.get("client")),
        "url": conn.url if conn else None,
        "auth_type": conn.token_type if conn else None,
        "capabilities": caps().as_dict() if s and s.get("caps") else None,
        "user": s.get("user") if s else None,
    }


@app.post("/api/logout")
async def logout(response: Response):
    """Выйти: закрыть сессию текущего браузера и стереть cookie."""
    sid = _current_sid.get()
    if sid and sid in _sessions:
        sess = _sessions.pop(sid)
        if sess.get("client"):
            await sess["client"].aclose()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


async def _fetch_overview(cl: GitLabClient, limit: int = 50) -> list[dict]:
    """Краткая статистика по доступным группам: проекты, задачи (открытые/всего)."""
    import asyncio as _asyncio

    groups = await cl.paginate(
        "/groups", params={"all_available": "true", "order_by": "path"}
    )

    async def one(g: dict) -> dict:
        gid = g["id"]
        projects, total, opened = await _asyncio.gather(
            cl.count(f"/groups/{gid}/projects", {}),
            cl.count(f"/groups/{gid}/issues", {"state": "all"}),
            cl.count(f"/groups/{gid}/issues", {"state": "opened"}),
        )
        return {
            "id": gid,
            "full_path": g["full_path"],
            "projects": projects,
            "issues_total": total,
            "issues_opened": opened,
        }

    return await _asyncio.gather(*(one(g) for g in groups[:limit]))


@app.get("/api/overview")
async def overview():
    try:
        return {"groups": await _fetch_overview(client())}
    except httpx.HTTPError as e:
        raise HTTPException(502, f"GitLab error: {e}") from e


# ── Админка (инстанс-настройки, доступ по паролю) ───────────────────────────
_admin_tokens: dict[str, float] = {}  # token -> expires
ADMIN_COOKIE = "ig_admin"
ADMIN_TTL = 8 * 3600


def require_admin(request) -> None:
    tok = request.cookies.get(ADMIN_COOKIE)
    exp = _admin_tokens.get(tok or "")
    if not tok or not exp or time.time() > exp:
        raise HTTPException(401, "Требуется вход администратора")
    _admin_tokens[tok] = time.time() + ADMIN_TTL


class AdminLogin(BaseModel):
    password: str


@app.get("/api/admin/status")
async def admin_status(request: Request):
    tok = request.cookies.get(ADMIN_COOKIE)
    logged = bool(tok and _admin_tokens.get(tok, 0) > time.time())
    return {"enabled": bool(ADMIN_PASSWORD), "logged_in": logged}


@app.post("/api/admin/login")
async def admin_login(body: AdminLogin, response: Response):
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "Админка отключена (не задан ADMIN_PASSWORD)")
    if not secrets.compare_digest(body.password, ADMIN_PASSWORD):
        raise HTTPException(403, "Неверный пароль")
    tok = secrets.token_urlsafe(24)
    _admin_tokens[tok] = time.time() + ADMIN_TTL
    response.set_cookie(ADMIN_COOKIE, tok, max_age=ADMIN_TTL, httponly=True,
                        samesite="lax", path="/")
    return {"ok": True}


class AdminEnabledIn(BaseModel):
    enabled: bool


@app.post("/api/admin/enabled")
async def admin_set_enabled(body: AdminEnabledIn, request: Request):
    """Глобальный рубильник: включить/выключить приложение для всех."""
    require_admin(request)
    set_enabled(body.enabled)
    return {"enabled": body.enabled}


@app.get("/api/admin/config")
async def admin_get_config(request: Request):
    require_admin(request)
    oauth = get_oauth_app() or {}
    bot = get_bot() or {}
    return {
        "app_enabled": is_enabled(),
        "pro": get_pro(),
        "gitlab_url": get_instance_url(),
        "oauth_client_id": oauth.get("client_id"),
        "has_oauth_secret": bool(oauth.get("client_secret")),
        "bot_url": bot.get("url"),
        "has_bot_secret": bool(bot.get("secret")),
        "redirect_uri": redirect_uri(),
    }


class AdminProIn(BaseModel):
    pro: bool


@app.post("/api/admin/pro")
async def admin_set_pro(body: AdminProIn, request: Request):
    """Режим PRO (Premium/Ultimate): включает epics/blocking/iterations."""
    require_admin(request)
    set_pro(body.pro)
    return {"pro": body.pro}


class AdminConfigIn(BaseModel):
    gitlab_url: str
    oauth_client_id: str = ""
    oauth_client_secret: str = ""  # пусто = не менять
    bot_url: str = ""
    bot_secret: str = ""  # пусто = не менять (если bot_url задан)


@app.post("/api/admin/config")
async def admin_set_config(body: AdminConfigIn, request: Request):
    require_admin(request)
    save_instance(
        gitlab_url=body.gitlab_url,
        client_id=body.oauth_client_id or None,
        client_secret=body.oauth_client_secret or None,
        bot_url=body.bot_url,
        bot_secret=body.bot_secret or None,
    )
    return {"ok": True}


# ── OAuth «Войти через GitLab» (использует инстанс-настройки админа) ─────────
@app.get("/api/oauth/start")
async def oauth_start():
    """Начать OAuth-флоу: редирект на страницу авторизации GitLab."""
    app_ = get_oauth_app()
    gl_url = get_instance_url()
    if not app_ or not app_.get("client_id") or not gl_url:
        raise HTTPException(400, "OAuth не настроен администратором")
    state = secrets.token_urlsafe(24)
    _oauth_pending[state] = secrets.token_urlsafe(24)  # state -> будущий session id
    from urllib.parse import urlencode
    q = urlencode({
        "client_id": app_["client_id"],
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "api",
        "state": state,
    })
    return RedirectResponse(f"{gl_url}/oauth/authorize?{q}")


@app.get("/api/oauth/callback")
async def oauth_callback(code: str | None = None, state: str | None = None,
                         error: str | None = None):
    """GitLab вернул код → меняем на токен и подключаемся."""
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/?oauth_error={error}")
    sid = _oauth_pending.pop(state, None) if state else None
    if not code or not sid:
        return RedirectResponse(f"{FRONTEND_URL}/?oauth_error=bad_state")
    app_ = get_oauth_app()
    gl_url = get_instance_url()
    if not app_ or not gl_url:
        return RedirectResponse(f"{FRONTEND_URL}/?oauth_error=no_app")
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{gl_url}/oauth/token", data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": app_["client_id"],
                "client_secret": app_["client_secret"],
                "redirect_uri": redirect_uri(),
            })
        r.raise_for_status()
        tok = r.json()
        await attach_session(
            sid,
            Connection(url=gl_url, token=tok["access_token"], token_type="oauth"),
            refresh_token=tok.get("refresh_token"),
        )
    except (httpx.HTTPError, KeyError):
        return RedirectResponse(f"{FRONTEND_URL}/?oauth_error=exchange_failed")
    resp = RedirectResponse(f"{FRONTEND_URL}/?oauth=ok")
    _set_session_cookie(resp, sid)
    return resp


@app.get("/api/namespaces")
async def namespaces():
    """Список групп и проектов для выбора области графа."""
    try:
        groups = await client().paginate(
            "/groups", params={"all_available": "true", "order_by": "path"})
        projects = await client().paginate(
            "/projects", params={"membership": "true", "order_by": "path",
                                 "simple": "true"})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"GitLab error: {e}") from e
    return {
        "groups": [
            {"id": g["id"], "full_path": g["full_path"], "name": g["name"]}
            for g in groups
        ],
        "projects": [
            {"id": p["id"], "full_path": p["path_with_namespace"],
             "name": p["name"]}
            for p in projects
        ],
    }


@app.get("/api/graph")
async def graph(
    group: str | None = Query(None, description="id или full_path группы"),
    project: str | None = Query(None, description="id или full_path проекта"),
    state: str = Query("opened", description="opened (по умолчанию) или all"),
    assignee: str | None = Query(
        None,
        description='исполнитель: "me" (мои задачи) или username; пусто = все'),
    nocache: bool = False,
):
    if bool(group) == bool(project):
        raise HTTPException(400, "Передайте ровно один из параметров: group ИЛИ project")
    if state not in ("opened", "all"):
        state = "opened"
    scope = Scope("group", _enc(group)) if group else Scope("project", _enc(project))

    sess = _resolve_session()
    # Этап 5: «Мои задачи» — "me" резолвим в username вошедшего пользователя.
    # PAT-фолбэк (нет user в сессии) → фильтр игнорируем, отдаём обычный вид.
    assignee = (assignee or "").strip() or None
    if assignee == "me":
        me = (sess.get("user") or {}) if sess else {}
        assignee = me.get("username") or None
    # D1: состояние входит в ключ кэша («открытые»/«все» — разные графы);
    # Этап 5: исполнитель тоже — «Мои задачи» это отдельный, более узкий граф.
    key = _scope_key(scope, state, assignee)

    cache = sess["cache"] if sess else {}
    if not nocache and key in cache:
        ts, data = cache[key]
        if time.monotonic() - ts < settings.cache_ttl:
            return data  # свежий — отдаём как есть
        # stale-while-revalidate: отдаём устаревший мгновенно, перестраиваем фоном
        if sess:
            _spawn_refresh(sess, scope, state, assignee)
        return data

    # ключа нет (первая загрузка) или nocache — собираем синхронно (нечего отдать)
    try:
        data = await _build_graph(
            sess or {"client": client()}, scope, state, assignee)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code,
                            f"GitLab: {e.response.text[:200]}") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"GitLab error: {e}") from e
    if sess is not None:
        cache[key] = (time.monotonic(), data)
    return data


# ── Фаза 3: запись обратно в GitLab ─────────────────────────────────────────
class NewIssue(BaseModel):
    project_id: int
    title: str
    description: str | None = None
    labels: list[str] = []
    milestone_id: int | None = None
    assignee_ids: list[int] = []


class NewLink(BaseModel):
    project_id: int
    iid: int
    target_project_id: int
    target_iid: int
    kind: str = "relates"  # relates | blocks | parent (наш тип поверх relates_to)


class LinkMetaIn(BaseModel):
    source: str  # node id "P:I"
    target: str
    kind: str  # relates | blocks | parent


class IssuePatch(BaseModel):
    milestone_id: int | None = None       # None => снять спринт
    state_event: str | None = None        # close | reopen
    assignee_ids: list[int] | None = None  # [] => снять исполнителей
    due_date: str | None = None           # "YYYY-MM-DD"; передан+пусто => снять
    # Этап 6 (паритет): инлайн-правки прямо из карточки графа.
    add_labels: list[str] | None = None     # добавить метки (инкрементально)
    remove_labels: list[str] | None = None  # снять метки (инкрементально)
    weight: int | None = None             # вес (Premium); передан+null => снять
    description: str | None = None        # описание (markdown-исходник)


@app.post("/api/issues")
async def create_issue(body: NewIssue):
    payload: dict = {"title": body.title}
    if body.description:
        payload["description"] = body.description
    if body.labels:
        payload["labels"] = ",".join(body.labels)
    if body.milestone_id is not None:
        payload["milestone_id"] = body.milestone_id
    if body.assignee_ids:
        payload["assignee_ids"] = body.assignee_ids
    try:
        r = await client().post(
            f"/projects/{body.project_id}/issues", json=payload)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text[:300]) from e
    _invalidate_cache()
    return _issue_to_node(r.json())


@app.post("/api/links")
async def create_link(body: NewLink):
    """В GitLab всегда создаём relates_to (Free); тип/направление (blocks/parent)
    кладём в наш слой (store)."""
    r = await client().post(
        f"/projects/{body.project_id}/issues/{body.iid}/links",
        json={"target_project_id": body.target_project_id,
              "target_issue_iid": body.target_iid, "link_type": "relates_to"})
    if r.status_code >= 300:
        raise HTTPException(r.status_code, r.text[:300])
    src = f"{body.project_id}:{body.iid}"
    tgt = f"{body.target_project_id}:{body.target_iid}"
    store.set_link(body.kind, src, tgt)
    _invalidate_cache()
    body_json = r.json() if r.content else {}
    link_id = body_json.get("issue_link_id") or body_json.get("id")
    return {"ok": True, "kind": body.kind, "link_id": link_id}


@app.post("/api/links/meta")
async def set_link_meta(body: LinkMetaIn):
    """Сменить тип/направление существующей связи (наш слой)."""
    store.set_link(body.kind, body.source, body.target)
    _invalidate_cache()
    return {"ok": True, "kind": body.kind}


@app.delete("/api/links/{project_id}/{iid}/{link_id}")
async def delete_link(project_id: int, iid: int, link_id: int,
                      target: str | None = Query(None)):
    """Удалить связь по id; target ("P:I") — чтобы убрать и наш оверлей."""
    r = await client().delete(
        f"/projects/{project_id}/issues/{iid}/links/{link_id}"
    )
    if r.status_code >= 300:
        raise HTTPException(r.status_code, r.text[:300])
    if target:
        store.delete_link(f"{project_id}:{iid}", target)
    _invalidate_cache()
    return {"ok": True}


def _patch_payload(body: IssuePatch) -> dict:
    """Собрать тело PUT к GitLab из IssuePatch. Выделено, чтобы переиспользовать
    в одиночном PATCH и в bulk (Этап 7) — одинаковая семантика снятия/инкремента."""
    payload: dict = {}
    if "milestone_id" in body.model_fields_set:
        payload["milestone_id"] = body.milestone_id if body.milestone_id else 0
    if body.state_event:
        payload["state_event"] = body.state_event
    if body.assignee_ids is not None:
        # пустой список снимает исполнителей; GitLab ждёт [0] для очистки
        payload["assignee_ids"] = body.assignee_ids or [0]
    if "due_date" in body.model_fields_set:
        # пустая строка/None снимает срок в GitLab
        payload["due_date"] = body.due_date or ""
    # Этап 6: инлайн-метки. add_labels/remove_labels у GitLab инкрементальны и
    # совместимы в одном PUT — добавляем/снимаем точечно, не перетирая остальные.
    if body.add_labels:
        payload["add_labels"] = ",".join(body.add_labels)
    if body.remove_labels:
        payload["remove_labels"] = ",".join(body.remove_labels)
    if "weight" in body.model_fields_set:
        # null снимает вес (GitLab трактует пустое значение как «нет веса»)
        payload["weight"] = body.weight if body.weight is not None else ""
    if "description" in body.model_fields_set:
        payload["description"] = body.description or ""
    return payload


@app.patch("/api/issues/{project_id}/{iid}")
async def patch_issue(project_id: int, iid: int, body: IssuePatch):
    """Назначение/снятие спринта (milestone) и закрытие — для планирования."""
    payload = _patch_payload(body)
    if not payload:
        raise HTTPException(400, "Нечего обновлять")
    r = await client().put(
        f"/projects/{project_id}/issues/{iid}", json=payload)
    if r.status_code >= 300:
        raise HTTPException(r.status_code, r.text[:300])
    _invalidate_cache()
    return _issue_to_node(r.json())


class BulkPatch(BaseModel):
    """Массовое действие лассо (Этап 7): один набор изменений ко многим задачам.

    `targets` — список (project_id, iid). `patch` — те же поля, что у одиночного
    PATCH (сменить спринт/исполнителя/закрыть и т.п.). Применяем под семафором
    клиента (параллельно, но не лавиной), частичные ошибки не валят всю операцию —
    возвращаем по каждой задаче ok/ошибку, фронт показывает прогресс/что не вышло.
    """
    targets: list[tuple[int, int]]
    patch: IssuePatch


@app.post("/api/issues/bulk")
async def bulk_patch_issues(body: BulkPatch):
    """Массовый PATCH по семафору с частичными ошибками (Этап 7 ROADMAP)."""
    payload = _patch_payload(body.patch)
    if not payload:
        raise HTTPException(400, "Нечего обновлять")
    if not body.targets:
        raise HTTPException(400, "Нет задач для изменения")
    cl = client()

    async def one(project_id: int, iid: int) -> dict:
        # параллелизм ограничен общим семафором GitLabClient (см. .put → .request),
        # поэтому здесь дополнительный семафор не нужен — лавины запросов не будет.
        try:
            r = await cl.put(
                f"/projects/{project_id}/issues/{iid}", json=payload)
            if r.status_code >= 300:
                return {"project_id": project_id, "iid": iid,
                        "ok": False, "error": r.text[:200]}
            return {"project_id": project_id, "iid": iid,
                    "ok": True, "node": _issue_to_node(r.json())}
        except httpx.HTTPError as e:  # сеть/таймаут одной задачи не валит пачку
            return {"project_id": project_id, "iid": iid,
                    "ok": False, "error": str(e)[:200]}

    results = await asyncio.gather(
        *(one(pid, iid) for pid, iid in body.targets))
    _invalidate_cache()
    ok = sum(1 for r in results if r["ok"])
    return {"ok": ok == len(results), "succeeded": ok,
            "failed": len(results) - ok, "results": results}


class MoveIssue(BaseModel):
    """Перенос задачи в другой проект (Этап 8, Kanban: колонки-проекты).

    GitLab переносит задачу отдельной операцией `POST .../issues/{iid}/move`
    (меняется iid и project_id), а не обычным PATCH — поэтому отдельный эндпоинт.
    """
    to_project_id: int


@app.post("/api/issues/{project_id}/{iid}/move")
async def move_issue(project_id: int, iid: int, body: MoveIssue):
    """Перенести задачу в другой проект (Этап 8 ROADMAP, опционально).

    Возвращает обновлённую ноду (у неё уже новый iid/project_id), чтобы фронт мог
    переложить её в колонку-проект. Ошибки GitLab (нет прав/нельзя перенести)
    отдаём как есть, не валя весь холст."""
    if body.to_project_id == project_id:
        raise HTTPException(400, "Задача уже в этом проекте")
    try:
        r = await client().post(
            f"/projects/{project_id}/issues/{iid}/move",
            json={"to_project_id": body.to_project_id})
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text[:300]) from e
    _invalidate_cache()
    return _issue_to_node(r.json())


@app.get("/api/issues/{project_id}/{iid}")
async def issue_detail(project_id: int, iid: int):
    """Детали задачи (Этап 6): description, weight, time_stats, confidential,
    health_status, start_date, reference. Фронт зовёт при клике на ноду.

    Связанные MR (`/related_merge_requests` + `/closed_by`) тянем параллельно;
    их отсутствие/недоступность не валит карточку — отдаём пустой список.
    """
    cl = client()
    try:
        issue = await cl.get_json(f"/projects/{project_id}/issues/{iid}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code,
                            f"GitLab: {e.response.text[:200]}") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"GitLab error: {e}") from e

    async def safe_mrs(path: str) -> list[dict]:
        # связанные/закрывающие MR — приятный бонус, но их недоступность
        # (нет прав/старый CE) не должна ронять детали задачи
        try:
            return await cl.get_json(path)
        except httpx.HTTPError:
            return []

    related, closed_by = await asyncio.gather(
        safe_mrs(f"/projects/{project_id}/issues/{iid}/related_merge_requests"),
        safe_mrs(f"/projects/{project_id}/issues/{iid}/closed_by"),
    )
    detail = _issue_detail(issue)
    detail["merge_requests"] = _merge_merge_requests(related, closed_by)
    return detail


def _merge_merge_requests(related: list[dict], closed_by: list[dict]) -> list[dict]:
    """Объединить связанные и закрывающие MR в один список без дублей.

    `closed_by` помечаем флагом `closes` — это MR, который закроет задачу при
    мердже (полезный сигнал в карточке). Дедуп по id (один MR может быть в обоих).
    """
    closes_ids = {mr.get("id") for mr in closed_by}
    by_id: dict = {}
    for mr in (*related, *closed_by):
        mid = mr.get("id")
        if mid is None or mid in by_id:
            continue
        by_id[mid] = {
            "id": mid,
            "iid": mr.get("iid"),
            "title": mr.get("title"),
            "state": mr.get("state"),  # opened | closed | merged | locked
            "web_url": mr.get("web_url"),
            "reference": (mr.get("references") or {}).get("full"),
            "closes": mid in closes_ids,
        }
    return list(by_id.values())


@app.get("/api/issues/{project_id}/{iid}/notes")
async def issue_notes(project_id: int, iid: int):
    """Комментарии к задаче (без системных записей)."""
    try:
        notes = await client().paginate(
            f"/projects/{project_id}/issues/{iid}/notes",
            params={"sort": "asc", "order_by": "created_at"},
        )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"GitLab error: {e}") from e
    return [
        {
            "id": n["id"],
            "body": n["body"],
            "created_at": n["created_at"],
            "author": {
                "name": n["author"]["name"],
                "username": n["author"]["username"],
                "avatar_url": n["author"].get("avatar_url"),
            },
        }
        for n in notes
        if not n.get("system")
    ]


class NewNote(BaseModel):
    body: str


@app.post("/api/issues/{project_id}/{iid}/notes")
async def add_note(project_id: int, iid: int, body: NewNote):
    r = await client().post(
        f"/projects/{project_id}/issues/{iid}/notes",
        json={"body": body.body},
    )
    if r.status_code >= 300:
        raise HTTPException(r.status_code, r.text[:300])
    return {"ok": True}


class NewMilestone(BaseModel):
    title: str
    start_date: str | None = None
    due_date: str | None = None


@app.post("/api/milestones")
async def create_milestone(
    body: NewMilestone,
    group: str | None = Query(None),
    project: str | None = Query(None),
):
    """Создать спринт (milestone) в группе или проекте."""
    if bool(group) == bool(project):
        raise HTTPException(400, "Передайте ровно один из: group | project")
    scope = Scope("group", _enc(group)) if group else Scope("project", _enc(project))
    base = f"/groups/{scope.id}" if scope.kind == "group" else f"/projects/{scope.id}"
    payload = {"title": body.title}
    if body.start_date:
        payload["start_date"] = body.start_date
    if body.due_date:
        payload["due_date"] = body.due_date
    r = await client().post(f"{base}/milestones", json=payload)
    if r.status_code >= 300:
        raise HTTPException(r.status_code, r.text[:300])
    _invalidate_cache(scope)  # точечно: затронут только этот scope
    m = r.json()
    return {"id": m["id"], "title": m["title"]}


@app.get("/api/scope-meta")
async def scope_meta(
    group: str | None = Query(None), project: str | None = Query(None)):
    """Метаданные области для форм: milestones, метки, участники, проекты."""
    if bool(group) == bool(project):
        raise HTTPException(400, "Передайте ровно один из: group | project")
    scope = Scope("group", _enc(group)) if group else Scope("project", _enc(project))
    base = f"/groups/{scope.id}" if scope.kind == "group" else f"/projects/{scope.id}"

    async def safe(path: str, params: dict | None = None) -> list[dict]:
        # часть метаданных может быть запрещена обычному юзеру (напр. members/all
        # для не-участника) — это не повод ронять всю карточку, отдаём пусто
        try:
            return await client().paginate(path, params=params)
        except httpx.HTTPError:
            return []

    milestones = await safe(f"{base}/milestones", {"state": "active"})
    members = await safe(f"{base}/members/all")
    labels = await safe(f"{base}/labels")
    projects = await safe(f"{base}/projects", {"simple": "true"}) \
        if scope.kind == "group" else []
    return {
        "milestones": [{"id": m["id"], "title": m["title"]} for m in milestones],
        "members": [{"id": u["id"], "name": u["name"], "username": u["username"]}
                    for u in members],
        "labels": [{"name": la["name"], "color": la.get("color")} for la in labels],
        "projects": [{"id": p["id"], "name": p["name"],
                      "full_path": p["path_with_namespace"]} for p in projects],
    }


# ── Этап 12 (D7): импорт Kanban-доски из GitLab ─────────────────────────────
def _normalize_board(b: dict, lists: list[dict] | None = None) -> dict:
    """GitLab-доска → наш формат {id, name, hide_backlog, hide_closed, columns}.

    Колонка — это только список с `label` (status::todo и т.п.); спец-списки
    Open(backlog)/Closed на доске задаются флагами hide_*, их колонками не считаем.
    `list_type` в inline-ответе бывает null, поэтому ориентируемся на наличие
    `label`. Колонки отсортированы по `position`.
    """
    raw = lists if lists is not None else (b.get("lists") or [])
    cols = []
    for ls in raw:
        la = ls.get("label")
        if not la:  # backlog/closed/assignee/milestone-список — не label-колонка
            continue
        cols.append({
            "label": la.get("name"),
            "color": la.get("color"),
            "position": ls.get("position", 0),
        })
    cols.sort(key=lambda c: c["position"])
    return {
        "id": b.get("id"),
        "name": b.get("name"),
        "hide_backlog": bool(b.get("hide_backlog_list")),
        "hide_closed": bool(b.get("hide_closed_list")),
        "columns": cols,
    }


@app.get("/api/boards")
async def boards(
    group: str | None = Query(None, description="id или full_path группы"),
    project: str | None = Query(None, description="id или full_path проекта"),
):
    """Доски GitLab выбранной области (Этап 12 D7) для режима «Доска».

    Параметры как в /api/graph: ровно один из group|project. Любая ошибка
    (нет досок / 403 / старый CE без эндпоинта) → {boards: []}, чтобы фронт мог
    деградировать к ручному Open|Closed, а не падать.
    """
    if bool(group) == bool(project):
        raise HTTPException(400, "Передайте ровно один из: group | project")
    scope = Scope("group", _enc(group)) if group else Scope("project", _enc(project))
    base = f"/groups/{scope.id}" if scope.kind == "group" else f"/projects/{scope.id}"
    cl = client()
    try:
        raw = await cl.get_json(f"{base}/boards")
    except httpx.HTTPError:
        return {"boards": []}
    if not isinstance(raw, list) or not raw:
        return {"boards": []}

    async def normalize(b: dict) -> dict:
        # обычно lists приходят inline; если нет — дотягиваем отдельным запросом
        if b.get("lists") is None:
            try:
                lists = await cl.get_json(f"{base}/boards/{b['id']}/lists")
            except httpx.HTTPError:
                lists = []
            return _normalize_board(b, lists)
        return _normalize_board(b)

    return {"boards": await asyncio.gather(*(normalize(b) for b in raw))}


# ── Уведомления через внешний бот («Пнуть исполнителя») ─────────────────────
# Бот настраивается админом (инстанс-уровень: url + webhook_secret).
class PokeIn(BaseModel):
    message: str | None = None


@app.post("/api/notify/{project_id}/{iid}")
async def poke_assignee(project_id: int, iid: int, body: PokeIn):
    """Пнуть исполнителя задачи через внешний бот. Без настройки бота — ошибка."""
    bot = get_bot()
    if not bot or not bot.get("url"):
        raise HTTPException(400, "Бот уведомлений не настроен администратором")
    try:
        issue = await client().get_json(f"/projects/{project_id}/issues/{iid}")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"GitLab error: {e}") from e
    assignees = issue.get("assignees") or []
    if not assignees:
        raise HTTPException(400, "У задачи нет исполнителя")
    s = _resolve_session()
    me = (s.get("user") or {}) if s else {}
    payload = {
        "gitlab_username": assignees[0]["username"],
        "issue": {
            "iid": issue["iid"],
            "title": issue["title"],
            "web_url": issue.get("web_url"),
            "project_path": issue.get("references", {}).get("full", "")
            .split("#")[0],
        },
        "poked_by": me.get("name") or me.get("username"),
        "message": body.message,
    }
    headers = {}
    if bot.get("secret"):  # webhook_secret для аутентификации в боте
        headers["Authorization"] = f"Bearer {bot['secret']}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{bot['url']}/api/poke", json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Бот недоступен: {e}") from e
    if r.status_code >= 300:
        raise HTTPException(r.status_code, f"Бот вернул ошибку: {r.text[:200]}")
    return {"ok": True, "to": assignees[0]["username"]}


def _enc(value: str) -> str:
    """full_path надо урл-энкодить для REST; числовой id оставляем как есть."""
    if value.isdigit():
        return value
    from urllib.parse import quote
    return quote(value, safe="")


# ── статика собранного фронта (прод: один образ, один origin) ────────────────
# Если задан STATIC_DIR с собранным фронтом — отдаём его на "/". Монтируем ПОСЛЕ
# всех /api-роутов, чтобы они имели приоритет. Роутинг во фронте — по hash
# (#admin), поэтому одного index.html достаточно.
import os as _os  # noqa: E402

_static_dir = _os.environ.get("STATIC_DIR")
if _static_dir and _os.path.isdir(_static_dir):
    from fastapi.staticfiles import StaticFiles  # noqa: E402

    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
