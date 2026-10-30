# -*- coding: utf-8 -*-
"""컴퓨터 작업내역서 관리 및 엑셀 내보내기 모듈"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import uuid
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.app_settings import get_setting, set_setting
from app.quote_info import bank_account_line, load_quote_info

DATA_FILE = Path(__file__).resolve().parent.parent / "task_reports.json"
TEMPLATE_FILE = Path(__file__).resolve().parent.parent / "컴퓨터_작업내역서.xlsx"
# 화면 기본 행 수. Excel 도 최소 이만큼 번호를 매긴다.
MIN_TASK_ROWS = 5

# 서명 그림(캔버스 PNG data URL). 이상한 값이나 너무 큰 값은 버린다.
SIGN_IMG_PREFIX = "data:image/png;base64,"
SIGN_IMG_MAX_CHARS = 600_000
SIGN_IMG_KEYS = ("customer_sign_img", "technician_sign_img")


def clean_sign_image(value: Any) -> str:
    s = str(value or "")
    return s if s.startswith(SIGN_IMG_PREFIX) and len(s) <= SIGN_IMG_MAX_CHARS else ""


def _sign_image_for_excel(data_url: str, max_w: int = 300, max_h: int = 70):
    """서명 PNG 를 Excel 그림으로 만든다. 빈 여백은 잘라 내고 칸 크기에 맞게 줄인다. 실패하면 None."""
    data_url = clean_sign_image(data_url)
    if not data_url:
        return None
    try:
        from openpyxl.drawing.image import Image as XLImage
        from PIL import Image as PILImage

        img = PILImage.open(BytesIO(base64.b64decode(data_url[len(SIGN_IMG_PREFIX):]))).convert("RGBA")
        box = img.getbbox()
        if not box:
            return None
        img = img.crop(box)
        img.thumbnail((max_w, max_h))
        out = BytesIO()
        img.save(out, format="PNG")
        out.seek(0)
        return XLImage(out)
    except Exception:
        return None
BACKUP_DIR_KEY = "task_report_backup_dir"


def load_task_reports() -> list[dict[str, Any]]:
    """저장된 작업내역서 목록을 반환한다."""
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_all_task_reports(reports: list[dict[str, Any]]) -> None:
    """작업내역서 전체 목록을 파일에 저장한다."""
    DATA_FILE.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_task_report(report_id: str) -> dict[str, Any] | None:
    """특정 ID의 작업내역서를 조회한다."""
    reports = load_task_reports()
    for rep in reports:
        if rep.get("id") == report_id:
            return rep
    return None


def create_task_report(data: dict[str, Any]) -> dict[str, Any]:
    """새 작업내역서를 등록한다."""
    reports = load_task_reports()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_id = str(uuid.uuid4())[:8]
    qi = load_quote_info()

    report = {
        "id": report_id,
        "created_at": now_str,
        "updated_at": now_str,
        "company_name": data.get("company_name") or qi.get("supplier_name", "대한민국주식회사"),
        "company_address": data.get("company_address") or qi.get("supplier_address", "서울시"),
        "company_phone": data.get("company_phone") or qi.get("supplier_phone", "010-1234-5678"),
        "business_number": data.get("business_number") or qi.get("supplier_business_number", ""),
        "doc_no": data.get("doc_no") or f"TR-{datetime.now().strftime('%Y%m%d')}-{report_id[:4].upper()}",
        "receipt_date": data.get("receipt_date") or datetime.now().strftime("%Y-%m-%d"),
        
        # 1. 고객 정보
        "customer_name": data.get("customer_name", ""),
        "customer_phone": data.get("customer_phone", ""),
        "customer_address": data.get("customer_address", ""),
        "customer_email": data.get("customer_email", ""),
        "contact_method": data.get("contact_method", "전화"),  # 전화, 문자, 이메일
        
        # 2. 장비 정보
        "device_type": data.get("device_type", "데스크탑"),  # 데스크탑, 노트북, 서버, 기타
        "device_model": data.get("device_model", ""),
        "serial_number": data.get("serial_number", ""),
        "purchase_date": data.get("purchase_date", ""),
        "accessories": data.get("accessories", []),  # ['충전기', '마우스', '케이블', '가방']
        "accessories_custom": data.get("accessories_custom", ""),
        
        # 3. 증상 / 요청 사항
        "symptoms": data.get("symptoms", ""),
        
        # 4. 진단 및 작업 내역
        "tasks": data.get("tasks", [
            {"no": 1, "description": "", "parts": "", "cost": 0},
            {"no": 2, "description": "", "parts": "", "cost": 0},
            {"no": 3, "description": "", "parts": "", "cost": 0},
        ]),
        "total_cost": int(data.get("total_cost", 0)),
        
        # 5. 처리 현황
        "status": data.get("status", "완료"),  # 접수, 진단중, 수리중, 완료, 인도완료
        "expected_date": data.get("expected_date", datetime.now().strftime("%Y-%m-%d")),
        "technician": data.get("technician", "김대건"),
        "warranty_period": data.get("warranty_period", "3개월"),
        
        # 6. 확인 및 서명
        "customer_sign": data.get("customer_sign", ""),
        "technician_sign": data.get("technician_sign", ""),
        "customer_sign_img": clean_sign_image(data.get("customer_sign_img")),
        "technician_sign_img": clean_sign_image(data.get("technician_sign_img")),
    }

    # 계산된 총액
    total = sum(int(t.get("cost", 0) or 0) for t in report["tasks"])
    if total > 0 or not report["total_cost"]:
        report["total_cost"] = total

    reports.insert(0, report)
    save_all_task_reports(reports)
    return report


def update_task_report(report_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """작업내역서를 수정한다."""
    reports = load_task_reports()
    target_idx = -1
    for idx, rep in enumerate(reports):
        if rep.get("id") == report_id:
            target_idx = idx
            break

    if target_idx == -1:
        return None

    existing = reports[target_idx]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    data = dict(data)
    for key in SIGN_IMG_KEYS:
        if key in data:
            data[key] = clean_sign_image(data[key])
    existing.update(data)
    existing["id"] = report_id
    existing["updated_at"] = now_str

    # 총액 갱신
    if "tasks" in existing:
        existing["total_cost"] = sum(int(t.get("cost", 0) or 0) for t in existing["tasks"])

    reports[target_idx] = existing
    save_all_task_reports(reports)
    return existing


def delete_task_report(report_id: str) -> bool:
    """작업내역서를 삭제한다."""
    reports = load_task_reports()
    initial_len = len(reports)
    reports = [rep for rep in reports if rep.get("id") != report_id]
    if len(reports) < initial_len:
        save_all_task_reports(reports)
        return True
    return False


def build_task_report_excel(report: dict[str, Any]) -> BytesIO:
    """작업내역서 엑셀 파일을 생성하여 BytesIO로 반환한다."""
    if TEMPLATE_FILE.exists():
        wb = openpyxl.load_workbook(TEMPLATE_FILE)
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "작업내역서"

    # 1. 헤더 영역
    comp_name = report.get("company_name", "0000 ")
    ws["A1"] = f"{comp_name} 컴퓨터 판매·수리센터"
    ws["A3"] = f"주소 : {report.get('company_address', '')}"
    ws["A4"] = f"전화 : {report.get('company_phone', '')}  |   사업자등록번호: {report.get('business_number', '')}"
    
    ws["E3"] = f"접수번호 : {report.get('doc_no', '')}"
    r_date = report.get("receipt_date", "")
    ws["E4"] = f"접수일자 : {r_date}"

    # 2. 고객 정보
    ws["B7"] = report.get("customer_name", "")
    ws["E7"] = report.get("customer_phone", "")
    ws["B8"] = report.get("customer_address", "")
    ws["B9"] = report.get("customer_email", "")

    contact_m = report.get("contact_method", "전화")
    ws["E9"] = f"{'■' if contact_m == '전화' else '□'} 전화   {'■' if contact_m == '문자' else '□'} 문자   {'■' if contact_m == '이메일' else '□'} 이메일"

    # 3. 장비 정보
    dev_t = report.get("device_type", "데스크탑")
    ws["B11"] = f"{'■' if dev_t == '데스크탑' else '□'} 데스크탑   {'■' if dev_t == '노트북' else '□'} 노트북   {'■' if dev_t == '서버' else '□'} 서버   {'■' if dev_t == '기타' else '□'} 기타"
    ws["E11"] = report.get("device_model", "")
    ws["B12"] = report.get("serial_number", "")
    ws["E12"] = report.get("purchase_date", "")

    acc_list = report.get("accessories", [])
    acc_str = (
        f"{'■' if '충전기' in acc_list else '□'} 충전기   "
        f"{'■' if '마우스' in acc_list else '□'} 마우스   "
        f"{'■' if '케이블' in acc_list else '□'} 케이블   "
        f"{'■' if '가방' in acc_list else '□'} 가방   "
        f"{'■' if report.get('accessories_custom') else '□'} 기타 : {report.get('accessories_custom', '')}"
    )
    ws["B13"] = acc_str

    # 4. 증상 / 요청 사항
    ws["A15"] = report.get("symptoms", "")

    # 5. 진단 및 작업 내역: 20행부터 행 수만큼(최소 MIN_TASK_ROWS). 뒤 항목은 행 수에 따라 밀린다.
    tasks = report.get("tasks", [])
    n_rows = max(MIN_TASK_ROWS, len(tasks))
    for idx in range(n_rows):
        row_num = 20 + idx
        ws[f"A{row_num}"] = idx + 1
        if idx < len(tasks):
            t = tasks[idx]
            ws[f"B{row_num}"] = t.get("description", "")
            ws[f"D{row_num}"] = t.get("parts", "")
            cost_val = int(t.get("cost", 0) or 0)
            ws[f"F{row_num}"] = cost_val if cost_val > 0 else ""
            if cost_val > 0:
                ws[f"F{row_num}"].number_format = "#,##0"
        else:
            ws[f"B{row_num}"] = ""
            ws[f"D{row_num}"] = ""
            ws[f"F{row_num}"] = ""

    total_row = 20 + n_rows  # 행이 3개였을 때의 23행과 같은 간격
    ws[f"F{total_row}"] = int(report.get("total_cost", 0) or 0)
    ws[f"F{total_row}"].number_format = "#,##0"

    # 6. 처리 현황
    status_val = report.get("status", "완료")
    status_str = (
        f"{'■' if status_val == '접수' else '□'} 접수   "
        f"{'■' if status_val == '진단중' else '□'} 진단중   "
        f"{'■' if status_val == '수리중' else '□'} 수리중   "
        f"{'■' if status_val == '완료' else '□'} 완료   "
        f"{'■' if status_val == '인도완료' else '□'} 인도완료"
    )
    ws[f"B{total_row + 2}"] = status_str
    ws[f"E{total_row + 2}"] = report.get("expected_date", "")
    ws[f"B{total_row + 3}"] = report.get("technician", "김대건")
    ws[f"E{total_row + 3}"] = report.get("warranty_period", "3개월")

    # 7. 확인 및 서명: 그린 서명은 그림으로, 예전 글자 서명은 글자로 넣는다
    sign_row = total_row + 6
    for col, label, text_key, img_key in (
        ("A", "고객 서명 (또는 인)", "customer_sign", "customer_sign_img"),
        ("D", "담당자 서명 (또는 인)", "technician_sign", "technician_sign_img"),
    ):
        sign_img = _sign_image_for_excel(report.get(img_key, ""))
        if sign_img is not None:
            if not ws[f"{col}{sign_row - 1}"].value:
                ws[f"{col}{sign_row - 1}"] = label
                ws[f"{col}{sign_row - 1}"].font = Font(bold=True)
            ws.add_image(sign_img, f"{col}{sign_row}")
            # 그림 높이(px) → 행 높이(pt) 로 맞춰 아래 내용과 겹치지 않게
            need_pt = sign_img.height * 0.75 + 4
            if (ws.row_dimensions[sign_row].height or 15) < need_pt:
                ws.row_dimensions[sign_row].height = need_pt
        elif report.get(text_key):
            ws[f"{col}{sign_row}"] = f"서명: {report.get(text_key)}"

    # 8. 입금 계좌 (견적 정보에서 가져옴, 계좌번호가 없으면 생략)
    bank_line = bank_account_line(load_quote_info())
    if bank_line:
        ws[f"A{total_row + 8}"] = bank_line
        ws[f"A{total_row + 8}"].font = Font(bold=True, color="1F4E79")

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# 백업: 지정한 폴더에 내역서 한 건을 Excel + JSON 으로 모아 둔다
# ---------------------------------------------------------------------------
_FILENAME_BAD_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_filename_part(text: str, limit: int = 60) -> str:
    """파일 이름에 못 쓰는 문자를 공백으로 바꾸고 길이를 자른다."""
    cleaned = _FILENAME_BAD_RE.sub(" ", str(text or ""))
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned[:limit]


def get_backup_dir() -> str:
    return str(get_setting(BACKUP_DIR_KEY, "") or "")


def set_backup_dir(path: str) -> str:
    """폴더를 확인(없으면 생성)하고 설정에 저장한다. 빈 값이면 설정을 지운다."""
    path = (path or "").strip().strip('"')
    if not path:
        set_setting(BACKUP_DIR_KEY, "")
        return ""
    folder = Path(path).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    if not folder.is_dir():
        raise ValueError(f"폴더가 아닙니다: {folder}")
    resolved = str(folder.resolve())
    set_setting(BACKUP_DIR_KEY, resolved)
    return resolved


def choose_backup_dir_dialog(initial: str = "", timeout_sec: int = 600) -> str:
    """서버 PC 화면에 폴더 선택 창을 띄운다. 취소하면 빈 문자열."""
    initial_ps = (initial or "").replace("'", "''")
    script = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'
$owner.Show()
$owner.Activate()
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = '작업내역서 백업 폴더를 고르세요'
$d.ShowNewFolderButton = $true
if ('{initial_ps}' -ne '') {{ $d.SelectedPath = '{initial_ps}' }}
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {{ Write-Output $d.SelectedPath }}
$owner.Close()
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-EncodedCommand", encoded],
            capture_output=True,
            timeout=timeout_sec,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = completed.stdout.decode("utf-8", "replace").strip()
    return out.splitlines()[-1].strip() if out else ""


def _report_stem(report: dict[str, Any]) -> str:
    cust = safe_filename_part(report.get("customer_name") or "고객")
    doc_no = safe_filename_part(report.get("doc_no") or report.get("id") or "")
    return f"작업내역서_{cust}_{doc_no}" if doc_no else f"작업내역서_{cust}"


def backup_all_task_reports(folder: str) -> dict[str, Any]:
    """저장된 작업내역서 전체를 folder 에 ZIP 한 파일로 모은다.

    ZIP 안: task_reports.json(원본 DB 그대로, 복구용) + 내역서마다 Excel 한 장.
    파일 이름은 작업내역서_백업_YYYYMMDD_HHMMSS.zip 이라 백업할 때마다 새 파일이 쌓인다.
    """
    target = Path(folder)
    if not target.is_dir():
        raise FileNotFoundError(f"백업 폴더를 찾을 수 없습니다: {folder}")
    reports = load_task_reports()
    stamp = datetime.now()
    stem = f"작업내역서_백업_{stamp.strftime('%Y%m%d_%H%M%S')}"
    zip_path = target / f"{stem}.zip"
    seq = 2
    while zip_path.exists():  # 같은 초에 두 번 눌러도 앞 파일을 덮어쓰지 않는다
        zip_path = target / f"{stem}_{seq}.zip"
        seq += 1
    used: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "task_reports.json",
            json.dumps(reports, ensure_ascii=False, indent=2),
        )
        for rep in reports:
            stem = _report_stem(rep)
            name = stem
            if name in used:  # 같은 고객·접수번호가 둘이면 id 로 구분
                name = f"{stem}_{rep.get('id', '')}"
            used.add(name)
            zf.writestr(f"excel/{name}.xlsx", build_task_report_excel(rep).getvalue())
        zf.writestr(
            "README.txt",
            "컴퓨존 견적 자동화 - 작업내역서 백업\n"
            f"백업 시각: {stamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"내역서 수: {len(reports)}건\n\n"
            "task_reports.json : 프로그램이 저장한 원본 데이터(복구할 때 프로그램 폴더의 같은 이름 파일에 덮어쓰기)\n"
            "excel/            : 내역서마다 Excel 한 장\n",
        )
    return {
        "dir": str(target),
        "file": zip_path.name,
        "path": str(zip_path),
        "count": len(reports),
        "size": zip_path.stat().st_size,
    }


def open_in_explorer(path: str) -> bool:
    """서버 PC 화면에서 탐색기를 연다. 파일이면 그 파일을 선택한 상태로, 폴더면 폴더를 연다."""
    p = Path(path)
    if p.is_file():
        # 리스트로 넘기면 "/select,경로" 전체가 따옴표로 묶여 탐색기가 무시한다 → 문자열로 넘긴다.
        subprocess.Popen(f'explorer /select,"{p}"')
        return True
    if p.is_dir():
        os.startfile(str(p))  # type: ignore[attr-defined]
        return True
    return False
