# -*- coding: utf-8 -*-
"""직접 만드는 견적(새 견적 만들기): 폼1~8 중 하나를 골라 빈 견적에 품목을 직접 적는다.

저장은 manual_quotes.json(작업내역서와 같은 방식). Excel 은 컴퓨존 · Apple 견적과 같은
build_excel() 을 쓰도록 TodayQuotesResult 한 건으로 바꿔 넘긴다(to_result).
단가는 부가세 포함 금액을 그대로 쓰며 마진 · 만원 올림을 적용하지 않는다.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.quote_engine import Quote, QuotePart, TodayQuotesResult
from app.quote_info import load_quote_info

DATA_FILE = Path(__file__).resolve().parent.parent / "manual_quotes.json"
TAB_LABEL = "직접견적"
# 폼 시트는 A4 세로 1페이지로 강제되므로 행이 너무 많으면 글자가 읽기 어려울 만큼 줄어든다.
MAX_ITEMS = 20
FORM_NUMBERS = range(1, 9)
# 견적마다 따로 두는 견적처(거래처) 정보. 비어 있으면(예전 저장본) 견적정보(quote_info.json)의 견적처를 쓴다.
CUSTOMER_FIELDS = ("name", "manager", "phone", "email", "address")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).replace(",", "").strip() or default)
    except (TypeError, ValueError):
        return default


def clean_items(items: Any) -> list[dict[str, Any]]:
    """화면에서 온 품목 행을 정리한다. 품명이 빈 행은 버리고, 수량은 1 이상, 단가는 0 이상."""
    rows: list[dict[str, Any]] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        rows.append({
            "category": str(it.get("category") or "").strip(),
            "name": name,
            "qty": max(1, _int(it.get("qty"), 1)),
            "unit_price": max(0, _int(it.get("unit_price"), 0)),
        })
    return rows[:MAX_ITEMS]


def clean_customer(value: Any) -> dict[str, str]:
    src = value if isinstance(value, dict) else {}
    return {k: str(src.get(k) or "").strip()[:200] for k in CUSTOMER_FIELDS}


def clean_record(data: dict[str, Any]) -> dict[str, Any]:
    form = _int(data.get("form"), 1)
    return {
        "form": form if form in FORM_NUMBERS else 1,
        "title": str(data.get("title") or "").strip(),
        "quote_date": str(data.get("quote_date") or "").strip() or date.today().isoformat(),
        "customer_info": clean_customer(data.get("customer_info")),
        "items": clean_items(data.get("items")),
    }


def info_override(record: dict[str, Any]) -> dict[str, str]:
    """견적서에 들어갈 견적처를 quote_info 키 이름으로 돌려준다. 견적처 칸이 모두 비면 {}(견적정보 그대로)."""
    cust = clean_customer(record.get("customer_info"))
    if not any(cust.values()):
        return {}
    return {f"customer_{k}": v for k, v in cust.items()}


def total_of(items: list[dict[str, Any]]) -> int:
    return sum(it["qty"] * it["unit_price"] for it in items)


def to_result(record: dict[str, Any]) -> TodayQuotesResult:
    """저장된(또는 작성 중인) 직접 견적을 Excel 용 결과 한 건으로 바꾼다. 품목이 없으면 ValueError."""
    rec = clean_record(record)
    if not rec["items"]:
        raise ValueError("품목을 한 줄 이상 입력하세요(품명이 빈 줄은 빠집니다).")
    parts = [
        QuotePart(
            no=i,
            category=it["category"],
            name=it["name"],
            qty=it["qty"],
            unit_price=it["unit_price"],
            amount=it["qty"] * it["unit_price"],
        )
        for i, it in enumerate(rec["items"], 1)
    ]
    total = sum(p.amount for p in parts)
    quote = Quote(
        tier=TAB_LABEL,
        tier_key="manual",
        model=rec["title"] or "컴퓨터 견적",
        tab_label=TAB_LABEL,
        description="직접 작성한 견적",
        product_no=0,
        url="",
        quote_date=rec["quote_date"],
        parts=parts,
        parts_subtotal=total,
        total=total,
        price_source="manual",
    )
    return TodayQuotesResult(
        quote_date=rec["quote_date"],
        event_url="",
        event_title="새 견적 만들기",
        series=TAB_LABEL,
        quotes=[quote],
        logic_steps=[],
    )


# ---------------------------------------------------------------------------
# 저장 (manual_quotes.json)
# ---------------------------------------------------------------------------
def load_manual_quotes() -> list[dict[str, Any]]:
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_all(records: list[dict[str, Any]]) -> None:
    DATA_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def get_manual_quote(quote_id: str) -> dict[str, Any] | None:
    return next((r for r in load_manual_quotes() if r.get("id") == quote_id), None)


def save_manual_quote(data: dict[str, Any]) -> dict[str, Any]:
    """id 가 있으면 고치고, 없거나 못 찾으면 새로 만든다. customer 는 목록 표시용 거래처 이름(견적처 칸 → 없으면 견적정보)."""
    records = load_manual_quotes()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec = clean_record(data)
    rec["total"] = total_of(rec["items"])
    rec["customer"] = rec["customer_info"]["name"] or (load_quote_info().get("customer_name") or "").strip()
    rec["updated_at"] = now
    quote_id = str(data.get("id") or "")
    for idx, old in enumerate(records):
        if quote_id and old.get("id") == quote_id:
            records[idx] = {**old, **rec, "id": quote_id}
            _save_all(records)
            return records[idx]
    rec = {"id": uuid.uuid4().hex[:12], "created_at": now, **rec}
    records.insert(0, rec)
    _save_all(records)
    return rec


def delete_manual_quote(quote_id: str) -> bool:
    records = load_manual_quotes()
    kept = [r for r in records if r.get("id") != quote_id]
    if len(kept) == len(records):
        return False
    _save_all(kept)
    return True
