# -*- coding: utf-8 -*-
"""🛒 컴퓨존 장바구니 담기 (서버 쪽): 계정 저장 · 작업 프로그램 관리 · 진행 상황.

- 계정: ⚙️ 설정에서 아이디 · 비밀번호를 받아 app_settings.json 의 `compuzone_account` 에 둔다. 비밀번호는 Gmail 앱
  비밀번호와 같은 Windows DPAPI 로 암호화해 이 PC · 이 Windows 사용자만 풀 수 있다. 화면에는 비밀번호를 돌려주지 않는다.
- 작업 프로그램: `python -m app.compuzone_cart_runner <프로필>` 을 콘솔 없이 띄우고 stdin 으로 작업을, stdout 으로 진행을
  주고받는다(app/compuzone_cart_runner.py). 창(브라우저)이 열려 있는 동안 같은 프로그램을 다시 쓴다.
- 종료: shutdown_service 가 이 프로그램과 프로필 폴더 이름(AI-PC-Quote-Compuzone)이 명령줄에 든 Edge 만 닫는다.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
import uuid
from typing import Any

from app.app_settings import get_setting, set_setting
from app.data_paths import COMPUZONE_PROFILE_DIR, ROOT
from app.mail_send import _dpapi

ACCOUNT_KEY = "compuzone_account"
JOB_KEEP_SEC = 3600

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_jobs: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# 계정
# ---------------------------------------------------------------------------
def get_account() -> dict[str, Any]:
    cfg = get_setting(ACCOUNT_KEY, {}) or {}
    user = str(cfg.get("user") or "")
    return {"user": user, "has_password": bool(cfg.get("password")), "configured": bool(user and cfg.get("password"))}


def set_account(user: str, password: str) -> dict[str, Any]:
    """비밀번호가 비어 있으면 아이디만 바꾸고 기존 비밀번호를 둔다. 아이디를 비우면 계정을 지운다."""
    user = (user or "").strip()
    if not user:
        set_setting(ACCOUNT_KEY, {})
        return get_account()
    cfg = get_setting(ACCOUNT_KEY, {}) or {}
    enc = cfg.get("password") or ""
    if password:
        enc = base64.b64encode(_dpapi(password.encode("utf-8"), True)).decode()
    if not enc:
        raise ValueError("컴퓨존 비밀번호를 입력해 주세요.")
    set_setting(ACCOUNT_KEY, {"user": user[:80], "password": enc})
    return get_account()


def _password() -> str:
    enc = (get_setting(ACCOUNT_KEY, {}) or {}).get("password") or ""
    return _dpapi(base64.b64decode(enc), encrypt=False).decode("utf-8") if enc else ""


# ---------------------------------------------------------------------------
# 작업 프로그램
# ---------------------------------------------------------------------------
def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _reader(proc: subprocess.Popen) -> None:
    for raw in proc.stdout:  # type: ignore[union-attr]
        try:
            msg = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        job = _jobs.get(str(msg.get("job") or ""))
        if not job:
            continue
        typ = msg.get("type")
        with _lock:
            if typ == "progress":
                job["step"], job["total"] = msg.get("step", 0), msg.get("total", 1)
                job["log"].append(msg.get("msg", ""))
            elif typ == "log":
                job["log"].append(msg.get("msg", ""))
            elif typ == "need_user":
                job["need_user"] = msg.get("msg", "")
                job["log"].append("⚠️ " + msg.get("msg", ""))
            elif typ == "done":
                job.update(state="done", result={k: msg.get(k) for k in ("added", "failed", "qty_diff", "already", "missing", "in_cart")},
                           need_user="")
                job["step"] = job.get("total", 1)
            elif typ == "error":
                job.update(state="error", error=msg.get("msg", ""), need_user="")
                job["log"].append("✕ " + msg.get("msg", ""))
    # 프로그램이 끝났는데 진행 중인 작업이 있으면 실패로
    with _lock:
        for j in _jobs.values():
            if j["state"] == "running":
                j.update(state="error", error="컴퓨존 창이 닫혀 작업이 끝났습니다.")


def _ensure_proc() -> subprocess.Popen:
    global _proc
    if _proc is not None and _proc.poll() is None:
        return _proc
    COMPUZONE_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-m", "app.compuzone_cart_runner", str(COMPUZONE_PROFILE_DIR)],
        cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    threading.Thread(target=_reader, args=(_proc,), daemon=True).start()
    return _proc


def start(items: list[dict[str, Any]], label: str = "") -> dict[str, Any]:
    """장바구니 담기 작업을 시작한다. items = [{pno, qty, category, name}]"""
    if not playwright_available():
        raise RuntimeError("Playwright 가 설치되어 있지 않습니다. install.bat 을 다시 실행해 주세요.")
    clean = []
    for it in items or []:
        try:
            pno = int(it.get("pno") or 0)
        except (TypeError, ValueError):
            continue
        if pno > 0:
            clean.append({"pno": pno, "qty": max(1, min(99, int(it.get("qty") or 1))),
                          "category": str(it.get("category") or "")[:40], "name": str(it.get("name") or "")[:200]})
    if not clean:
        raise ValueError("담을 컴퓨존 상품이 없습니다.")
    acct = get_setting(ACCOUNT_KEY, {}) or {}
    with _lock:
        now = time.time()
        for k in [k for k, j in _jobs.items() if now - j["created"] > JOB_KEEP_SEC]:
            _jobs.pop(k, None)
        if any(j["state"] == "running" for j in _jobs.values()):
            raise RuntimeError("다른 장바구니 담기가 진행 중입니다. 끝난 뒤 다시 눌러 주세요.")
        job_id = uuid.uuid4().hex[:10]
        _jobs[job_id] = {"id": job_id, "label": label[:120], "state": "running", "step": 0, "total": len(clean) + 2,
                         "log": [], "need_user": "", "error": "", "result": None, "created": now, "items": len(clean)}
    proc = _ensure_proc()
    task = {"job": job_id, "user": acct.get("user") or "", "password": _password(), "items": clean}
    try:
        proc.stdin.write((json.dumps(task, ensure_ascii=False) + "\n").encode("utf-8"))  # type: ignore[union-attr]
        proc.stdin.flush()  # type: ignore[union-attr]
    except OSError as e:
        with _lock:
            _jobs[job_id].update(state="error", error=f"작업 프로그램을 시작하지 못했습니다: {e}")
    return status(job_id)


def status(job_id: str) -> dict[str, Any]:
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            raise KeyError(job_id)
        return {k: (list(v) if isinstance(v, list) else v) for k, v in j.items() if k != "created"}


def stop_runner() -> None:
    """서버 종료 때: 작업 프로그램에 끝내라고 알리고, 안 끝나면 강제로 닫는다(그 Edge 는 shutdown_service 가 닫는다)."""
    global _proc
    p = _proc
    _proc = None
    if p is None or p.poll() is not None:
        return
    try:
        p.stdin.write(b'{"cmd":"quit"}\n')  # type: ignore[union-attr]
        p.stdin.flush()  # type: ignore[union-attr]
        p.wait(timeout=5)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
