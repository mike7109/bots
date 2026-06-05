"""Multi-group support: each "source" maps a GitLab group -> a Matrix room,
with its own API token. Sources live in the state DB (admin-managed), so adding
a group/room/token is a UI action, not a redeploy.

Routing:
  - webhook: the project's path_with_namespace is matched (longest prefix) to a
    source's group full_path -> that source's room.
  - scheduler/digests: iterate enabled sources, poll each group with its token,
    deliver to its room (dedup namespaced per source).

The first source is seeded from env (GITLAB_GROUP_ID/TOKEN + room) so an existing
single-group deploy keeps working unchanged.
"""
from __future__ import annotations

from botkit.gitlab import GitLabClient

_KIND = "source"
_FIELDS = ("name", "group_id", "token", "room", "enabled", "full_path", "group_name")


class Sources:
    def __init__(self, store, gitlab_url: str):
        self.store = store
        self.gitlab_url = gitlab_url

    def all(self) -> list[dict]:
        items = self.store.list_state(_KIND)
        return [{"id": k, **v} for k, v in sorted(items.items())]

    def enabled(self) -> list[dict]:
        return [s for s in self.all() if s.get("enabled")]

    def get(self, sid: str) -> dict | None:
        v = self.store.get_state(_KIND, sid)
        return {"id": sid, **v} if v else None

    def upsert(self, src: dict) -> dict:
        sid = src["id"]
        cur = self.store.get_state(_KIND, sid) or {}
        for f in _FIELDS:
            if f in src and src[f] is not None:
                cur[f] = src[f]
        cur.setdefault("enabled", True)
        self.store.set_state(_KIND, sid, cur)
        return self.get(sid)

    def delete(self, sid: str) -> None:
        self.store.clear_state(_KIND, sid)

    def match_path(self, path_with_namespace: str) -> dict | None:
        """Route a webhook: longest `full_path` prefix wins (handles subgroups)."""
        path = (path_with_namespace or "").strip("/")
        best = None
        for s in self.enabled():
            fp = (s.get("full_path") or "").strip("/")
            if fp and (path == fp or path.startswith(fp + "/")):
                if best is None or len(fp) > len((best.get("full_path") or "").strip("/")):
                    best = s
        return best

    def validate(self, group_id: str, token: str) -> dict:
        """Test a token against a group: proves access, returns name/path + open issues."""
        gl = GitLabClient(self.gitlab_url, token)
        try:
            g = gl.get_json(f"/api/v4/groups/{group_id}")
        except Exception as e:                       # noqa: BLE001
            return {"ok": False, "error": f"нет доступа: {e}"}
        try:
            n = len(gl.group_issues(group_id, state="opened", scope="all"))
        except Exception:                            # noqa: BLE001
            n = None
        return {"ok": True, "group_name": g.get("full_name") or g.get("name"),
                "full_path": g.get("full_path"), "issues": n}


def seed_from_env(sources: Sources, *, group_id, token, room) -> None:
    """First run: turn the existing env single-group config into source 'default'
    so nothing breaks. Best-effort validate to fill full_path for routing."""
    if sources.all() or not group_id:
        return
    src = {"id": "default", "name": "По умолчанию (из env)", "group_id": group_id,
           "token": token or "", "room": room or "", "enabled": True}
    if token:
        v = sources.validate(group_id, token)
        if v.get("ok"):
            src["full_path"] = v.get("full_path")
            src["group_name"] = v.get("group_name")
    sources.upsert(src)
