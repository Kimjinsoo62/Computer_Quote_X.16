# -*- coding: utf-8 -*-
"""📤 견적서 올리기에서 견적서 내용(거래처 · 합계 · 견적일)을 읽는 도우미.

- Excel(.xlsx) · PDF 를 올리면 둘 다 📥 보관 파일로 들어간다. PDF 는 app/pdf_to_excel.py 가 PDF 모양 그대로 Excel 로
  바꾸고, 이 모듈은 목록에 보일 거래처 · 합계 · 견적일만 best-effort 로 읽는다(parse_rows). 못 읽어도 올리기는 된다.
- 예전(XII.18)에는 PDF 품목을 읽어 📝 작성본(폼1~8)으로 만들었으나, 견적서마다 표 모양이 달라 틀리게 읽는 일이 많아
  "PDF 모양 그대로 Excel" 로 바꿨다. 그때 만든 작성본의 원본 PDF(data/manual_quotes/pdf/)는 그대로 볼 수 있다.

줄 · 칸 목록(rows)에서 품목 줄은 "수량 × 단가 = 금액" 또는 수량 + 금액이 있는 줄, 합계는 '합계 · 총액 · TOTAL' 줄의 금액이다.
견적처는 '견적처 · 수신 · To · 귀하'(강한 표시)를 먼저, '상호 · 회사명'(공급자 칸에도 흔함)은 마지막에 본다.
🧩 견적요소의 공급자(우리 회사) 값은 견적처로 쓰지 않는다.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from app import manual_quote
from app.quote_info import load_quote_info

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

_NUM_RE = re.compile(r"^[-−–]?\s*[₩\\]?\s*-?\d{1,3}(?:,\d{3})+\s*(?:원)?$|^[-−–]?\s*[₩\\]?\s*-?\d+\s*(?:원|개|대|EA|ea|식|SET|set)?$")
_DATE_RE = re.compile(r"(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})")
_PHONE_RE = re.compile(r"0\d{1,2}-\d{3,4}-\d{4}|1[568]\d{2}-\d{4}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_BRACKET_RE = re.compile(r"^\[([^\]]{1,20})\]\s*(.+)$")
_TO_RE = re.compile(r"^(?:to|bill\s*to|수\s*신(?:\s*\(\s*to\s*\))?|받는\s*분|고\s*객)\s*[:：]\s*(.*)$", re.I)
_ATTN_RE = re.compile(r"^attn\.?\s*[:：]\s*(.+)$", re.I)
# 합계 · 세금 줄(품목이 아님). 공백을 뺀 소문자로 비교한다.
_TOTAL_WORDS = ("합계", "총계", "소계", "총액", "총합", "total", "부가세", "부가가치세", "세액", "공급가액", "견적금액", "합계금액",
                "결제금액", "청구금액", "subtotal", "vat")
_SKIP_WORDS = ("유효기간", "계좌", "입금", "은행", "예금주", "사업자", "등록번호", "tel", "fax", "e-mail", "email", "http")
# 견적처 라벨: 강한 표시(문서 전체에서 먼저 찾음) · 약한 표시(공급자 칸 이름으로도 흔해 못 찾았을 때만)
_CUSTOMER_LABELS = ("견적처", "수신", "고객명", "거래처", "귀하", "받는분", "공급받는자")
_CUSTOMER_WEAK_LABELS = ("상호", "상호명", "회사명", "업체명")
_MANAGER_LABELS = ("담당자", "담당", "성명")
_PHONE_LABELS = ("연락처", "전화", "전화번호", "휴대폰", "tel")
_ADDRESS_LABELS = ("주소", "소재지")
_DATE_LABELS = ("견적일", "견적일자", "발행일", "작성일", "일자", "date")


class ImportError_(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 읽기 오류."""


def _norm(s: str) -> str:
    return re.sub(r"[\s:：()·\[\]]", "", str(s or "")).lower()


def _num(s: str) -> int | None:
    t = str(s or "").strip()
    if not t or not _NUM_RE.match(t):
        return None
    neg = t.lstrip().startswith(("-", "−", "–"))
    digits = re.sub(r"[^\d]", "", t)
    if not digits:
        return None
    v = int(digits)
    return -v if neg else v


