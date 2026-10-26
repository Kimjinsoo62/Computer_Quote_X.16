# -*- coding: utf-8 -*-
"""거래처(견적처) 목록 저장·관리 (customers.json)

[설정관리 → 견적정보] 탭에서 거래처를 생성·저장·검색·삭제하고, 하나를 골라 현재 견적처(quote_info.json)로 불러온다.
quote_info.json 과는 따로 둔다(save_quote_info 는 모달이 보내지 않은 키를 기본값으로 되돌리므로 섞지 않는다).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).resolve().parent.parent / "customers.json"
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
