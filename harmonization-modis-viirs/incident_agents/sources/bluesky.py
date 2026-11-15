"""Bluesky keyword search (app.bsky.feed.searchPosts) with an app password.

Login: com.atproto.server.createSession with the handle and an app password
(format xxxx-xxxx-xxxx-xxxx). The session token is reused for an hour.
Posts carry a self-reported createdAt; we also keep our fetch time.
"""

from __future__ import annotations

import time

import requests

from .. import config
from ..store import now_iso
from . import Record, SourceError, utc_iso

PDS = "https://bsky.social"


class Bluesky:
    def __init__(self, handle: str | None = None, app_password: str | None = None,
                 session: requests.Session | None = None):
        self.handle = handle or config.secret("bsky_handle")
        self.password = app_password or config.secret("bsky_app_password")
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = config.USER_AGENT
        self._jwt: str | None = None
        self._jwt_at = 0.0

    @property
    def available(self) -> bool:
        return bool(self.handle and self.password)

    def _token(self) -> str:
        if self._jwt and time.monotonic() - self._jwt_at < 3600:
            return self._jwt
        if not self.available:
            raise SourceError("bluesky: no handle / app password in .env")
        try:
            r = self.s.post(f"{PDS}/xrpc/com.atproto.server.createSession", timeout=30,
                            json={"identifier": self.handle, "password": self.password})
        except requests.RequestException as e:
            raise SourceError(f"bluesky login: {type(e).__name__}") from None
        if r.status_code != 200:
            raise SourceError(f"bluesky login: HTTP {r.status_code}")
        self._jwt, self._jwt_at = r.json()["accessJwt"], time.monotonic()
        return self._jwt

    def search(self, terms: list[str], lang: str, since_iso: str, limit: int = 50) -> list[Record]:
        """One searchPosts call per term (Bluesky search has no OR operator). Deduped by URI."""
        out: dict[str, Record] = {}
        for term in terms:
            q = f'"{term}"' if " " in term else term
            try:
                r = self.s.get(f"{PDS}/xrpc/app.bsky.feed.searchPosts", timeout=30,
                               headers={"Authorization": f"Bearer {self._token()}"},
                               params={"q": q, "lang": lang, "since": since_iso, "sort": "latest",
                                       "limit": min(100, limit)})
            except requests.RequestException as e:
                raise SourceError(f"bluesky search: {type(e).__name__}") from None
            if r.status_code == 401:
                self._jwt = None
                continue
            if r.status_code != 200:
                raise SourceError(f"bluesky search: HTTP {r.status_code}")
            fetched = now_iso()
            for p in r.json().get("posts", []):
                rec = p.get("record") or {}
                handle = (p.get("author") or {}).get("handle", "")
                rkey = p.get("uri", "").rsplit("/", 1)[-1]
                url = f"https://bsky.app/profile/{handle}/post/{rkey}"
                if url in out:
                    continue
                out[url] = Record(
                    source="bluesky", kind="post", record_id=p.get("uri", url), url=url,
                    text=rec.get("text") or "", published_at=utc_iso(rec.get("createdAt")),
                    fetched_at=fetched, author=handle, lang=(rec.get("langs") or [lang])[0],
                    meta={"term": term, "indexedAt": utc_iso(p.get("indexedAt"))})
        return list(out.values())