# ---------------------------------------------------------------------------
# 파일 → 줄(칸 목록)
# ---------------------------------------------------------------------------
def _cell_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int):
        return f"{v:,}" if abs(v) >= 1000 else str(v)
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return " ".join(str(v).split())


def check_xlsx(content: bytes) -> None:
    if not content.startswith(b"PK"):
        raise ImportError_("Excel(.xlsx) 파일이 아닙니다.")
    try:
        zipfile.ZipFile(io.BytesIO(content)).testzip()
    except zipfile.BadZipFile as e:
        raise ImportError_("Excel 파일이 손상되었습니다.") from e


def _xlsx_rows(content: bytes) -> list[list[str]]:
    check_xlsx(content)
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:
        raise ImportError_(f"Excel 파일을 열지 못했습니다: {e}") from e
    rows: list[list[str]] = []
    try:
        for ws in wb.worksheets:
            if ws.sheet_state != "visible" or ws.title in ("서식 목록", "견적요약"):
                continue
            for r in ws.iter_rows(values_only=True):
                cells = [_cell_text(v) for v in r]
                cells = [c for c in cells if c]
                if cells:
                    rows.append(cells)
                if len(rows) > 2000:
                    break
    finally:
        wb.close()
    return rows


# ---------------------------------------------------------------------------
# 줄 → 견적 내용
# ---------------------------------------------------------------------------
def _is_total_row(texts: list[str]) -> bool:
    joined = _norm(" ".join(texts))
    return any(w in joined for w in _TOTAL_WORDS)


# 표 머리글: 숫자 칸 이름 두 개 이상이 있는 줄. 구분(분류) 칸이 있는지로 첫 글자 칸을 구분으로 볼지 정한다.
_HEAD_NUM_WORDS = ("수량", "qty", "q'ty", "단가", "unit", "unitprice", "rate", "금액", "amount", "total", "공급가액", "공급가", "합계")
_HEAD_CAT_WORDS = ("구분", "분류", "부품구분", "구분/품명", "품목", "category", "component", "item", "종류")


def _header_has_category(rows: list[list[str]]) -> bool | None:
    """표 머리글을 찾아 구분 칸이 있으면 True, 없으면 False, 머리글을 못 찾으면 None."""
    for cells in rows:
        heads = [_norm(c).replace("(원)", "") for c in cells]
        if sum(1 for h in heads if any(h == w or h.startswith(w) for w in _HEAD_NUM_WORDS)) >= 2:
            return any(h in _HEAD_CAT_WORDS for h in heads)
    return None


def _item_from_row(cells: list[str], has_category: bool | None = None) -> dict[str, Any] | None:
    """한 줄이 품목이면 {category, name, qty, unit_price, amount} 를, 아니면 None.
    has_category: 표 머리글에 구분 칸이 있는지(None = 모름 → 첫 칸이 짧으면 구분으로 본다)."""
    cells = [c for c in cells if c.strip()]
    if len(cells) < 2:
        return None
    nums = [_num(c) for c in cells]
    # 맨 앞 번호 칸(No) 은 버린다: 작은 정수 + 뒤에 글자 칸이 있을 때
    start = 0
    if nums[0] is not None and 0 < nums[0] < 1000 and any(n is None for n in nums[1:]):
        start = 1
    # 글자 칸(품명 등) 다음에 오는 첫 숫자 묶음
    i = start
    while i < len(cells) and nums[i] is None:
        i += 1
    label = cells[start:i]
    j = i
    while j < len(cells) and nums[j] is not None:
        j += 1
    run = [n for n in nums[i:j] if n is not None]
    if not label or not run:
        return None
    if _is_total_row(label) or any(w in _norm(" ".join(label)) for w in _SKIP_WORDS):
        return None
    qty = unit = amount = None
    vat_from_supply = False
    for k in range(len(run) - 2):  # 수량 · 단가 · 금액 (연속 세 칸)
        q, u, a = run[k], run[k + 1], run[k + 2]
        if 0 < q < 10000 and q * u == a:
            qty, unit, amount = q, u, a
            break
        if 0 < q < 10000 and u > 0 and abs(round(q * u * 1.1) - a) <= max(2, q):  # 공급가 단가 + VAT 포함 금액
            qty, unit, amount, vat_from_supply = q, round(a / q), a, True
            break
    if qty is None and len(run) >= 2:
        q, a = run[0], run[-1]
        if 0 < q < 10000 and a != 0 and abs(a) >= q and a % q == 0:  # 수량 · 금액만
            qty, unit, amount = q, a // q, a
        elif len(run) == 2 and run[0] == run[1] and abs(run[0]) >= 100:  # 단가 = 금액 (수량 칸 없음)
            qty, unit, amount = 1, run[0], run[0]
    if qty is None:
        return None
    # 구분 / 품명
    m = _BRACKET_RE.match(label[0])
    if m:
        category, name = m.group(1).strip(), " ".join([m.group(2)] + label[1:]).strip()
    elif len(label) >= 2 and has_category is not False and len(label[0]) <= 12:
        category, name = label[0], " ".join(label[1:])
    else:
        category, name = "", " ".join(label)
    if not name:
        return None
    return {"category": category[:40], "name": name[:200], "qty": qty, "unit_price": unit, "amount": amount,
            "vat_from_supply": vat_from_supply}


