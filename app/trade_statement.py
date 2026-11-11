# -*- coding: utf-8 -*-
"""🧾 거래명세서관리: 거래명세표 작성 · 저장 · Excel/PDF · 서명본 보관.

- 서식은 견적서 폼5(리테일)를 그대로 옮겨 제목만 "거 래 명 세 표"로 바꾼 것이다. 같은 내용을 A4 한 장에
  위(공급자 보관용) · 아래(공급받는자 보관용) 두 벌로 찍고 사이에 절취선을 둔다. 납품할 때 인수자 서명을
  받아 한 벌은 구매자에게 주고 한 벌은 보관한다. 견적 전용 문구(견적유효기간)는 뺐다.
- 폼1~8 은 build_excel() 한 함수 안에 순서대로 있고 1~8 범위가 여러 곳(_FORM_NO_RE, 갤러리, app.js)에
  박혀 있어 폼9 로 끼우지 않고 이 모듈의 build_statement_excel() 로 따로 만든다. 폼5 모양을 고치면 여기도 맞춘다.
- 저장은 trade_statements.json, 서명받은 명세서 스캔/사진은 trade_statements/ 폴더(둘 다 .gitignore).
- 품목 정리 · 견적처 칸은 manual_quote 와 같은 규칙(부가세 포함 단가, 마진 · 만원 올림 없음)을 쓴다.
"""
from __future__ import annotations

import json
import threading
import uuid
import zipfile
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side

from app.excel_export import FONT_NAME, FORM5_COL_WIDTHS, _apply_page_setup, _set_cell
from app.manual_quote import CUSTOMER_FIELDS, clean_customer
from app.quote_info import load_quote_info

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "trade_statements.json"
SIGNED_DIR = ROOT / "trade_statements"
SHEET_TITLE = "거래명세표"
DOC_TITLE = "거 래 명 세 표"
COPY_LABELS = ("(공급자 보관용)", "(공급받는자 보관용)")
# 두 벌이 A4 세로 한 장에 들어가야 하므로 품목 행은 견적서(20행)보다 적게 둔다.
# 품목 표는 늘 MIN_ROWS 행(빈 행 포함)이라 품목 수와 상관없이 같은 모양이고, MAX_ITEMS 까지는 늘어나며 한 장에 맞춰 줄어든다.
# MAX_ITEMS 는 static/js/app.js 의 TS_MAX_ROWS 와 같아야 한다(양쪽 주석에 명시).
MIN_ROWS = 10
MAX_ITEMS = 20  # 새 견적 만들기(manual_quote.MAX_ITEMS)와 같아 작성본을 잘림 없이 가져온다
SIGNED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_SIGNED_BYTES = 20 * 1024 * 1024

_lock = threading.Lock()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).replace(",", "").strip() or default)
    except (TypeError, ValueError):
        return default


def clean_items(items: Any) -> list[dict[str, Any]]:
    """품목 행 정리. 새 견적 만들기와 같되, 견적 카드에서 가져온 할인 행을 위해 음수 단가를 허용한다."""
    rows: list[dict[str, Any]] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        rows.append({
            "category": str(it.get("category") or "").strip()[:40],
            "name": name[:200],
            "qty": max(1, _int(it.get("qty"), 1)),
            "unit_price": _int(it.get("unit_price"), 0),
        })
    return rows[:MAX_ITEMS]


def clean_record(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_no": str(data.get("doc_no") or "").strip()[:40],
        "title": str(data.get("title") or "").strip()[:200],
        "issue_date": str(data.get("issue_date") or "").strip()[:10] or date.today().isoformat(),
        "customer_info": clean_customer(data.get("customer_info")),
        "items": clean_items(data.get("items")),
        "note": str(data.get("note") or "").strip()[:500],
        "signed": bool(data.get("signed")),
        "signer": str(data.get("signer") or "").strip()[:60],
        "signed_date": str(data.get("signed_date") or "").strip()[:10],
        # 어디서 만들었는지(카드 · 작성본) 목록 표시용. 비어도 된다.
        "source": str(data.get("source") or "").strip()[:200],
    }


def total_of(items: list[dict[str, Any]]) -> int:
    return sum(it["qty"] * it["unit_price"] for it in items)


