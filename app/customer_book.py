# -*- coding: utf-8 -*-
"""거래처(견적처) 목록 저장·관리 (customers.json)

[설정관리 → 견적정보] 탭에서 거래처를 생성·저장·검색·삭제하고, 하나를 골라 현재 견적처(quote_info.json)로 불러온다.
quote_info.json 과는 따로 둔다(save_quote_info 는 모달이 보내지 않은 키를 기본값으로 되돌리므로 섞지 않는다).
"""
from __future__ import annotations

import csv
import io
import json
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).resolve().parent.parent / "customers.json"
QUOTE_INFO_FILE = Path(__file__).resolve().parent.parent / "quote_info.json"
FIELDS = ("name", "manager", "address", "phone", "email", "memo")
_lock = threading.Lock()


def _load() -> list[dict[str, Any]]:
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [c for c in data if isinstance(c, dict) and c.get("id")]
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save(items: list[dict[str, Any]]) -> None:
    DATA_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def list_customers() -> list[dict[str, Any]]:
    """최근 수정한 거래처가 앞."""
    with _lock:
        items = _load()
    return sorted(items, key=lambda c: c.get("updated_at", ""), reverse=True)


def save_customer(data: dict[str, Any]) -> dict[str, Any]:
    """id 가 있으면 수정, 없으면 생성. 같은 상호명이 이미 있으면 그 항목을 갱신한다."""
    clean = {k: str(data.get(k) or "").strip()[:200] for k in FIELDS}
    if not clean["name"]:
        raise ValueError("거래처 상호명을 입력해 주세요.")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        items = _load()
        target_id = str(data.get("id") or "").strip()
        found = next((c for c in items if c["id"] == target_id), None) if target_id else None
        if found is None:
            found = next((c for c in items if c.get("name") == clean["name"]), None)
        if found is None:
            found = {"id": datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3], "created_at": now}
            items.append(found)
        found.update(clean, updated_at=now)
        _save(items)
    return found


def delete_customer(customer_id: str) -> bool:
    with _lock:
        items = _load()
        keep = [c for c in items if c["id"] != customer_id]
        if len(keep) == len(items):
            return False
        _save(keep)
    return True


def backup_customers(folder: str) -> dict[str, Any]:
    """거래처 목록(customers.json) + 견적정보(quote_info.json)를 folder 에 ZIP 한 파일로 저장한다.
    엑셀에서 바로 열어 보도록 거래처목록.csv(UTF-8 BOM)도 함께 넣는다."""
    target = Path(folder)
    if not target.is_dir():
        raise FileNotFoundError(f"백업 폴더를 찾을 수 없습니다: {folder}")
    with _lock:
        items = _load()
    items = sorted(items, key=lambda c: c.get("name", ""))
    stamp = datetime.now()
    stem = f"거래처_백업_{stamp.strftime('%Y%m%d_%H%M%S')}"
    zip_path = target / f"{stem}.zip"
    seq = 2
    while zip_path.exists():
        zip_path = target / f"{stem}_{seq}.zip"
        seq += 1
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["상호명", "담당자", "주소", "연락처", "이메일", "메모", "수정일"])
    for c in items:
        writer.writerow([c.get(k, "") for k in FIELDS] + [c.get("updated_at", "")])
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("customers.json", json.dumps(items, ensure_ascii=False, indent=2))
        if QUOTE_INFO_FILE.is_file():
            zf.write(QUOTE_INFO_FILE, "quote_info.json")
        zf.writestr("거래처목록.csv", "\ufeff" + buf.getvalue())
        zf.writestr(
            "README.txt",
            "컴퓨존 견적 자동화 - 거래처 · 견적정보 백업\n"
            f"백업 시각: {stamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"거래처 수: {len(items)}곳\n\n"
            "customers.json  : 거래처 목록(복구할 때 프로그램 폴더의 같은 이름 파일에 덮어쓰기)\n"
            "quote_info.json : 견적처 · 공급자 · 추가 품목 금액 · 입금 계좌(복구할 때 같은 이름 파일에 덮어쓰기)\n"
            "거래처목록.csv   : 엑셀에서 보는 용도\n",
        )
    return {
        "dir": str(target),
        "file": zip_path.name,
        "path": str(zip_path),
        "count": len(items),
        "size": zip_path.stat().st_size,
    }