def _value_after(cells: list[str], labels: tuple[str, ...]) -> list[str]:
    """줄 안에서 라벨 칸 바로 다음 칸 값들(라벨이 여러 번 나오면 각각)."""
    out = []
    for k, c in enumerate(cells):
        head, sep, tail = c.replace("：", ":").partition(":")
        if _norm(head) not in labels:
            continue
        if sep and tail.strip():  # "견적일 : 2026-10-31" 처럼 한 칸에 라벨 + 값
            out.append(tail.strip())
        elif k + 1 < len(cells):
            out.append(cells[k + 1])
    return out


def _split_customer(text: str) -> tuple[str, str, str]:
    """'(주) 경방테크롤 (안병욱차장 / 032-818-0892)' · '(주) 경방테크롤 - 안병욱차장 귀하 (032-...)' → (상호, 담당자, 연락처)"""
    text = re.sub(r"\s*귀\s*[하중](?=\s|$|\()", "", str(text or "")).strip()
    manager = phone = ""
    m = re.match(r"^(.*\S)\s*\(([^()]*)\)\s*$", text)
    if m and m.group(1).strip() not in ("(주)", "(유)", "㈜"):
        text, inner = m.group(1).strip(), m.group(2)
        for part in re.split(r"[/,]", inner):
            part = part.strip()
            pm = _PHONE_RE.search(part)
            if pm:
                phone = phone or pm.group(0)
            elif part and not manager:
                manager = re.sub(r"^(?:담당|attn)\s*[:：]?\s*", "", part, flags=re.I)
    if " - " in text:
        text, mgr = text.split(" - ", 1)
        manager = manager or mgr.strip()
    return text.strip(), manager.strip(), phone


def _looks_like_value(v: str) -> bool:
    """라벨 다음 칸이 견적처 이름으로 쓸 만한지(다른 라벨 · 이메일 · 전화가 아님)."""
    return bool(v) and ":" not in v and "：" not in v and not _EMAIL_RE.search(v) and not _PHONE_RE.fullmatch(v.strip())


