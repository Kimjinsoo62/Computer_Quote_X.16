# -*- coding: utf-8 -*-
"""폼1~8 견적서 서식 썸네일(static/img/forms/formN.png)과 크게 보기 그림(formN_large.png)을 다시 만든다.

서식 디자인(excel_export.py)을 고쳤을 때만 실행한다. 실행 PC에 Excel 이 있어야 한다.
샘플 견적 한 건으로 전체 Excel 을 만든 뒤 Excel(COM)로 각 폼 시트를 그림으로 내보낸다.
목록용은 폭 480px, 미리보기 창에서 한 장을 크게 볼 때 쓰는 _large 는 글자를 읽을 수 있게 폭 1200px.
견적처·공급자 정보는 quote_info.json 이 아니라 기본값(가짜 데이터)을 쓴다 → 저장소에 올라가는 썸네일에 실제 고객 정보가 들어가지 않는다.

    .venv\\Scripts\\python.exe tools\\make_form_thumbnails.py
"""
from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app.excel_export import FORM_LABELS, build_excel  # noqa: E402
from app.quote_engine import Quote, QuotePart, TodayQuotesResult  # noqa: E402
from app.quote_info import DEFAULT_INFO  # noqa: E402

OUT_DIR = ROOT / "static" / "img" / "forms"
THUMB_WIDTH = 480
LARGE_WIDTH = 1200
# 시트 그림(메타파일 = 벡터)을 이 배율로 키운 차트에 붙여 내보낸다 → _large 도 글자가 또렷하다
EXPORT_SCALE = 2.5

SAMPLE_PARTS = [
    ("CPU", "AMD 라이젠7 9800X3D (그래니트 릿지)", 1, 650000),
    ("메인보드", "ASUS TUF Gaming B650M-PLUS", 1, 230000),
    ("메모리", "DDR5 32GB (16GB×2) 5600MHz", 1, 150000),
    ("그래픽카드", "RTX 5070 SUPER 12GB", 1, 980000),
    ("SSD", "NVMe M.2 1TB", 1, 130000),
    ("케이스", "미들타워 강화유리 케이스", 1, 80000),
    ("파워", "850W 80PLUS GOLD 풀모듈러", 1, 140000),
    ("쿨러/특수냉각", "360mm 수랭 쿨러", 1, 120000),
    ("조립비", "조립 · 케이블 정리 · 테스트", 1, 30000),
    ("모니터", "LG PC 모니터 27U411A 68cm(27인치)", 1, 180000),
    ("키보드마우스", "무선키보드마우스", 1, 30000),
]


def sample_result() -> TodayQuotesResult:
    parts = [
        QuotePart(no=i, category=c, name=n, qty=q, unit_price=p, amount=p * q)
        for i, (c, n, q, p) in enumerate(SAMPLE_PARTS, 1)
    ]
    total = sum(p.amount for p in parts)
    quote = Quote(
        tier="고급형",
        tier_key="sample",
        model="샘플 게이밍 PC (9800X3D / RTX 5070 SUPER)",
        tab_label="고급형",
        description="서식 미리보기용 샘플 견적",
        product_no=0,
        # 공급자 상호에 걸리는 제품 페이지 링크(파란 밑줄)가 썸네일에도 보이도록 샘플 URL 을 넣는다
        url="https://www.compuzone.co.kr/",
        quote_date="2026-01-01",
        parts=parts,
        parts_subtotal=total,
        total=total,
    )
    return TodayQuotesResult(
        quote_date="2026-01-01",
        event_url="",
        event_title="서식 미리보기",
        series="샘플",
        quotes=[quote],
        logic_steps=[],
    )


PS_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$xlsx = '{XLSX}'
$out = '{OUT}'
$scale = {SCALE}
$app = New-Object -ComObject Excel.Application
# 창이 보이지 않으면 CopyPicture/Chart.Export 가 빈 그림을 내보낸다 → 잠깐 보이게 띄운다
$app.Visible = $true
$app.DisplayAlerts = $false
$app.ScreenUpdating = $true
try {
  $wb = $app.Workbooks.Open($xlsx)
  foreach ($ws in $wb.Worksheets) {
    if ($ws.Name -notmatch '폼([1-8])\(') { continue }
    $n = $Matches[1]
    $ws.Activate() | Out-Null
    $app.ActiveWindow.DisplayGridlines = $false
    $app.ActiveWindow.Zoom = 100
    $rng = $ws.UsedRange
    # xlScreen=1, xlPicture=-4147 (메타파일: 확대해도 깨지지 않는다)
    $rng.CopyPicture(1, -4147) | Out-Null
    $co = $ws.ChartObjects().Add(0, 0, $rng.Width * $scale, $rng.Height * $scale)
    $co.Activate() | Out-Null
    $co.Chart.Paste() | Out-Null
    # 붙인 그림을 차트 크기에 맞춰 키운다(벡터라 깨지지 않음)
    if ($co.Chart.Shapes.Count -gt 0) {
      $pic = $co.Chart.Shapes.Item($co.Chart.Shapes.Count)
      $pic.LockAspectRatio = 0
      $pic.Left = 0; $pic.Top = 0
      $pic.Width = $co.Chart.ChartArea.Width; $pic.Height = $co.Chart.ChartArea.Height
    }
    $co.Chart.Refresh() | Out-Null
    Start-Sleep -Milliseconds 300
    $co.Chart.Export("$out\form$n.png", "PNG") | Out-Null
    $co.Delete() | Out-Null
    Write-Output ("form$n " + [int]$rng.Width + "x" + [int]$rng.Height)
  }
  $wb.Close($false)
} finally {
  $app.Quit()
  [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($app)
}
"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="form-thumbs-") as tmp:
        tmp_dir = Path(tmp)
        xlsx = tmp_dir / "sample.xlsx"
        # 실제 quote_info.json 대신 기본값(가짜 데이터)으로 만든다.
        with mock.patch("app.excel_export.load_quote_info", return_value=dict(DEFAULT_INFO)):
            xlsx.write_bytes(build_excel(sample_result(), quote_kind_label="소비자견적", gallery=False).getvalue())
        script = (
            PS_SCRIPT.replace("{XLSX}", str(xlsx)).replace("{OUT}", str(tmp_dir)).replace("{SCALE}", str(EXPORT_SCALE))
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-EncodedCommand", encoded],
            capture_output=True,
            timeout=300,
        )
        print(completed.stdout.decode("utf-8", "replace").strip())
        if completed.returncode != 0:
            print(completed.stderr.decode("utf-8", "replace"), file=sys.stderr)
            return 1
        made = 0
        for n in FORM_LABELS:
            src = tmp_dir / f"form{n}.png"
            if not src.exists():
                print(f"[누락] form{n}.png — Excel 이 시트를 내보내지 못했습니다.", file=sys.stderr)
                continue
            src_img = Image.open(src).convert("RGB")
            print(f"  내보낸 원본 form{n}: {src_img.width}x{src_img.height}")
            for width, suffix in ((THUMB_WIDTH, ""), (LARGE_WIDTH, "_large")):
                ratio = width / src_img.width
                img = src_img.resize((width, max(1, round(src_img.height * ratio))), Image.LANCZOS)
                dst = OUT_DIR / f"form{n}{suffix}.png"
                img.save(dst, optimize=True)
                print(f"{dst.relative_to(ROOT)} {img.width}x{img.height} {dst.stat().st_size // 1024}KB")
            made += 1
        print(f"썸네일 {made}/{len(FORM_LABELS)}개 생성")
        return 0 if made == len(FORM_LABELS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