def _customer_info(rec: dict[str, Any]) -> dict[str, str]:
    """명세서에 찍을 공급받는자. 칸이 모두 비면 견적정보(📒 거래처관리)의 견적처를 쓴다."""
    cust = clean_customer(rec.get("customer_info"))
    if any(cust.values()):
        return cust
    info = load_quote_info()
    return {k: (info.get(f"customer_{k}") or "").strip() for k in CUSTOMER_FIELDS}


# ---------------------------------------------------------------------------
# 저장 (trade_statements.json)
# ---------------------------------------------------------------------------
def load_statements() -> list[dict[str, Any]]:
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_all(records: list[dict[str, Any]]) -> None:
    DATA_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def get_statement(stmt_id: str) -> dict[str, Any] | None:
    return next((r for r in load_statements() if r.get("id") == stmt_id), None)


def _next_doc_no(records: list[dict[str, Any]], issue_date: str) -> str:
    """거래명세표 번호: TS-YYYYMMDD-NN (발행일 기준 그날의 순번)."""
    day = issue_date.replace("-", "")[:8] or date.today().strftime("%Y%m%d")
    prefix = f"TS-{day}-"
    used = [r.get("doc_no", "") for r in records if str(r.get("doc_no", "")).startswith(prefix)]
    seq = max([_int(n[len(prefix):]) for n in used] + [0]) + 1
    return f"{prefix}{seq:02d}"


def save_statement(data: dict[str, Any]) -> dict[str, Any]:
    """id 가 있으면 고치고, 없거나 못 찾으면 새로 만든다. 서명본 파일 정보(signed_file)는 업로드 API 만 바꾼다."""
    with _lock:
        records = load_statements()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rec = clean_record(data)
        if not rec["items"]:
            raise ValueError("품목을 한 줄 이상 입력하세요(품명이 빈 줄은 빠집니다).")
        rec["total"] = total_of(rec["items"])
        rec["customer"] = _customer_info(rec)["name"]
        if rec["signed"] and not rec["signed_date"]:
            rec["signed_date"] = date.today().isoformat()
        rec["updated_at"] = now
        stmt_id = str(data.get("id") or "")
        for idx, old in enumerate(records):
            if stmt_id and old.get("id") == stmt_id:
                rec["doc_no"] = rec["doc_no"] or old.get("doc_no") or _next_doc_no(records, rec["issue_date"])
                records[idx] = {**old, **rec, "id": stmt_id}
                _save_all(records)
                return records[idx]
        rec["doc_no"] = rec["doc_no"] or _next_doc_no(records, rec["issue_date"])
        rec = {"id": uuid.uuid4().hex[:12], "created_at": now, "signed_file": "", **rec}
        records.insert(0, rec)
        _save_all(records)
        return rec


def delete_statement(stmt_id: str) -> bool:
    with _lock:
        records = load_statements()
        target = next((r for r in records if r.get("id") == stmt_id), None)
        if not target:
            return False
        _remove_signed_file(target)
        _save_all([r for r in records if r.get("id") != stmt_id])
        return True


# ---------------------------------------------------------------------------
# 서명받은 명세서(스캔 · 사진) 보관 (trade_statements/)
# ---------------------------------------------------------------------------
def signed_path(rec: dict[str, Any]) -> Path | None:
    name = str(rec.get("signed_file") or "")
    if not name or "/" in name or "\\" in name:
        return None
    path = SIGNED_DIR / name
    return path if path.is_file() else None


def _remove_signed_file(rec: dict[str, Any]) -> None:
    path = signed_path(rec)
    if path:
        try:
            path.unlink()
        except OSError:
            pass