def _extract_meta(rows: list[list[str]], supplier: dict[str, str]) -> dict[str, Any]:
    sup_vals = {_norm(v) for v in supplier.values() if v and str(v).strip()}

    def ours(v: str) -> bool:  # 공급자(우리 회사) 값이면 견적처로 쓰지 않는다
        return _norm(v) in sup_vals

    cust = {"name": "", "manager": "", "phone": "", "email": "", "address": ""}

    def put(key: str, value: str) -> None:
        value = (value or "").strip()
        if value and not cust[key] and not ours(value):
            cust[key] = value[:200]

    name_row = [-1]  # 견적처 이름을 찾은 줄 번호
    row_no = [0]
    # 라벨로 찾은 담당자 · 연락처 · 주소 후보 (줄 번호, 값). 견적처 줄과 그 아래 것만 쓴다(위쪽은 공급자 칸일 수 있음)
    cands: dict[str, list[tuple[int, str]]] = {"manager": [], "phone": [], "address": []}

    def put_customer(text: str) -> bool:
        name, mgr, ph = _split_customer(text)
        if not name or ours(name) or not _looks_like_value(name):
            return False
        if not cust["name"]:
            name_row[0] = row_no[0]
        put("name", name)
        put("manager", mgr)
        put("phone", ph)
        return True

    quote_date = ""
    grand_total = None
    want_name_next = False
    want_total_next = 0  # 남은 줄 수: '합계' 라벨만 있던 줄 다음 두 줄 안의 금액을 합계로 본다
    for ri, cells in enumerate(rows):
        row_no[0] = ri
        texts = [c for c in cells if c.strip()]
        nums = [_num(t) for t in texts]
        if want_total_next:  # 앞 줄이 '합계' 라벨만 있었으면 이 줄(또는 그다음 줄)의 금액이 합계
            vals = [abs(v) for v in nums if v and abs(v) >= 1000]
            if vals:
                grand_total = max(grand_total or 0, max(vals))
                want_total_next = 0
            else:
                want_total_next -= 1
        if want_name_next and texts:  # 'BILL TO:' 처럼 라벨만 있던 다음 줄 첫 칸
            put_customer(texts[0])
            want_name_next = False
        for ci, c in enumerate(texts):
            m = _TO_RE.match(c)
            if m and not cust["name"]:
                if m.group(1).strip():
                    put_customer(m.group(1))
                else:
                    want_name_next = True
            m = _ATTN_RE.match(c)
            if m:
                for part in re.split(r"/", m.group(1)):
                    pm = _PHONE_RE.search(part)
                    if pm:
                        put("phone", pm.group(0))
                    elif part.strip():
                        put("manager", re.sub(r"\s*귀\s*하$", "", part.strip()))
            if c.endswith(("귀하", "귀중")) and len(c) > 2 and not _TO_RE.match(c):
                if ci > 0 and _norm(texts[ci - 1]) in _MANAGER_LABELS:
                    put("manager", re.sub(r"\s*귀\s*[하중]$", "", c))
                else:
                    put_customer(c)
        if not cust["name"]:
            for v in _value_after(texts, _CUSTOMER_LABELS):
                if _looks_like_value(v) and put_customer(v):
                    break
        for key, labels in (("manager", _MANAGER_LABELS), ("phone", _PHONE_LABELS), ("address", _ADDRESS_LABELS)):
            for v in _value_after(texts, labels):
                if key == "phone":
                    pm = _PHONE_RE.search(v or "")
                    v = pm.group(0) if pm else ""
                elif not _looks_like_value(v):
                    continue
                if v and not ours(v):
                    cands[key].append((ri, re.sub(r"\s*귀\s*하$", "", v)))
        if not cust["email"]:
            for c in texts:
                m = _EMAIL_RE.search(c)
                if m and not ours(m.group(0)):
                    cust["email"] = m.group(0)
                    break
        if not quote_date:
            for v in _value_after(texts, _DATE_LABELS) + texts:
                m = _DATE_RE.search(v)
                if m:
                    y, mo, d = (int(x) for x in m.groups())
                    try:
                        quote_date = date(y, mo, d).isoformat()
                    except ValueError:
                        pass
                    break
        if _is_total_row([t for t, n in zip(texts, nums) if n is None]):
            vals = [abs(v) for v in nums if v]
            if vals:
                grand_total = max(grand_total or 0, max(vals))
            else:
                want_total_next = 2
    if not cust["name"]:
        for ri, cells in enumerate(rows):
            row_no[0] = ri
            texts = [c for c in cells if c.strip()]
            if any(v for v in _value_after(texts, _CUSTOMER_WEAK_LABELS) if _looks_like_value(v) and put_customer(v)):
                break
    if cust["name"]:
        for key, found in cands.items():
            for ri, v in found:
                if ri >= name_row[0]:
                    put(key, v)
                    break
    # 견적처를 못 찾았으면 담당자 · 연락처 · 이메일도 공급자 것일 수 있어 모두 비운다
    if not cust["name"]:
        cust = {k: "" for k in cust}
    return {"customer": cust, "quote_date": quote_date, "grand_total": grand_total}


