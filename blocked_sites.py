"""원격 차단 사이트 — 자동화 불가 URL을 이용자 PC에서 제외·삭제."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app_constants import APP_NAME, APP_VERSION, UPDATE_VERSION_URL
from app_logger import log
from app_paths import data_file, get_install_dir
from batch_jobs import normalize_board_url
from updater import fetch_version_payload

BLOCKED_SITES_RAW_URL = (
    "https://raw.githubusercontent.com/lee3215-ko/backlink-auto-writer/main/blocked_sites.json"
)
APPLIED_FILE = "blocked_sites_applied.json"


@dataclass
class BlockedSite:
    host: str
    urls: list[str]
    reason: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> BlockedSite | None:
        host = (raw.get("host") or "").strip().lower()
        if host.startswith("www."):
            host = host[4:]
        urls = [str(u).strip() for u in (raw.get("urls") or []) if str(u).strip()]
        if not host and not urls:
            return None
        if not host and urls:
            host = host_of(urls[0])
        return cls(host=host, urls=urls, reason=str(raw.get("reason") or "").strip())


def host_of(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return ""
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def load_local_blocked_sites() -> list[BlockedSite]:
    candidates = [
        get_install_dir() / "blocked_sites.json",
        Path(__file__).resolve().parent / "blocked_sites.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            return parse_blocked_payload(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return []


def parse_blocked_payload(raw: Any) -> list[BlockedSite]:
    if isinstance(raw, dict):
        items = raw.get("sites") or raw.get("blocked_sites") or []
        # version.json 전체인 경우 blocked_sites 키만
        if not items and "version" in raw and "blocked_sites" in raw:
            items = raw.get("blocked_sites") or []
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[BlockedSite] = []
    for item in items:
        if isinstance(item, str):
            u = item.strip()
            if u:
                out.append(BlockedSite(host=host_of(u), urls=[u], reason=""))
            continue
        if isinstance(item, dict):
            site = BlockedSite.from_dict(item)
            if site:
                out.append(site)
    return out


def _fetch_json_url(url: str, user_agent: str) -> Any | None:
    try:
        parsed = urllib.parse.urlparse(url.strip())
        query = urllib.parse.parse_qs(parsed.query)
        query["_"] = [str(int(time.time()))]
        busted = parsed._replace(query=urllib.parse.urlencode(query, doseq=True)).geturl()
        req = urllib.request.Request(
            busted,
            headers={"User-Agent": user_agent, "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8-sig"))
    except Exception as exc:
        log.info("차단 목록 fetch 실패 %s: %s", url[:60], exc)
        return None


def fetch_remote_blocked_sites() -> list[BlockedSite]:
    """version.json → blocked_sites.json → 로컬 번들 순."""
    ua = f"{APP_NAME}/{APP_VERSION}"
    try:
        payload = fetch_version_payload(UPDATE_VERSION_URL, ua)
        if isinstance(payload, dict) and payload.get("blocked_sites"):
            sites = parse_blocked_payload(payload["blocked_sites"])
            if sites:
                return sites
    except Exception as exc:
        log.info("차단 목록 version.json 조회 실패: %s", exc)

    raw = _fetch_json_url(BLOCKED_SITES_RAW_URL, ua)
    if raw is not None:
        sites = parse_blocked_payload(raw)
        if sites:
            return sites

    return load_local_blocked_sites()


def _load_applied() -> dict:
    path = data_file(APPLIED_FILE)
    if not path.exists():
        return {"hosts": [], "urls": [], "at": ""}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"hosts": [], "urls": [], "at": ""}


def _save_applied(hosts: set[str], urls: set[str]) -> None:
    data_file(APPLIED_FILE).write_text(
        json.dumps(
            {
                "hosts": sorted(hosts),
                "urls": sorted(urls),
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def apply_blocked_sites(excluded, sites: list[BlockedSite]) -> tuple[int, list[str]]:
    """
    ExcludedUrlRegistry에 차단 사이트 반영.
    returns: (이번 실행에서 새로 추가된 항목 수, 사유 요약)
    """
    if not sites:
        return 0, []

    applied = _load_applied()
    known_hosts = set(applied.get("hosts") or [])
    known_urls = set(applied.get("urls") or [])
    added = 0
    reasons: list[str] = []

    for site in sites:
        host = (site.host or "").lower()
        if host.startswith("www."):
            host = host[4:]
        urls = list(site.urls)
        if host and f"https://{host}/" not in urls:
            urls.append(f"https://{host}/")

        is_new = False
        if host and host not in known_hosts:
            known_hosts.add(host)
            is_new = True
            added += 1
        for u in urls:
            if u and u not in known_urls:
                known_urls.add(u)
                is_new = True
                added += 1

        if hasattr(excluded, "add_host"):
            excluded.add_host(host, urls)
        else:
            key = host or (normalize_board_url(urls[0]) if urls else "")
            excluded.add(key, urls)

        if is_new and site.reason:
            reasons.append(f"{host}: {site.reason}")

    _save_applied(known_hosts, known_urls)
    return added, reasons[:12]


def url_matches_blocked(url: str, sites: list[BlockedSite] | None = None) -> bool:
    sites = sites if sites is not None else load_local_blocked_sites()
    url = (url or "").strip()
    if not url:
        return False
    host = host_of(url)
    key = normalize_board_url(url)
    for site in sites:
        if site.host and host == site.host:
            return True
        if url in site.urls:
            return True
        for u in site.urls:
            if key and key == normalize_board_url(u):
                return True
    return False
