# -*- coding: utf-8 -*-
"""견적처/공급자 정보 저장·관리 (quote_info.json)"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import SUPPLIER

DATA_FILE = Path(__file__).resolve().parent.parent / "quote_info.json"

DEFAULT_INFO = {
    "supplier_name": SUPPLIER.get("name", "대한민국주식회사"),
    "supplier_manager": SUPPLIER.get("manager", "홍길동대표"),
    "supplier_address": SUPPLIER.get("address", "서울시"),
    "supplier_phone": SUPPLIER.get("phone", "010-1234-5678"),
    "supplier_email": SUPPLIER.get("email", "email1234@gmail.com"),
    # 사업자등록번호: 견적서 폼7·폼8 등록번호 칸과 작업내역서에 쓰인다(기존 고정값을 기본값으로 둔다)
    "supplier_business_number": SUPPLIER.get("business_number", "121-18-24250"),
    "customer_name": "대한민국주식회사",
    "customer_manager": "홍길동대표",
    "customer_address": "서울시",
    "customer_phone": "010-1234-5678",
    "customer_email": "email1234@gmail.com",
    "quote_date": "",  # 빈 값이면 당일 날짜 사용
    "monitor_price": "180000",
    "keyboard_price": "30000",
    "setup_price": "0",
    # 입금 계좌 (계좌번호를 넣으면 모든 Excel 견적서 하단에 표시)
    "bank_name": "",
    "bank_account": "",
    "bank_holder": "",
}


def parse_won(value, default: int = 0) -> int:
    raw = (
        str(value if value is not None else "")
        .replace(",", "")
        .replace("원", "")
        .replace(" ", "")
        .strip()
    )
    if raw == "":
        return int(default)
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return int(default)


def extra_item_prices() -> dict[str, int]:
    info = load_quote_info()
    return {
        "모니터": parse_won(info.get("monitor_price"), 180000),
        "키보드마우스": parse_won(info.get("keyboard_price"), 30000),
        "초기 세팅비": parse_won(info.get("setup_price"), 0),
    }


def bank_account_line(info: dict) -> str:
    """Excel 견적서·작업내역서 공통 '입금 계좌 : 국민은행 123-456 (예금주 : 홍길동)' 문구. 계좌번호가 없으면 빈 문자열."""
    account = (info.get("bank_account") or "").strip()
    if not account:
        return ""
    bank = (info.get("bank_name") or "").strip()
    holder = (info.get("bank_holder") or "").strip()
    text = f"■ 입금 계좌 : {bank + ' ' if bank else ''}{account}"
    if holder:
        text += f"  (예금주 : {holder})"
    return text


def load_quote_info() -> dict:
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_INFO, **{k: v for k, v in data.items() if k in DEFAULT_INFO}}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_INFO)


def save_quote_info(data: dict) -> dict:
    info = {k: str(data.get(k, DEFAULT_INFO[k]) or "").strip() for k in DEFAULT_INFO}
    DATA_FILE.write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return info


def reset_quote_info() -> dict:
    if DATA_FILE.exists():
        DATA_FILE.unlink()
    return dict(DEFAULT_INFO)
