# -*- coding: utf-8 -*-
"""견적서 Excel → PDF 변환 (이 PC 의 Excel, COM).

카드 서식 미리보기 창이 실제 견적서를 PDF 로 보여 주고(목록 썸네일도 그 PDF 를 그림으로 바꾼 것),
견적 메일에 PDF 를 첨부할 때 쓴다.
PowerShell 로 Excel 을 숨긴 채 띄워 ExportAsFixedFormat 으로 내보낸다(폼 시트는 A4 세로 1페이지 강제).
Excel 이 없는 PC 에서는 RuntimeError 를 내고, 화면은 Excel 다운로드만 쓰게 된다.
"""
from __future__ import annotations

import base64
import io
import subprocess
import tempfile
import threading
import time
import winreg
from pathlib import Path

import pypdfium2 as pdfium

CONVERT_TIMEOUT_SEC = 90
CACHE_TTL_SEC = 10 * 60  # 같은 카드 · 같은 서식을 다시 열면 Excel 을 다시 띄우지 않는다
CACHE_MAX = 60  # 카드 한 건이 썸네일 묶음 1 + 서식별 PDF 8 = 9칸을 쓴다

# Excel 인스턴스를 동시에 여러 개 띄우지 않는다
_convert_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, bytes]] = {}

PS_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$xlsx = '{XLSX}'
$pdf = '{PDF}'
$app = New-Object -ComObject Excel.Application
$app.Visible = $false
$app.DisplayAlerts = $false
try {
  $wb = $app.Workbooks.Open($xlsx, 0, $true)
  # xlTypePDF=0, xlQualityStandard=0, IncludeDocProperties, IgnorePrintAreas=false
  $wb.ExportAsFixedFormat(0, $pdf, 0, $true, $false)
  $wb.Close($false)
} finally {
  $app.Quit()
  [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($app)
}
"""


def excel_available() -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application\CLSID"))
        return True
    except OSError:
        return False


def xlsx_to_pdf(xlsx_bytes: bytes) -> bytes:
    if not excel_available():
        raise RuntimeError("이 PC 에 Microsoft Excel 이 없어 PDF 를 만들 수 없습니다.")
    # 시간 초과로 남은 EXCEL.EXE 가 파일을 잡고 있어도 정리 오류가 원래 오류를 가리지 않게 한다
    with _convert_lock, tempfile.TemporaryDirectory(prefix="quote-pdf-", ignore_cleanup_errors=True) as tmp:
        xlsx = Path(tmp) / "quote.xlsx"
        pdf = Path(tmp) / "quote.pdf"
        xlsx.write_bytes(xlsx_bytes)
        script = PS_SCRIPT.replace("{XLSX}", str(xlsx)).replace("{PDF}", str(pdf))
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded],
                capture_output=True,
                timeout=CONVERT_TIMEOUT_SEC,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("Excel 이 응답하지 않아 PDF 변환을 중단했습니다.") from e
        if not pdf.is_file() or pdf.stat().st_size == 0:
            err = completed.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError("PDF 변환에 실패했습니다." + (f" ({err[0][:200]})" if err else ""))
        return pdf.read_bytes()


def cache_get(key: str) -> bytes | None:
    now = time.time()
    with _cache_lock:
        for k in [k for k, (at, _) in _cache.items() if now - at > CACHE_TTL_SEC]:
            _cache.pop(k, None)
        hit = _cache.get(key)
    return hit[1] if hit else None


def cache_put(key: str, data: bytes) -> None:
    with _cache_lock:
        if key not in _cache and len(_cache) >= CACHE_MAX:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]), None)
        _cache[key] = (time.time(), data)


def cached_pdf(key: str, make_xlsx) -> bytes:
    """key(요청 조건)별로 PDF 를 잠시 보관한다. openpyxl 은 만들 때마다 바이트가 달라 xlsx 해시로는 못 쓴다."""
    data = cache_get(key)
    if data is None:
        data = xlsx_to_pdf(make_xlsx())
        cache_put(key, data)
    return data


def split_pages(pdf_bytes: bytes) -> list[bytes]:
    """여러 쪽 PDF → 쪽마다 PDF 하나. 폼 시트는 1쪽 강제라 '폼1~8 한 파일' 이 서식별 PDF 8개가 된다."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        pages = []
        for i in range(len(doc)):
            one = pdfium.PdfDocument.new()
            one.import_pages(doc, [i])
            buf = io.BytesIO()
            one.save(buf)
            one.close()
            pages.append(buf.getvalue())
        return pages
    finally:
        doc.close()


def render_png(pdf_bytes: bytes, width: int) -> bytes:
    """PDF 첫 쪽을 폭 width(px) PNG 로."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page = doc[0]
        img = page.render(scale=width / page.get_width()).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    finally:
        doc.close()
