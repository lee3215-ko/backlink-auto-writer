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
    upload_failures_auto: bool = True

    @classmethod
    def from_dict(cls, raw: dict | None) -> LogSyncConfig:
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            owner=(raw.get("owner") or DEFAULT_OWNER).strip() or DEFAULT_OWNER,
            repo=(raw.get("repo") or DEFAULT_REPO).strip() or DEFAULT_REPO,
            token=(raw.get("token") or "").strip(),
            interval_min=float(raw.get("interval_min", 30) or 30),
            upload_failures_auto=bool(raw.get("upload_failures_auto", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "owner": self.owner,
            "repo": self.repo,
            "token": self.token,
            "interval_min": self.interval_min,
            "upload_failures_auto": self.upload_failures_auto,
        }

    def is_ready(self) -> bool:
        return bool(self.enabled and self.owner and self.repo and self.token)

    def can_upload(self) -> bool:
        """자동 켜짐 여부와 무관 — 토큰·저장소만 있으면 수동 업로드 가능."""
        return bool(self.owner and self.repo and self.token)


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
    }
    # 빈 저장소는 branch 지정 시 404 — 기본 브랜치 생성에 맡김
    # 파일이 이미 있으면 sha 필요
    sha = get_file_sha(cfg, path)
    if sha:
        payload["sha"] = sha
        payload["branch"] = "main"

    code, body = _api_request("PUT", _contents_url(cfg.owner, cfg.repo, path), token=cfg.token, payload=payload)
    if code in (200, 201):
        msg = f"원격 로그 업로드 완료 ({path}, {len(content)}자)"
        log.info(msg)
        _save_last_sync(True, msg)
        return True, msg

    # 재시도 1회 (sha 충돌·빈 저장소 branch 이슈)
    if code in (404, 409, 422):
        payload2 = dict(payload)
        payload2.pop("branch", None)
        sha2 = get_file_sha(cfg, path)
        if sha2:
            payload2["sha"] = sha2
        elif "sha" in payload2:
            del payload2["sha"]
        code, body = _api_request(
            "PUT", _contents_url(cfg.owner, cfg.repo, path), token=cfg.token, payload=payload2
        )
        if code in (200, 201):
            msg = f"원격 로그 업로드 완료(재시도) ({path})"
            log.info(msg)
            _save_last_sync(True, msg)
            return True, msg

    err = body.get("message", str(body)) if isinstance(body, dict) else str(body)
    if code == 404:
        hint = (
            " — 저장소가 비어 있거나(main 없음)·이름/토큰 권한(contents:write)을 확인하세요. "
            f"대상: {cfg.owner}/{cfg.repo}"
        )
        msg = f"원격 로그 업로드 실패 HTTP {code}: {err}{hint}"
    else:
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


# ── 실패 케이스 (기능 보강용) ──────────────────────────────────────────

MAX_FAILURE_JSON_BYTES = 450 * 1024
INDEX_MAX_ENTRIES = 80


@dataclass
class FailureCaseEntry:
    pc_id: str
    case_id: str
    path: str
    url: str = ""
    reason: str = ""
    action: str = ""
    uploaded_at: str = ""
    form_in_dom: bool = False
    size: int = 0


def make_case_id(url: str = "") -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha1(f"{url}|{ts}|{get_pc_id()}".encode()).hexdigest()[:8]
    return f"{ts}-{digest}"


def _failure_case_path(pc_id: str, case_id: str) -> str:
    return f"failures/{pc_id}/{case_id}.json"


def _failure_index_path(pc_id: str) -> str:
    return f"failures/{pc_id}/index.json"


def _put_text_file(cfg: LogSyncConfig, path: str, text: str, message: str) -> tuple[int, Any]:
    """Contents API PUT — 빈 저장소·sha 충돌 재시도 포함."""
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    payload: dict[str, Any] = {"message": message, "content": b64}
    sha = get_file_sha(cfg, path)
    if sha:
        payload["sha"] = sha
        payload["branch"] = "main"
    code, body = _api_request("PUT", _contents_url(cfg.owner, cfg.repo, path), token=cfg.token, payload=payload)
    if code in (200, 201):
        return code, body
    if code in (404, 409, 422):
        payload2 = dict(payload)
        payload2.pop("branch", None)
        sha2 = get_file_sha(cfg, path)
        if sha2:
            payload2["sha"] = sha2
        elif "sha" in payload2:
            del payload2["sha"]
        return _api_request("PUT", _contents_url(cfg.owner, cfg.repo, path), token=cfg.token, payload=payload2)
    return code, body


