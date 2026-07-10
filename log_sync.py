"""GitHub Contents API로 원격 로그 업로드·조회."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app_logger import LOG_FILE, log
from app_paths import data_file

MAX_UPLOAD_BYTES = 512 * 1024
DEFAULT_OWNER = "lee3215-ko"
DEFAULT_REPO = "backlink-writer-logs"
USER_AGENT = "BacklinkWriter-LogSync/1.0"


@dataclass
class LogSyncConfig:
    enabled: bool = False
    owner: str = DEFAULT_OWNER
    repo: str = DEFAULT_REPO
    token: str = ""
    interval_min: float = 30.0

    @classmethod
    def from_dict(cls, raw: dict | None) -> LogSyncConfig:
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            owner=(raw.get("owner") or DEFAULT_OWNER).strip() or DEFAULT_OWNER,
            repo=(raw.get("repo") or DEFAULT_REPO).strip() or DEFAULT_REPO,
            token=(raw.get("token") or "").strip(),
            interval_min=float(raw.get("interval_min", 30) or 30),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "owner": self.owner,
            "repo": self.repo,
            "token": self.token,
            "interval_min": self.interval_min,
        }

    def is_ready(self) -> bool:
        return bool(self.enabled and self.owner and self.repo and self.token)


@dataclass
class ClientLogEntry:
    pc_id: str
    path: str
    size: int = 0
    sha: str = ""
    updated_at: str = ""


def get_pc_id() -> str:
    name = (platform.node() or socket.gethostname() or "pc").strip() or "pc"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
    digest = hashlib.sha1(f"{name}|{platform.system()}|{os.environ.get('USERNAME', '')}".encode()).hexdigest()[:8]
    return f"{safe}-{digest}"


def read_log_tail(*, max_bytes: int = MAX_UPLOAD_BYTES) -> str:
    path = LOG_FILE
    if not path.exists():
        return f"(로그 파일 없음: {path})\n"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"(로그 읽기 실패: {exc})\n"
    if len(data) > max_bytes:
        data = data[-max_bytes:]
        # UTF-8 경계 맞춤
        while data and (data[0] & 0xC0) == 0x80:
            data = data[1:]
        text = data.decode("utf-8", errors="replace")
        return f"... (앞부분 생략, 마지막 {max_bytes // 1024}KB) ...\n{text}"
    return data.decode("utf-8", errors="replace")


def build_upload_payload(extra_note: str = "") -> str:
    header = [
        f"# BacklinkWriter remote log",
        f"pc_id: {get_pc_id()}",
        f"host: {platform.node()}",
        f"user: {os.environ.get('USERNAME', '')}",
        f"os: {platform.platform()}",
        f"uploaded_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if extra_note:
        header.append(f"note: {extra_note}")
    header.append("")
    body = read_log_tail()
    text = "\n".join(header) + body
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_UPLOAD_BYTES:
        # 헤더는 유지하고 본문만 자름
        head = ("\n".join(header)).encode("utf-8")
        remain = MAX_UPLOAD_BYTES - len(head) - 64
        if remain < 1024:
            remain = MAX_UPLOAD_BYTES // 2
        body_bytes = body.encode("utf-8")[-remain:]
        while body_bytes and (body_bytes[0] & 0xC0) == 0x80:
            body_bytes = body_bytes[1:]
        text = head.decode("utf-8") + body_bytes.decode("utf-8", errors="replace")
    return text


def _api_request(
    method: str,
    url: str,
    *,
    token: str,
    payload: dict | None = None,
) -> tuple[int, Any]:
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(detail) if detail.strip() else {"message": str(exc)}
        except Exception:
            body = {"message": detail[:300] or str(exc)}
        return exc.code, body
    except Exception as exc:
        return 0, {"message": str(exc)}


def _contents_url(owner: str, repo: str, path: str) -> str:
    quoted = "/".join(urllib.parse.quote(p) for p in path.strip("/").split("/"))
    return f"https://api.github.com/repos/{owner}/{repo}/contents/{quoted}"


def _client_log_path(pc_id: str | None = None) -> str:
    return f"clients/{pc_id or get_pc_id()}/latest.log"


def get_file_sha(cfg: LogSyncConfig, path: str) -> str | None:
    code, body = _api_request("GET", _contents_url(cfg.owner, cfg.repo, path), token=cfg.token)
    if code == 200 and isinstance(body, dict):
        return body.get("sha")
    return None


def upload_latest_log(cfg: LogSyncConfig, *, note: str = "") -> tuple[bool, str]:
    """PC별 latest.log 덮어쓰기. 실패해도 예외 없이 (ok, message) 반환."""
    if not cfg.is_ready():
        return False, "로그 동기화 미설정 (토큰·저장소·켜짐 확인)"

    path = _client_log_path()
    content = build_upload_payload(note)
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    payload: dict[str, Any] = {
        "message": f"log sync {get_pc_id()} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "content": b64,
        "branch": "main",
    }
    sha = get_file_sha(cfg, path)
    if sha:
        payload["sha"] = sha

    code, body = _api_request("PUT", _contents_url(cfg.owner, cfg.repo, path), token=cfg.token, payload=payload)
    if code in (200, 201):
        msg = f"원격 로그 업로드 완료 ({path}, {len(content)}자)"
        log.info(msg)
        _save_last_sync(True, msg)
        return True, msg

    # 재시도 1회 (sha 충돌 등)
    if code in (409, 422):
        sha2 = get_file_sha(cfg, path)
        if sha2:
            payload["sha"] = sha2
        code, body = _api_request("PUT", _contents_url(cfg.owner, cfg.repo, path), token=cfg.token, payload=payload)
        if code in (200, 201):
            msg = f"원격 로그 업로드 완료(재시도) ({path})"
            log.info(msg)
            _save_last_sync(True, msg)
            return True, msg

    err = body.get("message", str(body)) if isinstance(body, dict) else str(body)
    msg = f"원격 로그 업로드 실패 HTTP {code}: {err}"
    log.info(msg)
    _save_last_sync(False, msg)
    return False, msg


def list_client_logs(cfg: LogSyncConfig) -> tuple[list[ClientLogEntry], str]:
    if not cfg.token or not cfg.owner or not cfg.repo:
        return [], "토큰·저장소 설정 필요"
    code, body = _api_request("GET", _contents_url(cfg.owner, cfg.repo, "clients"), token=cfg.token)
    if code == 404:
        return [], "clients/ 폴더 없음 — 아직 업로드된 PC가 없습니다"
    if code != 200:
        err = body.get("message", str(body)) if isinstance(body, dict) else str(body)
        return [], f"목록 조회 실패 HTTP {code}: {err}"
    if not isinstance(body, list):
        return [], "목록 형식 오류"

    entries: list[ClientLogEntry] = []
    for item in body:
        if not isinstance(item, dict) or item.get("type") != "dir":
            continue
        pc_id = item.get("name") or ""
        if not pc_id:
            continue
        log_path = f"clients/{pc_id}/latest.log"
        fcode, fbody = _api_request("GET", _contents_url(cfg.owner, cfg.repo, log_path), token=cfg.token)
        size = 0
        sha = ""
        updated = ""
        if fcode == 200 and isinstance(fbody, dict):
            size = int(fbody.get("size") or 0)
            sha = fbody.get("sha") or ""
            # Contents API에는 mtime이 없어 commit API 생략 — size만
            updated = f"{size} bytes"
        entries.append(ClientLogEntry(pc_id=pc_id, path=log_path, size=size, sha=sha, updated_at=updated))
    entries.sort(key=lambda e: e.pc_id.lower())
    return entries, ""


def fetch_client_log(cfg: LogSyncConfig, pc_id: str) -> tuple[str, str]:
    if not cfg.token:
        return "", "토큰 필요"
    path = _client_log_path(pc_id)
    code, body = _api_request("GET", _contents_url(cfg.owner, cfg.repo, path), token=cfg.token)
    if code != 200 or not isinstance(body, dict):
        err = body.get("message", str(body)) if isinstance(body, dict) else str(body)
        return "", f"조회 실패 HTTP {code}: {err}"
    content_b64 = body.get("content") or ""
    encoding = body.get("encoding") or "base64"
    if encoding != "base64":
        return "", f"지원하지 않는 encoding: {encoding}"
    try:
        raw = base64.b64decode(content_b64.replace("\n", ""))
        return raw.decode("utf-8", errors="replace"), ""
    except Exception as exc:
        return "", f"디코드 실패: {exc}"


def _save_last_sync(ok: bool, message: str) -> None:
    path = data_file("log_sync_status.json")
    try:
        path.write_text(
            json.dumps(
                {
                    "ok": ok,
                    "message": message,
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "pc_id": get_pc_id(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def load_last_sync() -> dict:
    path = data_file("log_sync_status.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
