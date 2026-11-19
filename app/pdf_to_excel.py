# -*- coding: utf-8 -*-
"""📤 PDF 견적서 → 그 모양 그대로의 Excel(.xlsx).

앱의 폼1~8 에 맞추지 않고, PDF 한 쪽을 Excel 시트 한 장으로 옮긴다(쪽마다 시트).

1. 추출(PDFium, pdf_export._pdfium_lock 안에서 순수 파이썬 데이터로만 꺼냄)
   - 글자 덩어리(text rect): 위치 · 글꼴 크기 · 굵기 · 색
   - 선: 얇은 칠한 사각형(Excel · doPDF · ezPDF 가 테두리를 이렇게 그림) + 선으로 그린 가로/세로 선분
   - 칸 배경: 칠한 사각형(흰색 · 쪽 전체 크기는 뺌)
   - 그림 영역: 사진 · 직인(이미지) + 복잡한 도형(로고 · 둥근 상자 · 화살표). 겹치거나 가까운 것끼리 묶고
     그 영역을 2배 해상도로 렌더링해 그림으로 붙인다(영역 안 글자 · 선은 그림에 포함되므로 칸에는 넣지 않음).
2. 격자: 선 · 배경 · 글자 가장자리 x/y 를 모아(2pt 안은 하나로) 열 · 행 경계를 만든다. 열 너비 · 행 높이 = 경계 간격.
3. 칸: 글자를 감싸는 선(없으면 배경 사각형, 그것도 없으면 글자 자신의 범위)을 칸으로 보고 병합한다.
   같은 칸 안의 여러 줄은 줄바꿈으로 잇고, 칸 안 위치로 왼쪽 · 가운데 · 오른쪽 정렬을 정한다.
   천 단위 쉼표가 있는 수만 숫자(#,##0)로 넣는다 — 전화 · 사업자번호 · 연도 · 앞자리 0 은 글자 그대로.
4. 쪽 크기 · 방향 그대로, 여백 0, 한 쪽에 맞춤으로 인쇄 설정.

재현하지 못하는 것: 정확한 글꼴(맑은 고딕으로 통일), 회전된 글자, 그림 영역 안 글자의 편집(그림이 됨),
점선 · 이중선 같은 선 모양(가는 실선 / 굵은 실선 두 가지로만).
"""
from __future__ import annotations

import ctypes
import io
from dataclasses import dataclass, field
from typing import Any

MAX_PAGES = 10
MAX_PATHS_PER_PAGE = 6000
SNAP_PT = 2.0          # 이 안의 경계는 하나로 본다
LINE_MAX_PT = 2.2      # 이보다 얇은 칠한 사각형 = 선
RENDER_SCALE = 2.0     # 그림 영역 렌더링 배율
FONT_NAME = "맑은 고딕"


