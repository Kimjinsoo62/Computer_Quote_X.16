# -*- coding: utf-8 -*-
"""견적서 보관함: 앱에서 내려받은 최종 선택 Excel 견적서를 quote_archive/ 에 저장·관리·백업한다.

- 파일: quote_archive/{거래처명_…}_{id}.xlsx (파일명이 거래처명으로 시작, id 는 저장 시각 기반)
- 목록: quote_archive/index.json (최신이 앞)
- 백업: 백업 폴더(app_settings.json 의 task_report_backup_dir, 작업내역서와 같은 폴더)에
        견적서_백업_YYYYMMDD_HHMMSS.zip (index.json + 모든 xlsx)
"""
from __future__ import annotations

import json
import re
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = BASE_DIR / "quote_archive"
INDEX_FILE = ARCHIVE_DIR / "index.json"
_lock = threading.Lock()
_BAD_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_filename_part(text: str, limit: int = 120) -> str:
    """Windows 파일명에 못 쓰는 글자를 공백으로 바꾸고 공백을 하나로 줄인다."""
    cleaned = " ".join(_BAD_RE.sub(" ", str(text or "")).split()).strip(" .")
    return cleaned[:limit]


def _load_index() -> list[dict[str, Any]]:
    if INDEX_FILE.exists():
        try:
            data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_index(entries: list[dict[str, Any]]) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def list_entries() -> list[dict[str, Any]]:
    """최신 저장이 앞. 파일이 사라진 항목은 missing=True 로 표시한다."""
    with _lock:
        entries = _load_index()
    for e in entries:
        e["missing"] = not (ARCHIVE_DIR / e.get("stored_name", "")).is_file()
    return entries


def get_entry(entry_id: str) -> dict[str, Any] | None:
    for e in _load_index():
        if e.get("id") == entry_id:
            return e
    return None


def entry_path(entry: dict[str, Any]) -> Path:
    return ARCHIVE_DIR / entry.get("stored_name", "")


def add_entry(content: bytes, *, filename: str, meta: dict[str, Any]) -> dict[str, Any]:
    """Excel 바이트를 보관하고 목록에 넣는다. filename 은 사용자에게 보이는 한글 파일명."""
    with _lock:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        entry_id = now.strftime("%Y%m%d-%H%M%S-") + f"{now.microsecond // 1000:03d}"
        display = safe_filename_part(filename) or "견적서.xlsx"
        if not display.lower().endswith(".xlsx"):
            display += ".xlsx"
        # 탐색기에서 거래처명 순으로 정렬되도록 id 는 뒤에 붙인다(예전 보관본은 id 가 앞, stored_name 으로 찾으므로 그대로 동작).
        stored = f"{display[:-5]}_{entry_id}.xlsx"
        (ARCHIVE_DIR / stored).write_bytes(content)
        entry = {
            "id": entry_id,
            "saved_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "filename": display,
            "stored_name": stored,
            "size": len(content),
            "note": "",
            **{k: v for k, v in meta.items() if v is not None},
        }
        entries = _load_index()
        entries.insert(0, entry)
        _save_index(entries)
    return entry


def update_note(entry_id: str, note: str) -> dict[str, Any] | None:
    with _lock:
        entries = _load_index()
        for e in entries:
            if e.get("id") == entry_id:
                e["note"] = str(note or "").strip()[:200]
                _save_index(entries)
                return e
    return None


def delete_entry(entry_id: str) -> bool:
    with _lock:
        entries = _load_index()
        keep = [e for e in entries if e.get("id") != entry_id]
        if len(keep) == len(entries):
            return False
        for e in entries:
            if e.get("id") == entry_id:
                try:
                    entry_path(e).unlink(missing_ok=True)
                except OSError:
                    pass
        _save_index(keep)
    return True


def archive_stats() -> dict[str, Any]:
    entries = _load_index()
    size = 0
    for e in entries:
        p = entry_path(e)
        if p.is_file():
            size += p.stat().st_size
    return {"count": len(entries), "size": size, "dir": str(ARCHIVE_DIR)}


def backup_archive(folder: str) -> dict[str, Any]:
    """보관함 전체(index.json + xlsx)를 folder 에 ZIP 한 파일로 저장한다."""
    target = Path(folder)
    if not target.is_dir():
        raise FileNotFoundError(f"백업 폴더를 찾을 수 없습니다: {folder}")
    with _lock:
        entries = _load_index()
        stamp = datetime.now()
        stem = f"견적서_백업_{stamp.strftime('%Y%m%d_%H%M%S')}"
        zip_path = target / f"{stem}.zip"
        seq = 2
        while zip_path.exists():
            zip_path = target / f"{stem}_{seq}.zip"
            seq += 1
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("index.json", json.dumps(entries, ensure_ascii=False, indent=2))
            copied = 0
            for e in entries:
                p = entry_path(e)
                if p.is_file():
                    zf.write(p, f"excel/{p.name}")
                    copied += 1
            zf.writestr(
                "README.txt",
                "컴퓨존 견적 자동화 - 견적서 보관함 백업\n"
                f"백업 시각: {stamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"견적서 수: {copied}건\n\n"
                "index.json : 보관함 목록(복구할 때 프로그램 폴더의 quote_archive/index.json 에 덮어쓰기)\n"
                "excel/     : 보관된 Excel 견적서(복구할 때 quote_archive/ 에 그대로 복사)\n",
            )
    return {
        "dir": str(target),
        "file": zip_path.name,
        "path": str(zip_path),
        "count": copied,
        "size": zip_path.stat().st_size,
    }
