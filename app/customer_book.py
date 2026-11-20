# -*- coding: utf-8 -*-
"""거래처(견적처) 목록 저장·관리 (customers.json)

[설정 → 견적정보] 탭에서 거래처를 생성·저장·검색·삭제하고, 하나를 골라 현재 견적처(quote_info.json)로 불러온다.
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

from app.data_paths import CUSTOMERS_FILE as DATA_FILE  # data/customers/customers.json
from app.data_paths import QUOTE_INFO_FILE  # data/settings/quote_info.json
from app.phone_format import format_phone
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


def _clean(data: dict[str, Any]) -> dict[str, str]:
    clean = {k: str(data.get(k) or "").strip()[:200] for k in FIELDS}
    clean["phone"] = format_phone(clean["phone"])
    return clean


def _save(items: list[dict[str, Any]]) -> None:
    DATA_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def list_customers() -> list[dict[str, Any]]:
    """최근 수정한 거래처가 앞."""
    with _lock:
        items = _load()
    return sorted(items, key=lambda c: c.get("updated_at", ""), reverse=True)


def save_customer(data: dict[str, Any]) -> dict[str, Any]:
    """id 가 있으면 수정, 없으면 생성. 같은 상호명이 이미 있으면 그 항목을 갱신한다."""
    clean = _clean(data)
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


def add_if_new(data: dict[str, Any] | None, memo: str = "") -> dict[str, Any] | None:
    """거래명세서 · 작업내역서를 저장할 때 부른다. 상호명이 거래처 목록에 없으면 새로 등록하고 그 항목을, 이미 있거나
    상호명이 비었으면 None 을 돌려준다. 이미 있는 거래처는 건드리지 않는다(목록에서 고친 값을 덮어쓰지 않도록)."""
    src = data if isinstance(data, dict) else {}
    clean = _clean(src)
    if not clean["name"]:
        return None
    if memo and not clean["memo"]:
        clean["memo"] = memo[:200]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        items = _load()
        if any((c.get("name") or "").strip() == clean["name"] for c in items):
            return None
        found = {"id": datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3], "created_at": now, **clean, "updated_at": now}
        items.append(found)
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
            "customers.json  : 거래처 목록(복구할 때 프로그램 폴더의 data\\customers\\customers.json 에 덮어쓰기)\n"
            "quote_info.json : 견적처 · 공급자 · 추가 품목 금액 · 입금 계좌(복구할 때 data\\settings\\quote_info.json 에 덮어쓰기)\n"
            "거래처목록.csv   : 엑셀에서 보는 용도\n",
        )
    return {
        "dir": str(target),
        "file": zip_path.name,
        "path": str(zip_path),
        "count": len(items),
        "size": zip_path.stat().st_size,
    }


# ---------------------------------------------------------------------------
# 엑셀(.xlsx) · CSV 거래처 목록 가져오기
# ---------------------------------------------------------------------------
# 머리글 이름(공백 · 괄호 · 기호를 뺀 소문자)으로 열을 찾는다. 앞에 있을수록 우선이다.
# 견적서 견적처 칸(상호 · 담당자 · 연락처 · 이메일 · 주소)에 들어갈 값만 뽑고, 사업자번호 · 업태 · 비고 등은 메모로 모은다.
IMPORT_HEADERS: dict[str, tuple[str, ...]] = {
    "name": ("상호명", "상호", "거래처명", "거래처", "업체명", "업체", "회사명", "회사", "법인명", "고객명", "고객사",
             "납품처", "상호법인명", "company", "companyname", "name"),
    "manager": ("담당자명", "담당자", "담당", "대표자명", "대표자", "대표", "성명", "이름", "manager", "contact"),
    "phone": ("휴대폰번호", "휴대폰", "핸드폰번호", "핸드폰", "휴대전화", "연락처", "전화번호", "전화", "대표번호", "tel",
              "phone", "mobile"),
    "email": ("이메일주소", "이메일", "전자우편", "메일주소", "메일", "email", "mail"),
    "address": ("사업장주소", "사업장소재지", "주소", "소재지", "address"),
}
MEMO_HEADERS = ("사업자등록번호", "사업자번호", "등록번호", "업태", "종목", "팩스", "fax", "비고", "메모", "참고", "memo", "note")
IMPORT_MAX_ROWS = 5000
IMPORT_MAX_BYTES = 10 * 1024 * 1024
_HEAD_STRIP = str.maketrans("", "", " \t\r\n()[]{}<>·.:/_-*★☆※")


def _norm_head(value: Any) -> str:
    return str(value or "").strip().lower().translate(_HEAD_STRIP)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return " ".join(str(value).split())


_format_phone = format_phone  # 엑셀 가져오기용 이름(app/phone_format.py 로 옮김)


def _match_columns(header: list[Any]) -> dict[str, list[int]]:
    """머리글 한 줄 → {필드: [열 번호(우선순)]}. 같은 열을 두 필드에 쓰지 않는다. memo 는 남은 참고 열."""
    heads = [_norm_head(h) for h in header]
    used: set[int] = set()
    found: dict[str, list[int]] = {}
    for field, names in IMPORT_HEADERS.items():
        cols: list[int] = []
        for exact in (True, False):  # 같은 이름을 먼저, 다음에 그 이름을 포함한 머리글(예: "거래처 상호명")
            for name in names:
                for i, h in enumerate(heads):
                    if i in used or i in cols or not h:
                        continue
                    if (h == name) if exact else (len(name) >= 2 and name in h):
                        cols.append(i)
        if cols:
            found[field] = cols
            used.update(cols)
    memo = [i for i, h in enumerate(heads) if i not in used and h and any(h == m or m in h for m in MEMO_HEADERS)]
    if memo:
        found["memo"] = memo
    return found


def _sheets_from_upload(filename: str, content: bytes) -> list[tuple[str, list[list[Any]]]]:
    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = None
        for enc in ("utf-8-sig", "cp949"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("CSV 파일의 글자 인코딩을 읽지 못했습니다(UTF-8 또는 CP949 로 저장하세요).")
        return [("CSV", list(csv.reader(io.StringIO(text)))[: IMPORT_MAX_ROWS + 30])]
    if name.endswith(".xls"):
        raise ValueError("예전 Excel 형식(.xls)은 읽지 못합니다. Excel 에서 [다른 이름으로 저장 → Excel 통합 문서(.xlsx)]로 바꿔 올려 주세요.")
    if not name.endswith((".xlsx", ".xlsm")):
        raise ValueError("Excel(.xlsx) 또는 CSV 파일만 올릴 수 있습니다.")
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:  # 손상 · 암호 걸린 파일 등
        raise ValueError(f"Excel 파일을 열지 못했습니다: {e}") from e
    sheets = []
    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append(list(row))
                if len(rows) > IMPORT_MAX_ROWS + 30:
                    break
            sheets.append((ws.title, rows))
    finally:
        wb.close()
    return sheets


def parse_customer_upload(filename: str, content: bytes) -> dict[str, Any]:
    """거래처 목록 파일에서 견적처 칸에 들어갈 값을 뽑는다(저장은 하지 않음).

    시트마다 위쪽 30줄 안에서 상호 열 + 다른 칸 하나 이상이 있는 머리글 줄을 찾고, 그 아래 줄을 거래처로 읽는다.
    상호가 빈 줄 · 합계 줄은 건너뛰고, 같은 상호가 여러 번 나오면 빈 칸을 서로 채워 한 곳으로 합친다.
    """
    if len(content) > IMPORT_MAX_BYTES:
        raise ValueError("파일이 너무 큽니다(10MB 이하).")
    merged: dict[str, dict[str, str]] = {}
    sheets_used: list[dict[str, Any]] = []
    skipped = 0
    for title, rows in _sheets_from_upload(filename, content):
        head_idx, cols = -1, {}
        for i, row in enumerate(rows[:30]):
            m = _match_columns(row)
            if "name" in m and len(m) >= 2:
                head_idx, cols = i, m
                break
        if head_idx < 0:
            continue
        header = rows[head_idx]

        def cell(row: list[Any], c: int) -> str:
            return _cell_text(row[c]) if c < len(row) else ""

        count = 0
        for row in rows[head_idx + 1:][:IMPORT_MAX_ROWS]:
            got = {f: next((v for v in (cell(row, c) for c in cs) if v), "") for f, cs in cols.items() if f != "memo"}
            name = got.get("name", "")
            if not name or _norm_head(name) in ("합계", "총계", "소계", "계"):
                if any(_cell_text(v) for v in row):
                    skipped += 1
                continue
            memo = " / ".join(f"{_cell_text(header[c])}: {cell(row, c)}" for c in cols.get("memo", []) if cell(row, c))
            rec = {
                "name": name,
                "manager": got.get("manager", ""),
                "phone": _format_phone(got.get("phone", "")),
                "email": got.get("email", ""),
                "address": got.get("address", ""),
                "memo": memo,
            }
            rec = {k: v[:200] for k, v in rec.items()}
            if name in merged:
                old = merged[name]
                for k, v in rec.items():
                    if v and not old.get(k):
                        old[k] = v
            else:
                merged[name] = rec
            count += 1
        sheets_used.append({
            "sheet": title,
            "header_row": head_idx + 1,
            "rows": count,
            "columns": {f: [_cell_text(header[c]) for c in cs] for f, cs in cols.items()},
        })
    if not sheets_used:
        raise ValueError(
            "거래처 목록의 머리글 줄을 찾지 못했습니다. 위쪽 줄에 '상호(또는 거래처명 · 업체명)'와 "
            "'담당자 · 연락처 · 이메일 · 주소' 중 하나 이상의 머리글이 있어야 합니다."
        )
    customers = list(merged.values())
    with _lock:
        existing = {c.get("name") for c in _load()}
    for c in customers:
        c["status"] = "update" if c["name"] in existing else "new"
    return {
        "customers": customers,
        "sheets": sheets_used,
        "skipped": skipped,
        "new": sum(1 for c in customers if c["status"] == "new"),
        "update": sum(1 for c in customers if c["status"] == "update"),
    }


def import_customers(customers: list[dict[str, Any]]) -> dict[str, int]:
    """뽑은 거래처를 상호명 기준으로 한 번에 등록한다. 이미 있는 거래처는 파일에 값이 있는 칸만 바꾸고 빈 칸은 그대로 둔다."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    added = updated = 0
    with _lock:
        items = _load()
        by_name = {c.get("name"): c for c in items}
        for seq, data in enumerate(customers if isinstance(customers, list) else []):
            if not isinstance(data, dict):
                continue
            clean = _clean(data)
            if not clean["name"]:
                continue
            found = by_name.get(clean["name"])
            if found is None:
                found = {"id": f"{stamp}-{seq:04d}", "created_at": now, **{k: "" for k in FIELDS}}
                items.append(found)
                by_name[clean["name"]] = found
                added += 1
            else:
                updated += 1
            found.update({k: v for k, v in clean.items() if v}, updated_at=now)
        _save(items)
    return {"added": added, "updated": updated, "total": len(items)}
