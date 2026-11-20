# -*- coding: utf-8 -*-
"""💽 전체 백업 — 업무 데이터 폴더(data/)를 항목별로 골라 ZIP 한 파일로 묶는다(⚙️ 설정 머리줄의 [💽 전체 백업]).

- 저장 위치: 다른 백업(작업내역서 · 거래처 · 견적서 · 거래명세서 · 메모장)과 같은 공용 백업 폴더(task_report.get_backup_dir).
- 파일 이름: 전체백업_YYYYMMDD_HHMMSS.zip. ZIP 안의 경로는 프로그램 폴더 기준(data/…)이라,
  복구할 때는 앱을 끄고 ZIP 을 프로그램 폴더에 풀면(덮어쓰기) 된다. manifest.json · 복구방법.txt 를 함께 넣는다.
- 컴퓨존 장바구니용 Edge 프로필(data/AI-PC-Quote-Compuzone)은 넣지 않는다: 크고(수백 MB) 로그인 정보가 들어 있으며
  다시 로그인하면 새로 생긴다. 수집 캐시(data/cache)는 기본으로 빼고, 고르면 넣는다.
- 진행: 백그라운드 스레드가 파일마다 진행 상황을 갱신하고 화면이 GET 으로 읽는다(한 번에 하나만).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from app.data_paths import DATA_DIR, ROOT

# (키, 화면 이름, data/ 아래 폴더, 기본 선택, 설명)
ITEMS: list[tuple[str, str, str, bool, str]] = [
    ("settings", "🧩 견적요소 · 앱 설정", "settings", True, "견적처 · 공급자 · 추가 금액 · 계좌, 백업 폴더, 메일 · 컴퓨존 계정(암호는 이 PC 에서만 풀림)"),
    ("customers", "📒 거래처", "customers", True, "거래처 목록"),
    ("quote_archive", "🗂️ 견적서 보관함", "quote_archive", True, "내려받거나 올린 견적서 Excel · PDF"),
    ("manual_quotes", "📝 새 견적 작성본", "manual_quotes", True, "직접 만든 견적과 올린 PDF 원본"),
    ("task_reports", "📋 작업내역서", "task_reports", True, "작업내역서(서명 포함)"),
    ("trade_statements", "🧾 거래명세서", "trade_statements", True, "거래명세서와 서명받은 명세서 파일"),
    ("memos", "🗒️ 메모장", "memos", True, "메모와 붙인 그림"),
    ("cache", "🗃️ 수집 캐시", "cache", False, "Apple · 컴퓨존 수집 스냅샷(지워도 다시 받음 — 보통은 뺌)"),
]
EXCLUDED = ("AI-PC-Quote-Compuzone",)  # 넣지 않는 폴더(설명용)
JOB_KEEP_SEC = 3600

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def _files(sub: str) -> list[Path]:
    base = DATA_DIR / sub
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*") if p.is_file() and not p.name.endswith((".tmp", ".part")))


def info() -> list[dict[str, Any]]:
    """항목별 파일 수 · 크기(화면의 고르기 목록)."""
    out = []
    for key, label, sub, default, desc in ITEMS:
        files = _files(sub)
        size = 0
        for p in files:
            try:
                size += p.stat().st_size
            except OSError:
                pass
        out.append({"key": key, "label": label, "folder": f"data/{sub}", "default": default, "desc": desc,
                    "files": len(files), "bytes": size})
    return out


def _app_version() -> str:
    try:
        import re

        m = re.search(r'version="([^"]+)"', (ROOT / "app" / "main.py").read_text(encoding="utf-8"))
        return m.group(1) if m else ""
    except OSError:
        return ""


RESTORE_TEXT = """컴퓨존 견적 자동화 — 전체 백업

복구하는 방법
  1. 앱을 끕니다(⛔ 종료 또는 stop.bat).
  2. 이 ZIP 을 프로그램 폴더(START.bat 이 있는 폴더)에 풉니다. 같은 이름의 파일은 덮어씁니다.
     ZIP 안의 data 폴더가 프로그램 폴더의 data 폴더 자리로 들어가야 합니다.
  3. START.bat 으로 다시 켭니다.