def parse_rows(rows: list[list[str]]) -> dict[str, Any]:
    info = load_quote_info()
    supplier = {k: info.get(k, "") for k in ("supplier_name", "supplier_manager", "supplier_phone", "supplier_email",
                                             "supplier_address")}
    meta = _extract_meta(rows, supplier)
    has_cat = _header_has_category(rows)
    items = [it for it in (_item_from_row(r, has_cat) for r in rows) if it]
    warnings: list[str] = []
    gt = meta["grand_total"]
    # 요약 줄(예: 폼7 '데스크탑 컴퓨터 1대' 줄): 그 줄을 빼면 품목 합이 PDF 합계와 같아지고, 그 줄이 나머지 합과 같을 때만 뺀다
    if gt and len(items) > 2:
        total_all = sum(it["amount"] for it in items)
        if total_all != gt:
            for k, it in enumerate(items):
                if total_all - it["amount"] == gt and it["amount"] == gt:
                    items = items[:k] + items[k + 1:]
                    break
    total = sum(it["amount"] for it in items)
    if gt and items and total != gt:
        if abs(round(total * 1.1) - gt) <= max(10, len(items) * 2):
            for it in items:
                it["unit_price"] = round(it["unit_price"] * 1.1)
                it["amount"] = it["unit_price"] * it["qty"]
            warnings.append("PDF 의 단가가 공급가(부가세 별도)로 보여 부가세 포함(×1.1) 단가로 바꿨습니다. 원 단위가 조금 다를 수 있습니다.")
            total = sum(it["amount"] for it in items)
        if total != gt:
            warnings.append(f"읽은 품목 합계 {total:,}원이 PDF 에 적힌 합계 {gt:,}원과 다릅니다. 품목을 꼭 확인해 주세요.")
    if any(it["vat_from_supply"] for it in items):
        warnings.append("일부 품목은 공급가 단가 대신 부가세 포함 금액 ÷ 수량으로 단가를 넣었습니다.")
    if any(it["unit_price"] < 0 for it in items):
        warnings.append("할인(음수) 행은 작성본에 넣을 수 없어 0원으로 들어갑니다. 합계가 달라지니 확인해 주세요.")
    if len(items) > manual_quote.MAX_ITEMS:
        warnings.append(f"품목이 {len(items)}줄이라 앞 {manual_quote.MAX_ITEMS}줄만 넣었습니다(작성본 최대 {manual_quote.MAX_ITEMS}줄).")
        items = items[: manual_quote.MAX_ITEMS]
    if not gt:
        warnings.append("PDF 에서 합계 금액을 찾지 못해 품목 합계를 확인하지 못했습니다.")
    if not meta["customer"]["name"]:
        warnings.append("견적처(받는 곳)를 찾지 못했습니다. 지금은 🧩 기본 견적처로 표시되니 작성 창에서 견적처를 정해 주세요.")
    if not meta["quote_date"]:
        warnings.append("견적일을 찾지 못해 오늘 날짜로 넣었습니다.")
    return {
        "items": [{k: it[k] for k in ("category", "name", "qty", "unit_price")} for it in items],
        "items_total": sum(max(0, it["unit_price"]) * it["qty"] for it in items),
        "grand_total": gt,
        "customer_info": meta["customer"],
        "quote_date": meta["quote_date"],
        "warnings": warnings,
    }


def summarize_xlsx(content: bytes) -> dict[str, Any]:
    """보관 파일 목록에 보일 거래처 · 합계 · 견적일만 best-effort 로 읽는다(실패해도 빈 값)."""
    rows = _xlsx_rows(content)
    try:
        parsed = parse_rows(rows)
    except Exception:
        return {"customer": "", "total": 0, "quote_date": ""}
    return {
        "customer": parsed["customer_info"]["name"],
        "total": parsed["grand_total"] or parsed["items_total"] or 0,
        "quote_date": parsed["quote_date"],
    }


def title_from_filename(filename: str) -> str:
    stem = Path(filename or "").stem
    return " ".join(re.sub(r"[_]+", " ", stem).split())[:120] or "가져온 견적서"
