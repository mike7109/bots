"""Thin GitLab REST + GraphQL client.

Just enough for the bots here: paginated REST reads and raw GraphQL. The
GraphQL schema shifts between GitLab versions, so callers own their queries
(see services/gitlab-expand-tasks for a real-world example).
"""
from __future__ import annotations

import requests


class GitLabClient:
    def __init__(self, url: str, token: str, *, timeout: float = 15.0):
        self.base = url.rstrip("/")
        self.timeout = timeout
        self._s = requests.Session()
        self._s.headers["Authorization"] = f"Bearer {token}"

    # --- REST -------------------------------------------------------------
    def _get(self, path: str, **params):
        resp = self._s.get(f"{self.base}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    def get_paginated(self, path: str, **params) -> list:
        """Follow keyset/offset pagination and return all items."""
        params.setdefault("per_page", 100)
        page, out = 1, []
        while True:
            resp = self._get(path, page=page, **params)
            batch = resp.json()
            out.extend(batch)
            if len(batch) < params["per_page"]:
                return out
            page += 1

    def group_issues(self, group_id: str | int, **params) -> list:
        """List issues in a group (incl. subgroups). Params pass through to the API."""
        return self.get_paginated(f"/api/v4/groups/{group_id}/issues", **params)

    def get_json(self, path: str, **params):
        """Single GET returning parsed JSON (e.g. group info for access checks)."""
        return self._get(path, **params).json()

    # --- GraphQL ----------------------------------------------------------
    def graphql(self, query: str, variables: dict | None = None) -> dict:
        resp = self._s.post(
            f"{self.base}/api/graphql",
            json={"query": query, "variables": variables or {}},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            raise RuntimeError(data["errors"])
        return data["data"]