알아 둘 것
  - 메일(Gmail 앱 비밀번호) · 컴퓨존 계정 비밀번호는 이 백업을 만든 PC 의 Windows 계정에서만 풀립니다.
    다른 PC 에 복구하면 ⚙️ 설정 → 🧩 견적요소에서 비밀번호를 다시 넣어 주세요.
  - 컴퓨존 장바구니용 Edge 프로필(로그인 상태)은 넣지 않았습니다. 다음 장바구니 담기 때 다시 로그인합니다.
  - 들어 있는 항목과 파일 수는 manifest.json 에 있습니다.
"""


def start(folder: str, keys: list[str]) -> dict[str, Any]:
    target = Path(folder)
    if not target.is_dir():
        raise FileNotFoundError(f"백업 폴더를 찾을 수 없습니다: {folder}")
    chosen = [it for it in ITEMS if it[0] in set(keys or [])]
    if not chosen:
        raise ValueError("백업할 항목을 하나 이상 고르세요.")
    with _lock:
        now = time.time()
        for k in [k for k, j in _jobs.items() if now - j["created"] > JOB_KEEP_SEC]:
            _jobs.pop(k, None)
        if any(j["state"] == "running" for j in _jobs.values()):
            raise RuntimeError("전체 백업이 이미 진행 중입니다.")
        job_id = uuid.uuid4().hex[:10]
        _jobs[job_id] = {"id": job_id, "state": "running", "done": 0, "total": 0, "done_bytes": 0, "total_bytes": 0,
                         "current": "파일 목록을 만드는 중…", "path": "", "size": 0, "skipped": [], "error": "",
                         "items": [it[1] for it in chosen], "created": now}
    threading.Thread(target=_run, args=(job_id, target, chosen), daemon=True).start()
    return status(job_id)


def _set(job_id: str, **kw: Any) -> None:
    with _lock:
        _jobs[job_id].update(kw)


def _run(job_id: str, target: Path, chosen: list[tuple[str, str, str, bool, str]]) -> None:
    stamp = datetime.now()
    stem = f"전체백업_{stamp.strftime('%Y%m%d_%H%M%S')}"
    zip_path = target / f"{stem}.zip"
    seq = 2
    while zip_path.exists():
        zip_path = target / f"{stem}_{seq}.zip"
        seq += 1
    part = zip_path.with_name(zip_path.name + ".part")
    try:
        plan: list[tuple[str, Path]] = []
        counts: dict[str, int] = {}
        for key, label, sub, _d, _desc in chosen:
            files = _files(sub)
            counts[key] = len(files)
            plan += [(key, p) for p in files]
        sizes = {}
        for _k, p in plan:
            try:
                sizes[p] = p.stat().st_size
            except OSError:
                sizes[p] = 0
        _set(job_id, total=len(plan), total_bytes=sum(sizes.values()))
        skipped: list[str] = []
        done_bytes = 0
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for n, (_key, p) in enumerate(plan, 1):
                rel = p.relative_to(ROOT).as_posix()
                _set(job_id, current=rel)
                try:
                    zf.writestr(rel, p.read_bytes())  # 읽기만 한다(앱이 쓰는 중이어도 그 순간의 내용)
                except OSError as e:
                    skipped.append(f"{rel} ({e.strerror or e})")
                done_bytes += sizes.get(p, 0)
                _set(job_id, done=n, done_bytes=done_bytes, skipped=list(skipped))
            readme = DATA_DIR / "README.txt"
            if readme.is_file():
                try:
                    zf.writestr("data/README.txt", readme.read_bytes())
                except OSError:
                    pass
            manifest = {
                "app": "컴퓨존 견적 자동화",
                "version": _app_version(),
                "created": stamp.strftime("%Y-%m-%d %H:%M:%S"),
                "items": [{"key": k, "label": lab, "folder": f"data/{sub}", "files": counts.get(k, 0)} for k, lab, sub, _d, _x in chosen],
                "excluded": [f"data/{x}" for x in EXCLUDED] + [f"data/{sub}" for k, _l, sub, _d, _x in ITEMS if k not in counts],
                "skipped": skipped,
            }
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.writestr("복구방법.txt", RESTORE_TEXT)
        part.replace(zip_path)
        _set(job_id, state="done", current="", path=str(zip_path), size=zip_path.stat().st_size)
    except Exception as e:  # noqa: BLE001 - 실패를 화면에 알린다
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        _set(job_id, state="error", error=f"백업 파일을 만들지 못했습니다: {e}")


def status(job_id: str) -> dict[str, Any]:
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            raise KeyError(job_id)
        return {k: (list(v) if isinstance(v, list) else v) for k, v in j.items() if k != "created"}
