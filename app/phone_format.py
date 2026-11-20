# -*- coding: utf-8 -*-
"""전화번호 표기 맞춤: 숫자만 쓴 번호(01023678481)를 010-2367-8481 처럼 - 를 넣어 돌려준다.

화면(static/js/app.js 의 formatPhone)과 같은 규칙이다. 한쪽을 고치면 다른 쪽도 맞춘다.
이미 - 가 있거나 글자 · 기호가 섞인 값, 자릿수가 맞지 않는 값은 그대로 둔다.
"""
from __future__ import annotations

from typing import Any


def format_phone(value: Any) -> str:
    text = str(value or "").strip()
    digits = text.replace(" ", "")
    if not digits.isdigit():
        return text
    if digits[0] != "0" and len(digits) in (9, 10):  # 엑셀이 숫자로 바꿔 앞의 0 이 빠진 번호
        digits = "0" + digits
    if digits.startswith("02") and len(digits) in (9, 10):
        return f"02-{digits[2:-4]}-{digits[-4:]}"
    if len(digits) in (10, 11) and digits.startswith("0"):
        return f"{digits[:3]}-{digits[3:-4]}-{digits[-4:]}"
    if len(digits) == 8 and digits[:2] in ("15", "16", "18"):
        return f"{digits[:4]}-{digits[4:]}"
    return text
