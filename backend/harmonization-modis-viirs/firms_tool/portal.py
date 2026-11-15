"""Authenticated access to the FIRMS archive-download portal (create.php / list.php)."""

from __future__ import annotations

import json
import netrc
import os
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://firms.modaps.eosdis.nasa.gov"
CREATE_URL = f"{BASE}/download/create.php"
LIST_URL = f"{BASE}/download/list.php"
WHOAMI_URL = f"{BASE}/api/whoami"
SESSION_COOKIE = "auth-firms-session"
USER_AGENT = "firms-tool/0.1 (+archive download helper)"
AUTH_FAILURE = "Authorization failure"


class AuthError(RuntimeError):
    pass


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def load_session(path: Path) -> requests.Session:
    """Session from FIRMS_COOKIE (raw cookie header) or a saved session file."""
    s = new_session()
    raw = os.environ.get("FIRMS_COOKIE")
    if raw:
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                s.cookies.set(k, v, domain=urlparse(BASE).hostname)
        return s
    if path.exists():
        for c in json.loads(path.read_text()):
            s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    return s


def save_session(s: requests.Session, path: Path) -> None:
    data = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in s.cookies]
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def whoami(s: requests.Session) -> dict[str, Any]:
    r = s.get(WHOAMI_URL, timeout=30)
    try:
        return r.json() if r.ok else {}
    except ValueError:
        return {}


def require_login(s: requests.Session) -> dict[str, Any]:
    me = whoami(s)
    if not me:
        raise AuthError("not logged in to FIRMS; run `firms login` or set FIRMS_COOKIE")
    return me


def login_email(s: requests.Session, email: str, ask_code: Callable[[str], str]) -> None:
    """FIRMS email one-time-code login, as implemented by /download/ page JS."""
    r = s.get(f"{BASE}/api/auth/code/{email}", timeout=30)
    data = r.json()
    if data.get("status") != "ok" or not data.get("hash"):
        raise AuthError(f"FIRMS refused to send a code: {data.get('status')!r}")
    code = ask_code(email).strip()
    if not re.fullmatch(r"\d{6}", code):
        raise AuthError("code must be 6 digits")
    s.get(f"{BASE}/api/auth/login/download/email/{data['hash']}/{code}", timeout=30)
    require_login(s)


def login_earthdata(s: requests.Session, username: str | None, password: str | None) -> None:
    """Earthdata (URS) OAuth with HTTP basic auth on the authorize step.

    Works only after the user has approved the FIRMS app in Earthdata once via a
    browser; otherwise URS shows an approval page and this raises AuthError.
    """
    if not (username and password):
        try:
            auth = netrc.netrc().authenticators("urs.earthdata.nasa.gov")
        except (FileNotFoundError, netrc.NetrcParseError):
            auth = None
        if not auth:
            raise AuthError("set EARTHDATA_USERNAME/EARTHDATA_PASSWORD or add urs.earthdata.nasa.gov to ~/.netrc")
        username, password = auth[0], auth[2]
    r = s.get(f"{BASE}/oauth/login?redirect=/api/auth/login/download/ed/", allow_redirects=False, timeout=30)
    loc = r.headers.get("location", "")
    if "urs.earthdata.nasa.gov" not in loc:
        raise AuthError(f"unexpected OAuth redirect: {loc!r}")
    s.get(loc, auth=(username, password), timeout=60)
    require_login(s)


def _check_page(r: requests.Response) -> None:
    if AUTH_FAILURE in r.text:
        raise AuthError(f"{r.url}: FIRMS returned 'Authorization failure' (session missing or expired)")
    r.raise_for_status()


def inspect_form(s: requests.Session) -> dict[str, Any]:
    """Dump every form control and every backend URL referenced by create.php."""
    r = s.get(CREATE_URL, timeout=60)
    _check_page(r)
    soup = BeautifulSoup(r.text, "html.parser")
    forms = []
    for form in soup.find_all("form") or [soup]:
        controls = []
        for el in form.find_all(["input", "select", "textarea", "button"]):
            c = {k: el.get(k) for k in ("name", "id", "type", "value", "required", "onclick") if el.get(k) is not None}
            if el.name == "select":
                c["options"] = [{"value": o.get("value"), "label": o.get_text(strip=True)} for o in el.find_all("option")]
            c["tag"] = el.name
            controls.append(c)
        forms.append({"action": form.get("action"), "method": form.get("method"), "controls": controls})

    scripts = [urljoin(CREATE_URL, sc["src"]) for sc in soup.find_all("script", src=True)]
    js_text = "\n".join(sc.get_text() for sc in soup.find_all("script", src=False))
    for url in scripts:
        if urlparse(url).hostname == urlparse(BASE).hostname:
            try:
                js_text += "\n" + s.get(url, timeout=60).text
            except requests.RequestException:
                pass
    endpoints = sorted(set(re.findall(r"""["'`](/(?:api|download)/[^"'`\s]{0,120})["'`]""", js_text)))
    captcha = bool(re.search(r"recaptcha|hcaptcha|captcha", r.text + js_text, re.I))
    return {"url": r.url, "forms": forms, "scripts": scripts, "endpoints": endpoints, "captcha_detected": captcha}


def submit(s: requests.Session, form_cfg: dict[str, Any], payload: dict[str, str]) -> dict[str, Any]:
    url = urljoin(BASE, form_cfg["endpoint"])
    headers = {"Referer": CREATE_URL, "Origin": BASE}
    method = form_cfg.get("method", "POST").upper()
    if form_cfg.get("encoding") == "json":
        r = s.request(method, url, json=payload, headers=headers, timeout=120)
    else:
        r = s.request(method, url, data=payload, headers=headers, timeout=120)
    try:
        body: Any = r.json()
    except ValueError:
        body = r.text[:2000]
    if isinstance(body, str) and AUTH_FAILURE in body:
        raise AuthError("submission rejected: session missing or expired")
    return {"status_code": r.status_code, "url": r.url, "body": body}


def list_requests(s: requests.Session) -> list[dict[str, Any]]:
    """Parse list.php into rows of {cells, links}. Column meaning is taken from the header row."""
    r = s.get(LIST_URL, timeout=60)
    _check_page(r)
    soup = BeautifulSoup(r.text, "html.parser")
    rows: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        header: list[str] = []
        for tr in table.find_all("tr"):
            ths = tr.find_all("th")
            if ths and not tr.find_all("td"):
                header = [th.get_text(" ", strip=True) for th in ths]
                continue
            tds = tr.find_all("td")
            if not tds:
                continue
            cells = [td.get_text(" ", strip=True) for td in tds]
            links = [urljoin(LIST_URL, a["href"]) for a in tr.find_all("a", href=True)]
            row: dict[str, Any] = {"cells": cells, "links": links}
            if header and len(header) == len(cells):
                row["columns"] = dict(zip(header, cells))
            rows.append(row)
    return rows


def downloadable_links(rows: list[dict[str, Any]]) -> list[str]:
    out = []
    for row in rows:
        for link in row["links"]:
            if re.search(r"\.zip($|\?)|download", link, re.I) and not link.rstrip("/").endswith(("list.php", "create.php")):
                out.append(link)
    return list(dict.fromkeys(out))


def download(s: requests.Session, url: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with s.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        cd = r.headers.get("content-disposition", "")
        m = re.search(r'filename="?([^";]+)', cd)
        name = m.group(1) if m else Path(urlparse(url).path).name or f"firms_{int(time.time())}.zip"
        dest = out_dir / name
        if dest.exists() and dest.stat().st_size == int(r.headers.get("content-length", -1)):
            return dest
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
        tmp.replace(dest)
    return dest