def _get_json_file(cfg: LogSyncConfig, path: str) -> tuple[Any | None, str]:
    code, body = _api_request("GET", _contents_url(cfg.owner, cfg.repo, path), token=cfg.token)
    if code == 404:
        return None, ""
    if code != 200 or not isinstance(body, dict):
        err = body.get("message", str(body)) if isinstance(body, dict) else str(body)
        return None, f"HTTP {code}: {err}"
    try:
        raw = base64.b64decode((body.get("content") or "").replace("\n", ""))
        return json.loads(raw.decode("utf-8", errors="replace")), ""
    except Exception as exc:
        return None, f"디코드 실패: {exc}"


def build_failure_case(
    *,
    url: str,
    raw_error: str,
    localized_reason: str,
    action: str = "",
    kind: str = "",
    app_version: str = "",
    writer_urls: dict | None = None,
    snapshot: dict | None = None,
    dom_markers: dict | None = None,
    html_excerpt: str = "",
    note: str = "",
) -> dict[str, Any]:
    case_id = make_case_id(url)
    markers = dom_markers or {}
    form_in_dom = bool(
        markers.get("#commentform")
        or markers.get("#comment-form")
        or markers.get("textarea[name=comment]")
        or markers.get("#comment")
        or (snapshot or {}).get("comment_form_found")
    )
    case: dict[str, Any] = {
        "case_id": case_id,
        "pc_id": get_pc_id(),
        "host": platform.node(),
        "app_version": app_version,
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "url": url,
        "action": action,
        "kind": kind,
        "raw_error": (raw_error or "")[:2000],
        "localized_reason": localized_reason,
        "form_in_dom": form_in_dom,
        "writer_urls": writer_urls or {},
        "dom_markers": markers,
        "snapshot": snapshot or {},
        "note": note,
    }
    if html_excerpt:
        # 용량 제한
        max_html = 35000
        case["html_excerpt"] = html_excerpt[:max_html]
    encoded = json.dumps(case, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_FAILURE_JSON_BYTES:
        case.pop("html_excerpt", None)
        snap = case.get("snapshot") or {}
        if isinstance(snap, dict) and "body_excerpt" in snap:
            snap["body_excerpt"] = str(snap.get("body_excerpt", ""))[:200]
    return case


def _case_summary(case: dict) -> dict[str, Any]:
    return {
        "case_id": case.get("case_id", ""),
        "url": case.get("url", ""),
        "reason": case.get("localized_reason", ""),
        "action": case.get("action", ""),
        "uploaded_at": case.get("uploaded_at", ""),
        "form_in_dom": bool(case.get("form_in_dom")),
        "app_version": case.get("app_version", ""),
    }


def upload_failure_case(cfg: LogSyncConfig, case: dict) -> tuple[bool, str]:
    if not cfg.can_upload():
        return False, "토큰·저장소 설정 필요"
    pc_id = case.get("pc_id") or get_pc_id()
    case_id = case.get("case_id") or make_case_id(case.get("url", ""))
    case["case_id"] = case_id
    case["pc_id"] = pc_id
    path = _failure_case_path(pc_id, case_id)
    text = json.dumps(case, ensure_ascii=False, indent=2)
    code, body = _put_text_file(
        cfg,
        path,
        text,
        f"failure {pc_id} {case_id}",
    )
    if code not in (200, 201):
        err = body.get("message", str(body)) if isinstance(body, dict) else str(body)
        msg = f"실패 케이스 업로드 실패 HTTP {code}: {err}"
        log.info(msg)
        return False, msg

    # index.json 갱신
    idx_path = _failure_index_path(pc_id)
    index, _ = _get_json_file(cfg, idx_path)
    if not isinstance(index, list):
        index = []
    summary = _case_summary(case)
    index = [x for x in index if isinstance(x, dict) and x.get("case_id") != case_id]
    index.insert(0, summary)
    index = index[:INDEX_MAX_ENTRIES]
    _put_text_file(
        cfg,
        idx_path,
        json.dumps(index, ensure_ascii=False, indent=2),
        f"failure index {pc_id}",
    )
    msg = f"실패 케이스 업로드 완료 ({path})"
    log.info(msg)
    return True, msg


def upload_failure_cases(cfg: LogSyncConfig, cases: list[dict]) -> tuple[int, int, str]:
    ok_n = 0
    fail_n = 0
    last = ""
    for case in cases:
        ok, msg = upload_failure_case(cfg, case)
        last = msg
        if ok:
            ok_n += 1
        else:
            fail_n += 1
    return ok_n, fail_n, last


def list_failure_cases(cfg: LogSyncConfig, *, limit: int = 120) -> tuple[list[FailureCaseEntry], str]:
    if not cfg.can_upload():
        return [], "토큰·저장소 설정 필요"
    code, body = _api_request("GET", _contents_url(cfg.owner, cfg.repo, "failures"), token=cfg.token)
    if code == 404:
        return [], "failures/ 없음 — 아직 업로드된 실패 케이스가 없습니다"
    if code != 200:
        err = body.get("message", str(body)) if isinstance(body, dict) else str(body)
        return [], f"목록 조회 실패 HTTP {code}: {err}"
    if not isinstance(body, list):
        return [], "목록 형식 오류"

    entries: list[FailureCaseEntry] = []
    for item in body:
        if not isinstance(item, dict) or item.get("type") != "dir":
            continue
        pc_id = item.get("name") or ""
        if not pc_id:
            continue
        index, err = _get_json_file(cfg, _failure_index_path(pc_id))
        if isinstance(index, list):
            for row in index:
                if not isinstance(row, dict):
                    continue
                case_id = row.get("case_id") or ""
                if not case_id:
                    continue
                entries.append(
                    FailureCaseEntry(
                        pc_id=pc_id,
                        case_id=case_id,
                        path=_failure_case_path(pc_id, case_id),
                        url=row.get("url") or "",
                        reason=row.get("reason") or "",
                        action=row.get("action") or "",
                        uploaded_at=row.get("uploaded_at") or "",
                        form_in_dom=bool(row.get("form_in_dom")),
                    )
                )
            continue
        # index 없으면 디렉터리 나열
        dcode, dbody = _api_request(
            "GET", _contents_url(cfg.owner, cfg.repo, f"failures/{pc_id}"), token=cfg.token
        )
        if dcode != 200 or not isinstance(dbody, list):
            continue
        for f in dbody:
            if not isinstance(f, dict) or f.get("type") != "file":
                continue
            name = f.get("name") or ""
            if not name.endswith(".json") or name == "index.json":
                continue
            case_id = name[:-5]
            entries.append(
                FailureCaseEntry(
                    pc_id=pc_id,
                    case_id=case_id,
                    path=_failure_case_path(pc_id, case_id),
                    size=int(f.get("size") or 0),
                )
            )

    entries.sort(key=lambda e: e.uploaded_at or e.case_id, reverse=True)
    return entries[:limit], ""


def fetch_failure_case(cfg: LogSyncConfig, pc_id: str, case_id: str) -> tuple[dict | None, str]:
    data, err = _get_json_file(cfg, _failure_case_path(pc_id, case_id))
    if err:
        return None, err
    if data is None:
        return None, "파일을 찾을 수 없습니다"
    if not isinstance(data, dict):
        return None, "JSON 형식 오류"
    return data, ""


def failure_case_to_cursor_report(case: dict) -> str:
    """Cursor에 붙여넣어 기능 보강 요청용 마크다운."""
    lines = [
        "# 백링크 자동화 — 원격 실패 케이스 (기능 보강 요청)",
        "",
        f"- case_id: `{case.get('case_id', '')}`",
        f"- pc_id: `{case.get('pc_id', '')}`",
        f"- app: `{case.get('app_version', '')}`",
        f"- uploaded_at: {case.get('uploaded_at', '')}",
        f"- action/kind: `{case.get('action', '')}` / `{case.get('kind', '')}`",
        f"- URL: {case.get('url', '')}",
        f"- 한글 사유: {case.get('localized_reason', '')}",
        f"- raw_error: {case.get('raw_error', '')}",
        f"- form_in_dom(추정): {'예' if case.get('form_in_dom') else '아니오'}",
        "",
        "## DOM 마커",
        "```json",
        json.dumps(case.get("dom_markers") or {}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 스냅샷",
        "```json",
        json.dumps(case.get("snapshot") or {}, ensure_ascii=False, indent=2)[:8000],
        "```",
        "",
        "## 요청",
        "위 URL은 화면에 댓글/입력 폼이 있는데 프로그램이 못 찾은 경우가 많습니다.",
        "셀렉터·대기·스크롤·visibility 로직을 보강해 자동 등록되게 수정해 주세요.",
    ]
    html = case.get("html_excerpt") or ""
    if html:
        lines.extend(["", "## HTML 발췌", "```html", html[:12000], "```"])
    return "\n".join(lines)