class ConvertError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 변환 오류."""


@dataclass
class Seg:
    l: float
    t: float          # 위쪽(쪽 위에서 아래로 잰 값)
    r: float
    b: float          # 아래쪽(쪽 위에서 아래로 잰 값)
    text: str
    size: float = 10.0
    bold: bool = False
    color: tuple[int, int, int] = (0, 0, 0)
    font: str = FONT_NAME
    bordered: bool = True  # 선 · 배경으로 둘러싸인 칸인지(_write_page 가 채움)


@dataclass
class Line:
    horiz: bool
    pos: float        # 가로선이면 y, 세로선이면 x (위에서 아래 / 왼쪽에서 오른쪽)
    a: float          # 시작 (가로선 x0 / 세로선 y0)
    z: float          # 끝
    width: float
    color: tuple[int, int, int]
    dashed: bool = False


@dataclass
class Fill:
    l: float
    t: float
    r: float
    b: float
    color: tuple[int, int, int]


@dataclass
class Pic:
    l: float
    t: float
    r: float
    b: float
    png: bytes = b""


@dataclass
class Page:
    w: float
    h: float
    segs: list[Seg] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    pics: list[Pic] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. 추출
# ---------------------------------------------------------------------------
def _rgb(getter, obj) -> tuple[tuple[int, int, int], int]:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    ok = getter(obj, r, g, b, a)
    if not ok:
        return (0, 0, 0), 0
    return (r.value, g.value, b.value), a.value


_FONT_MAP = (("malgun", "맑은 고딕"), ("gulim", "굴림"), ("dotum", "돋움"), ("batang", "바탕"), ("gungsuh", "궁서"),
             ("nanumgothic", "나눔고딕"), ("nanummyeongjo", "나눔명조"), ("calibri", "Calibri"), ("arial", "Arial"),
             ("times", "Times New Roman"), ("tahoma", "Tahoma"), ("verdana", "Verdana"), ("segoe", "Segoe UI"))
_FORCE_BOLD = 0x40000  # PDF 글꼴 플래그 ForceBold


def _font_of(raw, tp_raw, i: int) -> tuple[str, bool]:
    """(Excel 글꼴 이름, 굵게). PDF 글꼴 이름 'ABCDEF+MalgunGothicBold' 의 앞 6글자 표식은 뺀다."""
    buf = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    raw.FPDFText_GetFontInfo(tp_raw, i, buf, 256, ctypes.byref(flags))
    name = buf.value.decode("utf-8", "replace").split("+")[-1]
    low = name.lower().replace(" ", "").replace("-", "")
    bold = "bold" in low or "black" in low or bool(flags.value & _FORCE_BOLD) or raw.FPDFText_GetFontWeight(tp_raw, i) >= 600
    family = next((ko for key, ko in _FONT_MAP if key in low), FONT_NAME)
    return family, bold


def _is_white(c: tuple[int, int, int]) -> bool:
    return min(c) >= 248


def _merge_boxes(boxes: list[list[float]], gap: float) -> list[list[float]]:
    """겹치거나 gap 안으로 가까운 상자를 합친다. 상자 = [l, t, r, b]."""
    boxes = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out: list[list[float]] = []
        for bx in boxes:
            for o in out:
                if bx[0] <= o[2] + gap and o[0] <= bx[2] + gap and bx[1] <= o[3] + gap and o[1] <= bx[3] + gap:
                    o[0], o[1], o[2], o[3] = min(o[0], bx[0]), min(o[1], bx[1]), max(o[2], bx[2]), max(o[3], bx[3])
                    changed = True
                    break
            else:
                out.append(bx)
        boxes = out
    return boxes


def _extract_page(pdfium, raw, doc, pi: int) -> Page:
    page = doc[pi]
    w, h = page.get_size()
    pg = Page(w=w, h=h)
    flip = lambda y: h - y  # PDF 좌표(아래 → 위) → 위에서 아래

    graphic: list[list[float]] = []   # 그림 영역 후보 [l, t, r, b]
    dashes: list[tuple] = []          # 점선 조각 (가로?, 위치, 시작, 끝, 두께, 색)
    n_paths = 0
    for obj in page.get_objects(max_depth=6):
        if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
            l, b, r, t = obj.get_bounds()
            if (r - l) * (t - b) < 0.6 * w * h:  # 쪽 전체 배경 그림은 뺀다
                graphic.append([l, flip(t), r, flip(b)])
            continue
        if obj.type != raw.FPDF_PAGEOBJ_PATH:
            continue
        n_paths += 1
        if n_paths > MAX_PATHS_PER_PAGE:
            break
        l, b, r, t = obj.get_bounds()
        bw, bh = r - l, t - b
        fill_mode, stroke = ctypes.c_int(), ctypes.c_int()
        raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill_mode), ctypes.byref(stroke))
        fcol, fa = _rgb(raw.FPDFPageObj_GetFillColor, obj.raw)
        scol, sa = _rgb(raw.FPDFPageObj_GetStrokeColor, obj.raw)
        sw = ctypes.c_float(1.0)
        raw.FPDFPageObj_GetStrokeWidth(obj.raw, ctypes.byref(sw))
        nseg = raw.FPDFPath_CountSegments(obj.raw)
        # 점들: 경로 점은 개체 좌표라 개체 변환 행렬을 곱해 쪽 좌표로 바꾼다(get_bounds 는 이미 쪽 좌표)
        try:
            m = obj.get_matrix()
            ma, mb, mc, md, me, mf = m.a, m.b, m.c, m.d, m.e, m.f
        except Exception:
            ma, mb, mc, md, me, mf = 1, 0, 0, 1, 0, 0
        pts = []
        for i in range(nseg):
            s = raw.FPDFPath_GetPathSegment(obj.raw, i)
            x, y = ctypes.c_float(), ctypes.c_float()
            raw.FPDFPathSegment_GetPoint(s, ctypes.byref(x), ctypes.byref(y))
            px, py = x.value, y.value
            pts.append((ma * px + mc * py + me, mb * px + md * py + mf, raw.FPDFPathSegment_GetType(s)))
        has_curve = any(p[2] == raw.FPDF_SEGMENT_BEZIERTO for p in pts)
        rect_like = nseg <= 6 and not has_curve
        if fill_mode.value and fa > 0 and rect_like:
            if min(bw, bh) <= LINE_MAX_PT and max(bw, bh) < 3:       # 점선 조각(아래에서 줄로 묶음)
                dashes.append((bw >= bh, flip((t + b) / 2) if bw >= bh else (l + r) / 2,
                               l if bw >= bh else flip(t), r if bw >= bh else flip(b), min(bw, bh), fcol))
                continue
            if min(bw, bh) <= LINE_MAX_PT and max(bw, bh) >= 3:      # 얇은 칠한 사각형 = 선
                if bw >= bh:
                    pg.lines.append(Line(True, flip((t + b) / 2), l, r, max(bh, 0.5), fcol))
                else:
                    pg.lines.append(Line(False, (l + r) / 2, flip(t), flip(b), max(bw, 0.5), fcol))
                continue
            if not _is_white(fcol) and bw * bh < 0.9 * w * h:      # 칸 배경
                pg.fills.append(Fill(l, flip(t), r, flip(b), fcol))
            if not stroke.value:
                continue
        if stroke.value and sa > 0 and not has_curve and nseg <= 64:
            # 선분 단위로 가로/세로만 선으로 쓴다 (사각형 테두리, 표 격자 한 덩어리 포함)
            straight = 0
            prev = None
            for x, y, typ in pts:
                if typ == raw.FPDF_SEGMENT_MOVETO or prev is None:
                    prev = (x, y)
                    continue
                x0, y0 = prev
                if abs(y - y0) < 0.6 and abs(x - x0) >= 3:
                    pg.lines.append(Line(True, flip(y), min(x, x0), max(x, x0), sw.value, scol))
                    straight += 1
                elif abs(x - x0) < 0.6 and abs(y - y0) >= 3:
                    pg.lines.append(Line(False, x, flip(max(y, y0)), flip(min(y, y0)), sw.value, scol))
                    straight += 1
                prev = (x, y)
            if straight and nseg <= 6:
                continue
        if bw * bh >= 4 and bw * bh < 0.6 * w * h and (has_curve or nseg > 6):
            graphic.append([l, flip(t), r, flip(b)])  # 로고 · 둥근 상자 · 화살표 같은 도형

    # 점선: 같은 줄에 4pt 안 간격으로 이어진 조각 2개 이상 → 점선 한 줄
    dashes.sort(key=lambda d: (d[0], round(d[1] * 2), d[2]))
    run: list[tuple] = []

    def close_run():
        if len(run) >= 2:
            d0 = run[0]
            pg.lines.append(Line(d0[0], sum(d[1] for d in run) / len(run), run[0][2], run[-1][3], d0[4], d0[5], True))

    for d in dashes:
        if run and d[0] == run[-1][0] and abs(d[1] - run[-1][1]) < 0.6 and d[2] - run[-1][3] <= 4.0:
            run.append(d)
        else:
            close_run()
            run = [d]
    close_run()

    # 그림 영역: 묶고, 영역 안의 선 · 배경은 그림에 들어가므로 뺀다
    regions = _merge_boxes(graphic, gap=4.0)
    regions = [g for g in regions if (g[2] - g[0]) >= 4 and (g[3] - g[1]) >= 4]

    def inside(x: float, y: float, g: list[float]) -> bool:
        return g[0] - 0.5 <= x <= g[2] + 0.5 and g[1] - 0.5 <= y <= g[3] + 0.5

    def in_any(x: float, y: float) -> bool:
        return any(inside(x, y, g) for g in regions)

    pg.lines = [ln for ln in pg.lines if not (
        in_any((ln.a + ln.z) / 2, ln.pos) if ln.horiz else in_any(ln.pos, (ln.a + ln.z) / 2))]
    pg.fills = [f for f in pg.fills if not in_any((f.l + f.r) / 2, (f.t + f.b) / 2) or
                (f.r - f.l) * (f.b - f.t) > 4 * max(((g[2] - g[0]) * (g[3] - g[1]) for g in regions), default=0)]

    # 글자: 한 글자씩 위치를 읽어 단어 · 덩어리로 다시 묶는다.
    # (ezPDF 처럼 글자마다 따로 놓는 PDF 는 text rect 가 '8 7 9 , 0 0 0' 처럼 벌어져 나오므로 글자 단위로 본다)
    tp = page.get_textpage()
    try:
        chars = []
        for i in range(tp.count_chars()):
            ch = tp.get_text_range(i, 1)
            if not ch or ch in ("\r", "\n"):
                continue
            if ch.isspace():  # PDF 에 적힌 띄어쓰기: 앞 글자 뒤에 띄어쓰기 표시만 남긴다
                if chars:
                    chars[-1][10] = True
                continue
            # (왼, 아래, 오른, 위). loose 상자 = 글자 진행 폭 · 글꼴 높이라 쉼표 · 마침표 뒤 간격을 띄어쓰기로 오해하지 않는다
            l, b, r, t = tp.get_charbox(i, loose=True)
            if r - l <= 0 and t - b <= 0:
                continue
            # 글꼴 크기: PDF 가 알려 주는 크기는 글자 개체 변환 전 값이다(예: Edge 는 24 × 0.75 = 실제 18pt).
            # 글자 개체 행렬의 세로 배율을 곱해 쪽 위 크기로 바꾼다. 못 읽으면 글자 높이(loose)로 어림한다.
            fs = raw.FPDFText_GetFontSize(tp.raw, i)
            lh = t - b
            scale = 1.0
            tobj = raw.FPDFText_GetTextObject(tp.raw, i)
            if tobj:
                mtx = raw.FS_MATRIX()
                if raw.FPDFPageObj_GetMatrix(tobj, ctypes.byref(mtx)):
                    scale = (abs(mtx.a * mtx.d - mtx.b * mtx.c)) ** 0.5 or 1.0
            size = fs * scale if fs and 3 <= fs * scale <= 96 else max(4.0, lh / 1.2)
            family, bold = _font_of(raw, tp.raw, i)
            rr, gg, bb, aa = (ctypes.c_uint() for _ in range(4))
            color = (rr.value, gg.value, bb.value) if raw.FPDFText_GetFillColor(tp.raw, i, rr, gg, bb, aa) else (0, 0, 0)
            cy = flip((t + b) / 2)
            if in_any((l + r) / 2, cy):
                continue
            # 글자 상자 높이는 글자마다 달라(·, -) 줄 맞춤에는 글꼴 크기 기준 가운데를 쓴다
            chars.append([l, r, flip(t), flip(b), cy, ch, size, bold, color, family, False])
        # 줄(기준선이 같은 글자)로 묶기 → 줄 안에서 간격으로 단어 · 덩어리 나누기
        chars.sort(key=lambda c: (round(c[4] / 2), c[0]))
        lines: list[list[list]] = []
        for c in sorted(chars, key=lambda c: c[4]):
            for ln in lines[-6:]:
                ref = ln[0]
                if abs(c[4] - ref[4]) <= 0.35 * max(c[6], ref[6]):
                    ln.append(c)
                    break
            else:
                lines.append([c])
        vlines = [v for v in pg.lines if not v.horiz]
        for ln in lines:
            ln.sort(key=lambda c: c[0])
            cur: list[list] = []
            text = ""

            def flush():
                if cur and text.strip():
                    l = min(c[0] for c in cur)
                    r = max(c[1] for c in cur)
                    t = min(c[2] for c in cur)
                    b = max(c[3] for c in cur)
                    first = max(cur, key=lambda c: c[6])
                    pg.segs.append(Seg(l, t, r, b, " ".join(text.split()), first[6],
                                       sum(1 for c in cur if c[7]) * 2 >= len(cur), cur[0][8], cur[0][9]))

            for c in ln:
                if cur:
                    gap = c[0] - cur[-1][1]
                    em = max(c[6], cur[-1][6])
                    # 두 글자 사이에 세로 표 선이 있으면 간격이 좁아도 다른 칸(좁은 칸이 붙어 '명 성김 재수' 처럼 합쳐지지 않게)
                    split_by_line = any(cur[-1][0] + (cur[-1][1] - cur[-1][0]) * 0.5 < v.pos < c[0] + (c[1] - c[0]) * 0.5
                                        and v.a - 1 <= c[4] <= v.z + 1 for v in vlines)
                    if gap > 1.2 * em or split_by_line:          # 다른 칸으로 본다
                        flush()
                        cur, text = [], ""
                    elif gap > 0.22 * em or cur[-1][10]:   # 띄어쓰기(간격, 또는 PDF 의 공백 글자)
                        text += " "
                cur.append(c)
                text += c[5]
            flush()
    finally:
        tp.close()

    # 그림 영역 렌더링(그 영역 안의 글자 · 선 · 사진이 모두 들어간다)
    for g in regions:
        l, t, r, b = g
        try:
            bmp = page.render(scale=RENDER_SCALE, crop=(max(0, l), max(0, h - b), max(0, w - r), max(0, t)))
            buf = io.BytesIO()
            bmp.to_pil().save(buf, format="PNG")
            pg.pics.append(Pic(l, t, r, b, buf.getvalue()))
        except Exception:
            continue
    page.close()
    return pg


def extract(content: bytes) -> list[Page]:
    if not content.startswith(b"%PDF-"):
        raise ConvertError("PDF 파일이 아닙니다(파일 내용이 PDF 형식이 아님).")
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    from app.pdf_export import _pdfium_lock

    with _pdfium_lock:  # PDFium 은 스레드 안전하지 않다 — 미리보기 썸네일과 동시에 부르면 서버가 죽는다
        try:
            doc = pdfium.PdfDocument(content)
        except Exception as e:
            raise ConvertError(f"PDF 를 열지 못했습니다(손상되었거나 암호가 걸린 파일): {e}") from e
        try:
            n = len(doc)
            if n > MAX_PAGES:
                raise ConvertError(f"쪽수가 너무 많습니다({n}쪽). 견적서는 {MAX_PAGES}쪽까지 바꿉니다.")
            pages = [_extract_page(pdfium, raw, doc, i) for i in range(n)]
        finally:
            doc.close()
    if not any(p.segs for p in pages):
        raise ConvertError("PDF 에서 글자를 읽지 못했습니다. 스캔하거나 사진으로 만든 PDF 는 글자 층이 없어 엑셀로 바꿀 수 없습니다"
                           "(Excel · 한글 등에서 'PDF로 저장'한 파일만 바꿉니다).")
    return pages


# ---------------------------------------------------------------------------
# 2~4. 격자 → 시트
# ---------------------------------------------------------------------------
def _snap_edges(values: list[float], lo: float, hi: float) -> list[float]:
    vals = sorted(v for v in values if lo - 0.5 <= v <= hi + 0.5) + [lo, hi]
    vals.sort()
    edges: list[float] = []
    for v in vals:
        if edges and v - edges[-1] < SNAP_PT:
            continue
        edges.append(v)
    if edges[-1] < hi - 0.5:
        edges.append(hi)
    return edges


def _nearest(edges: list[float], v: float) -> int:
    import bisect
    i = bisect.bisect_left(edges, v)
    if i <= 0:
        return 0
    if i >= len(edges):
        return len(edges) - 1
    return i if edges[i] - v < v - edges[i - 1] else i - 1


def _box_for(seg: Seg, page: Page) -> tuple[float, float, float, float, bool]:
    """글자를 감싸는 칸 (l, t, r, b, 선으로_둘러싸였는지). 선 → 배경 사각형 → 글자 자신 순."""
    cx, cy = (seg.l + seg.r) / 2, (seg.t + seg.b) / 2
    tol = 1.5
    lefts = [ln.pos for ln in page.lines if not ln.horiz and ln.pos <= seg.l + tol and ln.a - tol <= cy <= ln.z + tol]
    rights = [ln.pos for ln in page.lines if not ln.horiz and ln.pos >= seg.r - tol and ln.a - tol <= cy <= ln.z + tol]
    tops = [ln.pos for ln in page.lines if ln.horiz and ln.pos <= seg.t + tol and ln.a - tol <= cx <= ln.z + tol]
    bots = [ln.pos for ln in page.lines if ln.horiz and ln.pos >= seg.b - tol and ln.a - tol <= cx <= ln.z + tol]
    # 배경 사각형 가장자리도 칸 경계로 쓴다(선 없이 색만 칠한 칸)
    for f in page.fills:
        if f.l - tol <= seg.l and seg.r <= f.r + tol and f.t - tol <= seg.t and seg.b <= f.b + tol:
            lefts.append(f.l)
            rights.append(f.r)
            tops.append(f.t)
            bots.append(f.b)
    if lefts and rights and tops and bots:
        l, r, t, b = max(lefts), min(rights), max(tops), min(bots)
        # 너무 크게 잡힌 칸(예: 바깥 테두리만 있는 큰 영역)은 글자 줄 높이로 줄인다
        if b - t > 6 * (seg.b - seg.t) and not any(ln.horiz and abs(ln.pos - b) < tol for ln in page.lines if ln.a <= cx <= ln.z):
            t, b = seg.t, seg.b
        return l, t, r, b, True
    l = max(lefts) if lefts and seg.l - max(lefts) < 4 else seg.l
    r = min(rights) if rights and min(rights) - seg.r < 4 else seg.r
    return l, seg.t, r, seg.b, False


# ---------------------------------------------------------------------------
# Excel 열 너비 보정: 열 너비 단위(글자 수)가 인쇄될 때 몇 pt 가 되는지는 PC 의 화면 배율 · 기본 글꼴에 따라 다르다.
# 서버를 켠 뒤 처음 변환할 때 Excel 로 시험 시트를 한 번 인쇄해 '너비 w → 인쇄 pt = a·w + b' 와 행 높이 인쇄 배율을
# 재고(약 2초), 같은 배율로 가로 · 세로가 인쇄되게 열 너비를 정한다. Excel 이 없거나 실패하면 표준 공식(7px/글자)을 쓴다.
# ---------------------------------------------------------------------------
_METRICS: dict[str, float] | None = None
_METRICS_LOCK = __import__("threading").Lock()


def _measure_excel_metrics() -> dict[str, float] | None:
    from openpyxl import Workbook
    from openpyxl.styles import Border, Side

    from app import pdf_export

    if not pdf_export.excel_available():
        return None
    wb = Workbook()
    ws = wb.active
    thin = Side(style="thin")
    w1, w2, h = 10.0, 20.0, 200.0
    ws.column_dimensions["A"].width = w1
    ws.column_dimensions["B"].width = w2
    ws.row_dimensions[1].height = h
    for col in ("A", "B"):
        ws[f"{col}1"].border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    for k in ("left", "right", "top", "bottom", "header", "footer"):
        setattr(ws.page_margins, k, 0)
    ws.page_setup.scale = 100
    ws.print_area = "A1:B1"
    buf = io.BytesIO()
    wb.save(buf)
    pdf = pdf_export.xlsx_to_pdf(buf.getvalue())
    import pypdfium2 as pdfium

    from app.pdf_export import _pdfium_lock

    with _pdfium_lock:
        doc = pdfium.PdfDocument(pdf)
        try:
            pg = doc[0]
            vx, hy = [], []
            for o in pg.get_objects(max_depth=4):
                l, b, r, t = o.get_bounds()
                if r - l < 2.5 and t - b > 20:
                    vx.append((l + r) / 2)
                elif t - b < 2.5 and r - l > 10:
                    hy.append((t + b) / 2)
        finally:
            doc.close()
    # 선 하나가 겹친 두 개체로 그려지므로 1.5pt 안은 하나로 묶는다
    def cluster(vals):
        out = []
        for v in sorted(vals):
            if out and v - out[-1][-1] < 1.5:
                out[-1].append(v)
            else:
                out.append([v])
        return [sum(c) / len(c) for c in out]

    vx, hy = cluster(vx), cluster(hy)
    if len(vx) < 3 or len(hy) < 2:
        return None
    pt1, pt2 = vx[1] - vx[0], vx[-1] - vx[1]
    a = (pt2 - pt1) / (w2 - w1)
    b = pt1 - a * w1
    row_k = (hy[-1] - hy[0]) / h
    if not (2.0 < a < 12.0 and 0.8 < row_k < 1.2):
        return None
    return {"a": a, "b": b, "row_k": row_k}


def _excel_metrics() -> dict[str, float]:
    global _METRICS
    with _METRICS_LOCK:
        if _METRICS is None:
            try:
                _METRICS = _measure_excel_metrics() or {}
            except Exception:
                _METRICS = {}
        return _METRICS


def _col_width_for(pt: float, m: dict[str, float]) -> float:
    """인쇄 폭 pt 가 나오는 Excel 열 너비. 보정값이 있으면 그것으로(행과 같은 인쇄 배율 row_k 에 맞춤), 없으면 7px/글자."""
    if m:
        target = pt * m["row_k"]
        if target >= m["a"] + m["b"]:      # 너비 1 이상: 인쇄 pt = a·w + b
            return (target - m["b"]) / m["a"]
        return target / (m["a"] + m["b"])  # 너비 1 미만: 너비에 비례
    px = pt * 4 / 3
    return (px - 5) / 7 if px >= 12 else px / 12


def _hex(c: tuple[int, int, int]) -> str:
    return "%02X%02X%02X" % c


def _num_value(text: str):
    import re
    t = text.strip()
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+", t):
        return int(t.replace(",", ""))
    return None


def _write_page(wb, page: Page, title: str) -> None:
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
    from openpyxl.drawing.xdr import XDRPositiveSize2D
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.utils.units import pixels_to_EMU

    ws = wb.create_sheet(title)
    ws.sheet_view.showGridLines = False

    boxes = []
    for s in page.segs:
        l, t, r, b, bordered = _box_for(s, page)
        s.bordered = bordered
        boxes.append((s, l, t, r, b, bordered))

    xs = [ln.pos for ln in page.lines if not ln.horiz] + [v for ln in page.lines if ln.horiz for v in (ln.a, ln.z)]
    ys = [ln.pos for ln in page.lines if ln.horiz] + [v for ln in page.lines if not ln.horiz for v in (ln.a, ln.z)]
    for f in page.fills:
        xs += [f.l, f.r]
        ys += [f.t, f.b]
    for p in page.pics:
        xs += [p.l]
        ys += [p.t]
    for s, l, t, r, b, _ in boxes:
        xs += [l, r]
        ys += [t, b]
    X = _snap_edges(xs, 0.0, page.w)
    Y = _snap_edges(ys, 0.0, page.h)

    # 열 너비 · 행 높이. Excel 은 너비 w 열을 w ≥ 1 이면 7w+5 픽셀, w < 1 이면 12w 픽셀로 그린다(기본 글꼴 기준). 픽셀 = pt × 4/3
    # Excel 은 열마다 정수 픽셀로 반올림하므로, 열이 많으면 오차가 쌓여 쪽보다 넓어지고 '한 쪽에 맞춤'이 전체를 줄인다.
    # 그래서 누적 위치를 픽셀로 반올림해 열마다 정수 픽셀을 나눠 주고, 그 픽셀이 정확히 나오는 너비 값을 쓴다.
    metrics = _excel_metrics()
    prev_px = 0
    for ci in range(len(X) - 1):
        px = max(1, round(X[ci + 1] * 4 / 3) - prev_px)
        prev_px += px
        ws.column_dimensions[get_column_letter(ci + 1)].width = round(max(0.05, _col_width_for(px * 0.75, metrics)), 4)
    # 행 높이도 Excel 이 1픽셀(0.75pt) 단위로 맞추므로 누적 위치를 픽셀로 반올림해 나눈다(합이 쪽 높이를 넘지 않게)
    prev_py = 0
    for ri in range(len(Y) - 1):
        py = max(1, round(Y[ri + 1] * 4 / 3) - prev_py)
        prev_py += py
        ws.row_dimensions[ri + 1].height = py * 0.75

    def col_of(x: float) -> int:
        return _nearest(X, x)

    def row_of(y: float) -> int:
        return _nearest(Y, y)

    import bisect

    # 떠 있는 글자 칸의 시작 · 끝: 가장 가까운 경계를 쓰되, 그 경계가 글자 안쪽으로 2pt 넘게 들어가면(칸이 글자보다
    # 좁아져 맞춤 축소로 글자가 작아짐) 바깥쪽 경계를 쓴다. 2pt 안 경계는 하나로 묶였으니 가까운 쪽이 맞다.
    def col_start(x: float) -> int:
        i = _nearest(X, x)
        return i if X[i] <= x + SNAP_PT else max(0, bisect.bisect_right(X, x) - 1)

    def col_end(x: float) -> int:
        i = _nearest(X, x)
        return i if X[i] >= x - SNAP_PT else min(len(X) - 1, bisect.bisect_left(X, x))

    def row_start(y: float) -> int:
        i = _nearest(Y, y)
        return i if Y[i] <= y + SNAP_PT else max(0, bisect.bisect_right(Y, y) - 1)

    def row_end(y: float) -> int:
        i = _nearest(Y, y)
        return i if Y[i] >= y - SNAP_PT else min(len(Y) - 1, bisect.bisect_left(Y, y))

    # 배경 (그린 순서대로 — 나중 것이 위)
    for f in page.fills:
        c0, c1, r0, r1 = col_of(f.l), col_of(f.r), row_of(f.t), row_of(f.b)
        fill = PatternFill("solid", fgColor=_hex(f.color))
        for rr in range(r0, max(r1, r0 + 1)):
            for cc in range(c0, max(c1, c0 + 1)):
                ws.cell(row=rr + 1, column=cc + 1).fill = fill

    # 테두리
    sides: dict[tuple[int, int], dict[str, Side]] = {}

    def side_of(ln: Line) -> Side:
        style = "dotted" if ln.dashed else ("medium" if ln.width >= 1.6 else "thin")
        return Side(style=style, color=_hex(ln.color))

    for ln in page.lines:
        sd = side_of(ln)
        if ln.horiz:
            r_ = row_of(ln.pos)
            for cc in range(col_of(ln.a), col_of(ln.z)):
                if r_ < len(Y) - 1:
                    sides.setdefault((r_, cc), {})["top"] = sd
                if r_ > 0:
                    sides.setdefault((r_ - 1, cc), {})["bottom"] = sd
        else:
            c_ = col_of(ln.pos)
            for rr in range(row_of(ln.a), row_of(ln.z)):
                if c_ < len(X) - 1:
                    sides.setdefault((rr, c_), {})["left"] = sd
                if c_ > 0:
                    sides.setdefault((rr, c_ - 1), {})["right"] = sd
    for (rr, cc), sd in sides.items():
        ws.cell(row=rr + 1, column=cc + 1).border = Border(**sd)

    # 글자 칸: 같은 칸끼리 묶는다
    cells: dict[tuple[int, int, int, int], list[Seg]] = {}
    for s, l, t, r, b, bordered in boxes:
        # 떠 있는 글자도 제 폭만큼 병합한다(옆 칸으로 넘쳐 그리기는 옆 칸이 비어야만 되고 수는 아예 안 넘침)
        if bordered:  # 선 · 배경 칸: 가장자리가 선 위에 있으니 가장 가까운 경계(2pt 안은 한 경계로 묶여 조금 어긋날 수 있음)
            r0_, c0_ = row_of(t), col_of(l)
            key = (r0_, c0_, max(row_of(b), r0_ + 1), max(col_of(r), c0_ + 1))
        else:         # 떠 있는 글자: 바깥쪽으로 맞춰 글자 폭보다 좁아지지 않게(좁으면 맞춤 축소로 글자가 작아짐)
            r0_, c0_ = row_start(t), col_start(l)
            key = (r0_, c0_, max(row_end(b), r0_ + 1), max(col_end(r), c0_ + 1))
        cells.setdefault(key, []).append(s)
    used: list[tuple[int, int, int, int]] = []
    # 모든 글자 칸이 차지할 자리(왼쪽 정렬 한 줄 글자를 오른쪽 빈 칸으로 넓힐 때 남의 자리를 넘지 않게)
    occ: set[tuple[int, int]] = {(rr, cc) for (a0, b0, a1, b1) in cells for rr in range(a0, a1) for cc in range(b0, b1)}
    vlines_all = [ln for ln in page.lines if not ln.horiz]

    def overlaps(a, b) -> bool:
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    for key in sorted(cells, key=lambda k: (k[0], k[1])):
        segs = sorted(cells[key], key=lambda s: (round(s.t / 3), s.l))
        r0, c0, r1, c1 = key
        rng = (r0, c0, r1, c1)
        if any(overlaps(rng, u) for u in used):  # 겹치면 병합하지 않고 첫 칸에만
            rng = (r0, c0, r0 + 1, c0 + 1)
            if any(overlaps(rng, u) for u in used):
                tgt = next(u for u in used if overlaps(rng, u))
                cell = ws.cell(row=tgt[0] + 1, column=tgt[1] + 1)
                cell.value = f"{cell.value or ''} {' '.join(s.text for s in segs)}".strip()
                continue
        used.append(rng)
        r0, c0, r1, c1 = rng
        # 줄 단위로 잇기
        lines_txt: list[str] = []
        last_t = None
        for s in segs:
            if last_t is not None and abs(s.t - last_t) < max(2.0, (s.b - s.t) * 0.5):
                lines_txt[-1] += " " + s.text
            else:
                lines_txt.append(s.text)
                last_t = s.t
        text = "\n".join(lines_txt)
        first = segs[0]
        cell = ws.cell(row=r0 + 1, column=c0 + 1)
        num = _num_value(text) if len(lines_txt) == 1 else None
        if num is not None:
            cell.value = num
            cell.number_format = "#,##0"
        else:
            cell.value = text
            cell.number_format = "@"
        # 정렬: 칸 안에서 글자 위치
        bl, br = X[c0], X[min(c1, len(X) - 1)]
        bt, bb = Y[r0], Y[min(r1, len(Y) - 1)]
        sl, sr = min(s.l for s in segs), max(s.r for s in segs)
        st, sb = min(s.t for s in segs), max(s.b for s in segs)
        gl, gr = sl - bl, br - sr
        multi = len(lines_txt) > 1
        if not first.bordered:
            horiz = "right" if num is not None else "left"   # 떠 있는 글자: 시작(수는 끝) 위치가 곧 PDF 위치
        elif multi:
            # 여러 줄: 줄 시작이 모두 같으면 왼쪽, 끝이 같으면 오른쪽, 아니면 가운데
            starts = [min(s.l for s in segs if abs(s.t - ln_t) < 2) for ln_t in sorted({round(s.t, 0) for s in segs})]
            ends = [max(s.r for s in segs if abs(s.t - ln_t) < 2) for ln_t in sorted({round(s.t, 0) for s in segs})]
            # (들여 쓴 줄이 한두 개 있어도 대부분의 줄이 같은 곳에서 시작하면 왼쪽)
            same_start = sum(1 for x in starts if x - min(starts) <= 2.0) / len(starts)
            same_end = sum(1 for x in ends if max(ends) - x <= 2.0) / len(ends)
            mids = [(a + b) / 2 for a, b in zip(starts, ends)]
            box_mid = (bl + br) / 2
            centered = max(mids) - min(mids) <= 2.5 and abs(sum(mids) / len(mids) - box_mid) <= max(3.0, 0.1 * (br - bl))
            if centered:   # 줄마다 칸 가운데에 놓인 글(세로로 쓴 '공/급/자' 포함)
                horiz = "center"
            else:
                horiz = "left" if same_start >= 0.6 else ("right" if same_end >= 0.6 else "center")
        elif abs(gl - gr) <= max(2.5, 0.08 * (br - bl)):
            horiz = "center"
        elif gr < gl:
            horiz = "right"
        else:
            horiz = "left"
        # 왼쪽 정렬 한 줄 글자: Excel 글꼴이 PDF 보다 조금 넓어 맞춤 축소로 작아지지 않게, 오른쪽이 비어 있으면
        # (남의 글자 칸 · 세로 선이 없으면) 글자 폭의 1.6배까지 칸을 넓힌다
        tight = gl <= 2.0 and gr <= 2.0  # 칸이 글자 폭 그대로(선 없이 떠 있거나 배경 칸이 글자에 딱 맞음)
        if (horiz == "left" or tight) and not multi and num is None:
            horiz = "left"
            ymid = (Y[r0] + Y[min(r1, len(Y) - 1)]) / 2
            limit = sl + (sr - sl) * 1.6 + 6
            while (c1 < len(X) - 1 and X[c1] < limit
                   and all((rr, c1) not in occ for rr in range(r0, r1))
                   and not any(abs(v.pos - X[c1]) < SNAP_PT and v.a - 1 <= ymid <= v.z + 1 for v in vlines_all)):
                for rr in range(r0, r1):
                    occ.add((rr, c1))
                c1 += 1
            used[-1] = (r0, c0, r1, c1)
        # 세로: 칸이 글자보다 훨씬 높으면(큰 테두리 안 제목 · 목록) PDF 에서 놓인 쪽(위 · 아래)에 맞춘다
        gt, gb = st - bt, bb - sb
        vert = "center"
        if (bb - bt) > 1.4 * max(1.0, sb - st) and (bb - bt) - (sb - st) > 8:
            vert = "top" if gt <= gb / 2 else ("bottom" if gb <= gt / 2 else "center")
        # 한 줄 글자는 Excel 글꼴이 조금 넓어도 잘리지 않게 맞춤 축소, 여러 줄은 줄바꿈
        cell.alignment = Alignment(horizontal=horiz, vertical=vert, wrap_text=multi, shrink_to_fit=not multi)
        # 여러 줄 칸은 Excel 이 칸 안쪽 여백(양쪽 약 1.5pt)만큼 좁게 다시 줄바꿈해 잘리므로, 가장 긴 줄이 안쪽 폭에 들어가게
        # 글자를 줄인다(최소 0.92배, 최대 0.7배까지). 한 줄은 맞춤 축소가 처리한다.
        fsize = first.size
        if multi:
            widest = 0.0
            for ln_t in sorted({round(s.t, 0) for s in segs}):
                row_segs = [s for s in segs if abs(s.t - ln_t) < 2]
                widest = max(widest, max(s.r for s in row_segs) - min(s.l for s in row_segs))
            inner = max(1.0, (br - bl) - 4.0)
            fsize = first.size * max(0.7, min(0.92, 0.97 * inner / max(widest, 1.0)))
        cell.font = Font(name=first.font, size=round(fsize * 2) / 2, bold=first.bold, color=_hex(first.color))
        if r1 - r0 > 1 or c1 - c0 > 1:
            ws.merge_cells(start_row=r0 + 1, start_column=c0 + 1, end_row=r1, end_column=c1)

    # 병합이 겹치지 않는지(겹치면 Excel 이 파일이 손상됐다고 함)
    ranges = [(m.min_row, m.min_col, m.max_row, m.max_col) for m in ws.merged_cells.ranges]
    for i, a in enumerate(ranges):
        for b in ranges[i + 1:]:
            if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                raise ConvertError("내부 오류: 병합 칸이 겹칩니다.")

    # 그림
    for p in page.pics:
        try:
            img = XLImage(io.BytesIO(p.png))
        except Exception:
            continue
        wpx, hpx = (p.r - p.l) * 4 / 3, (p.b - p.t) * 4 / 3
        img.width, img.height = wpx, hpx
        c0 = max(i for i, x in enumerate(X) if x <= p.l + 0.01) if any(x <= p.l + 0.01 for x in X) else 0
        r0 = max(i for i, y in enumerate(Y) if y <= p.t + 0.01) if any(y <= p.t + 0.01 for y in Y) else 0
        marker = AnchorMarker(col=c0, colOff=pixels_to_EMU(int((p.l - X[c0]) * 4 / 3)),
                              row=r0, rowOff=pixels_to_EMU(int((p.t - Y[r0]) * 4 / 3)))
        img.anchor = OneCellAnchor(_from=marker, ext=XDRPositiveSize2D(pixels_to_EMU(int(wpx)), pixels_to_EMU(int(hpx))))
        ws.add_image(img)

    # 인쇄: 쪽 크기 · 방향 그대로, 여백 0, 한 쪽에 맞춤
    ws.page_setup.orientation = "landscape" if page.w > page.h else "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4 if abs(min(page.w, page.h) - 595) < 15 else (
        ws.PAPERSIZE_A3 if abs(min(page.w, page.h) - 842) < 20 else ws.PAPERSIZE_LETTER)
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = ws.page_margins.top = ws.page_margins.bottom = 0
    ws.page_margins.header = ws.page_margins.footer = 0
    ws.print_options.horizontalCentered = True
    ws.print_area = f"A1:{get_column_letter(len(X) - 1)}{len(Y) - 1}"


def pdf_to_xlsx(content: bytes) -> tuple[bytes, list[Page]]:
    """PDF 바이트 → (xlsx 바이트, 추출한 쪽 정보). 쪽마다 시트 한 장."""
    from openpyxl import Workbook

    pages = extract(content)
    wb = Workbook()
    wb.remove(wb.active)
    for i, pg in enumerate(pages):
        _write_page(wb, pg, "견적서" if len(pages) == 1 else f"{i + 1}쪽")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), pages


def rows_for_meta(pages: list[Page]) -> list[list[str]]:
    """quote_import.parse_rows 에 넘길 줄(칸 목록) — 목록에 보일 거래처 · 합계만 읽는 데 쓴다."""
    rows: list[list[str]] = []
    for pg in pages:
        segs = sorted(pg.segs, key=lambda s: ((s.t + s.b) / 2, s.l))
        cur: list[Seg] = []
        for s in segs:
            if cur and abs((s.t + s.b) / 2 - sum((c.t + c.b) / 2 for c in cur) / len(cur)) > 4.5:
                rows.append([c.text for c in sorted(cur, key=lambda c: c.l)])
                cur = []
            cur.append(s)
        if cur:
            rows.append([c.text for c in sorted(cur, key=lambda c: c.l)])
    return rows