def attach_signed(stmt_id: str, filename: str, content: bytes) -> dict[str, Any]:
    """서명본 파일을 붙이고 서명 완료로 표시한다. 예전 파일은 지운다."""
    ext = Path(filename or "").suffix.lower()
    if ext not in SIGNED_EXTS:
        raise ValueError("PDF 또는 그림 파일(png · jpg · webp · gif · bmp)만 올릴 수 있습니다.")
    if not content:
        raise ValueError("빈 파일입니다.")
    if len(content) > MAX_SIGNED_BYTES:
        raise ValueError("파일이 너무 큽니다(최대 20MB).")
    with _lock:
        records = load_statements()
        for idx, rec in enumerate(records):
            if rec.get("id") != stmt_id:
                continue
            _remove_signed_file(rec)
            SIGNED_DIR.mkdir(parents=True, exist_ok=True)
            name = f"{stmt_id}_signed{ext}"
            (SIGNED_DIR / name).write_bytes(content)
            rec.update({
                "signed_file": name,
                "signed_file_orig": Path(filename).name[:120],
                "signed": True,
                "signed_date": rec.get("signed_date") or date.today().isoformat(),
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            records[idx] = rec
            _save_all(records)
            return rec
    raise KeyError(stmt_id)


def detach_signed(stmt_id: str) -> dict[str, Any] | None:
    """서명본 파일만 지운다(서명 완료 표시는 그대로 둔다)."""
    with _lock:
        records = load_statements()
        for idx, rec in enumerate(records):
            if rec.get("id") == stmt_id:
                _remove_signed_file(rec)
                rec["signed_file"] = ""
                rec.pop("signed_file_orig", None)
                records[idx] = rec
                _save_all(records)
                return rec
    return None


def backup_statements(folder: str) -> dict[str, Any]:
    """명세서 목록(JSON) + 서명본 파일을 folder 에 ZIP 한 파일로 저장한다."""
    target = Path(folder)
    if not target.is_dir():
        raise FileNotFoundError(f"백업 폴더를 찾을 수 없습니다: {folder}")
    with _lock:
        records = load_statements()
        stamp = datetime.now()
        stem = f"거래명세서_백업_{stamp.strftime('%Y%m%d_%H%M%S')}"
        zip_path = target / f"{stem}.zip"
        seq = 2
        while zip_path.exists():
            zip_path = target / f"{stem}_{seq}.zip"
            seq += 1
        files = 0
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("trade_statements.json", json.dumps(records, ensure_ascii=False, indent=2))
            for rec in records:
                path = signed_path(rec)
                if path:
                    zf.write(path, f"signed/{path.name}")
                    files += 1
            zf.writestr(
                "README.txt",
                "컴퓨존 견적 자동화 - 거래명세서 백업\n"
                f"백업 시각: {stamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"거래명세서: {len(records)}건 / 서명본 파일: {files}개\n\n"
                "trade_statements.json : 명세서 목록(복구할 때 프로그램 폴더에 덮어쓰기)\n"
                "signed/               : 서명받은 명세서 파일(복구할 때 trade_statements/ 폴더에 복사)\n",
            )
    return {"path": str(zip_path), "count": len(records), "files": files}


# ---------------------------------------------------------------------------
# Excel (폼5 리테일 모양 × 위아래 두 벌)
# ---------------------------------------------------------------------------
def build_statement_excel(data: dict[str, Any]) -> BytesIO:
    """거래명세표 한 장(A4 세로) Excel. 같은 내용을 위 · 아래 두 벌로 찍는다. 품목이 없으면 ValueError."""
    rec = clean_record(data)
    if not rec["items"]:
        raise ValueError("품목을 한 줄 이상 입력하세요(품명이 빈 줄은 빠집니다).")
    info = load_quote_info()
    cust = _customer_info(rec)
    doc_no = str(data.get("doc_no") or rec["doc_no"] or "").strip()

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_TITLE
    _apply_page_setup(ws)
    ws.page_margins.top = ws.page_margins.bottom = 10.0 / 25.4
    for letter, width in FORM5_COL_WIDTHS.items():
        ws.column_dimensions[letter].width = width

    # 절취선을 A4 세로 정중앙(50%)에 둔다. 위 · 아래 두 벌은 모양이 같으므로, 절취선 위아래 빈 줄을 같은
    # 높이로 두고 남는 높이를 그 두 줄에 나눠 쪽을 채운 뒤 세로 가운데 맞춤을 건다. 품목이 많아 Excel 이
    # 한 장에 맞추려고 축소해도 가운데 맞춤이라 절취선은 계속 쪽 한가운데에 온다.
    ws.print_options.verticalCentered = True
    r = 1
    for i, label in enumerate(COPY_LABELS):
        r = _write_copy(ws, r, rec, cust, info, doc_no, label)
        if i == 0:
            copy_h = sum(ws.row_dimensions[x].height or 15.0 for x in range(1, r + 1))
            gap = max(CUT_GAP_MIN_PT, (PRINTABLE_HEIGHT_PT - 2 * copy_h - CUT_LINE_PT) / 2)
            ws.row_dimensions[r + 1].height = round(gap, 1)
            r = _write_cut_line(ws, r + 2) + 1
            ws.row_dimensions[r].height = round(gap, 1)
            r += 1
    ws.print_area = f"A1:F{r}"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# 절취선 위치 계산용 (포인트). A4 297mm 에서 위아래 여백 10mm 씩을 뺀 인쇄 높이 277mm, 렌더링 오차 여유 6pt.
PRINTABLE_HEIGHT_PT = 277.0 / 25.4 * 72 - 6.0
CUT_LINE_PT = 16.0
CUT_GAP_MIN_PT = 6.0


def _write_cut_line(ws, r: int) -> int:
    ws.merge_cells(f"A{r}:F{r}")
    ws[f"A{r}"] = "✂ - - - - - - - - - - - - - - - -  절  취  선  - - - - - - - - - - - - - - - - ✂"
    _set_cell(ws[f"A{r}"], font=Font(name=FONT_NAME, size=9, color="7F8C8D"),
              alignment=Alignment(horizontal="center", vertical="center"))
    ws.row_dimensions[r].height = CUT_LINE_PT
    return r


def _write_copy(ws, r0: int, rec: dict[str, Any], cust: dict[str, str], info: dict[str, Any],
                doc_no: str, copy_label: str) -> int:
    """폼5(리테일) 모양 한 벌을 r0 행부터 그린다. 마지막으로 쓴 행 번호를 돌려준다."""
    normal = Font(name=FONT_NAME, size=10)
    bold = Font(name=FONT_NAME, size=10, bold=True)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right = Alignment(horizontal="right", vertical="center", shrink_to_fit=True)
    fit = Alignment(horizontal="left", vertical="center", shrink_to_fit=True, indent=1)

    # 제목 (폼5: A1:F2, 22pt) — 두 벌이 한 장에 들어가도록 조금 줄였다
    ws[f"A{r0}"] = DOC_TITLE
    ws.merge_cells(f"A{r0}:F{r0 + 1}")
    _set_cell(ws[f"A{r0}"], font=Font(name=FONT_NAME, size=20, bold=True, color="34495E"), alignment=center)
    ws.row_dimensions[r0].height = 17.0
    ws.row_dimensions[r0 + 1].height = 17.0

    r = r0 + 2
    ws[f"A{r}"] = f"No. {doc_no}" if doc_no else ""
    ws.merge_cells(f"A{r}:C{r}")
    _set_cell(ws[f"A{r}"], font=Font(name=FONT_NAME, size=9, color="7F8C8D"), alignment=fit)
    ws[f"D{r}"] = copy_label
    ws.merge_cells(f"D{r}:F{r}")
    _set_cell(ws[f"D{r}"], font=Font(name=FONT_NAME, size=9, bold=True, color="C0392B"), alignment=right)
    ws.row_dimensions[r].height = 15.0

    r += 1
    cust_line = " / ".join(x for x in (cust["manager"], cust["phone"], cust["email"]) if x)
    ws[f"A{r}"] = f"공급받는자: {cust['name']}" + (f" ({cust_line})" if cust_line else "") + " 귀하"
    ws.merge_cells(f"A{r}:C{r}")
    _set_cell(ws[f"A{r}"], font=bold, alignment=fit)
    ws[f"E{r}"] = f"발행일: {rec['issue_date']}"
    ws.merge_cells(f"E{r}:F{r}")
    _set_cell(ws[f"E{r}"], font=normal, alignment=right)
    ws.row_dimensions[r].height = 18.0

    # 폼5 의 견적유효기간 줄 자리: 납품 문구
    r += 1
    ws.merge_cells(f"A{r}:F{r}")
    note = f" — {rec['title']}" if rec["title"] else ""
    ws[f"A{r}"] = f"▶ 아래와 같이 납품(공급)합니다.{note}"
    _set_cell(ws[f"A{r}"], font=Font(name=FONT_NAME, size=10, bold=True, color="000080"), alignment=fit)
    ws.row_dimensions[r].height = 18.0

    # 표 머리 (폼5 와 같은 굵은 밑줄, 머리글은 한글)
    r += 1
    thick_bot = Border(bottom=Side(style="thick", color="34495E"))
    ws[f"A{r}"] = "품    명"
    ws.merge_cells(f"A{r}:C{r}")
    ws[f"D{r}"] = "수량"
    ws.merge_cells(f"D{r}:E{r}")
    ws[f"F{r}"] = "금액"
    for c in range(1, 7):
        ws.cell(row=r, column=c).border = thick_bot
    _set_cell(ws[f"A{r}"], font=bold, alignment=left)
    _set_cell(ws[f"D{r}"], font=bold, alignment=center)
    _set_cell(ws[f"F{r}"], font=bold, alignment=right)
    ws.row_dimensions[r].height = 18.0

    thin_bot = Border(bottom=Side(style="hair", color="CCCCCC"))
    items = rec["items"]
    for idx in range(max(MIN_ROWS, len(items))):
        r += 1
        ws.merge_cells(f"A{r}:C{r}")
        ws.merge_cells(f"D{r}:E{r}")
        if idx < len(items):
            it = items[idx]
            ws[f"A{r}"] = f"[{it['category']}] {it['name']}" if it["category"] else it["name"]
            ws[f"D{r}"] = it["qty"]
            ws[f"F{r}"] = it["qty"] * it["unit_price"]
        for c in range(1, 7):
            ws.cell(row=r, column=c).border = thin_bot
        _set_cell(ws[f"A{r}"], font=normal, alignment=Alignment(horizontal="left", vertical="center", shrink_to_fit=True))
        _set_cell(ws[f"D{r}"], font=normal, alignment=center)
        _set_cell(ws[f"F{r}"], font=normal, alignment=right, number_format="#,##0")
        ws.row_dimensions[r].height = 17.0

    r += 2
    ws.row_dimensions[r - 1].height = 6.0
    ws.merge_cells(f"D{r}:E{r}")
    ws[f"D{r}"] = "합  계"
    ws[f"F{r}"] = total_of(items)
    _set_cell(ws[f"D{r}"], font=Font(name=FONT_NAME, size=13, bold=True), alignment=right)
    _set_cell(ws[f"F{r}"], font=Font(name=FONT_NAME, size=13, bold=True, color="000080"),
              alignment=right, number_format="#,##0")
    ws[f"A{r}"] = "(VAT 포함)"
    ws.merge_cells(f"A{r}:C{r}")
    _set_cell(ws[f"A{r}"], font=Font(name=FONT_NAME, size=9, color="7F8C8D"), alignment=fit)
    ws.row_dimensions[r].height = 22.0

    # 공급자 한 줄 (폼5 와 같음)
    r += 1
    sup_email = (info.get("supplier_email") or "").strip()
    ws.merge_cells(f"A{r}:F{r}")
    ws[f"A{r}"] = (
        f"공급자: {info.get('supplier_name', '')} / 담당: {info.get('supplier_manager', '')} / "
        f"Tel: {info.get('supplier_phone', '')}" + (f" / E-mail: {sup_email}" if sup_email else "")
    )
    _set_cell(ws[f"A{r}"], font=normal, alignment=Alignment(horizontal="center", vertical="center", shrink_to_fit=True))
    ws.row_dimensions[r].height = 18.0

    # 인수 확인 · 서명
    r += 1
    box = Side(style="thin", color="34495E")
    ws.merge_cells(f"A{r}:F{r}")
    signed_date = rec["signed_date"].replace("-", ". ") if rec["signed_date"] else "20      .      .      "
    signer = rec["signer"] or " " * 24
    ws[f"A{r}"] = f"위 물품을 정히 인수합니다.     인수일: {signed_date}     인수자: {signer}  (서명 / 인)"
    _set_cell(ws[f"A{r}"], font=bold, alignment=Alignment(horizontal="center", vertical="center", shrink_to_fit=True))
    for c in range(1, 7):
        ws.cell(row=r, column=c).border = Border(
            top=box, bottom=box, left=box if c == 1 else None, right=box if c == 6 else None,
        )
    ws.row_dimensions[r].height = 30.0
    if rec["note"]:
        r += 1
        ws.merge_cells(f"A{r}:F{r}")
        ws[f"A{r}"] = f"비고: {rec['note']}"
        _set_cell(ws[f"A{r}"], font=Font(name=FONT_NAME, size=9), alignment=fit)
        ws.row_dimensions[r].height = 15.0
    return r


def sample_statement() -> dict[str, Any]:
    """미리보기 · 점검용 샘플(실제 거래 아님)."""
    from app.excel_export import SAMPLE_PARTS

    return {
        "doc_no": "TS-20260101-01",
        "title": "샘플 게이밍 PC 납품",
        "issue_date": "2026-01-01",
        "customer_info": {},
        "items": [{"category": c, "name": n, "qty": q, "unit_price": p} for c, n, q, p in SAMPLE_PARTS],
    }
