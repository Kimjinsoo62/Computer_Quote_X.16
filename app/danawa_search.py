# -*- coding: utf-8 -*-
"""다나와 통합검색에서 컴퓨존 견적과 사양(CPU · 그래픽카드 · RAM · SSD)이 가장 비슷한 완제품 PC를 찾는다."""
from __future__ import annotations

import html
import logging
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.used_market import _parts_from_model_paren, short_cpu_name, short_gpu_name

logger = logging.getLogger(__name__)

SEARCH_URL = "https://search.danawa.com/dsearch.php"
PRODUCT_URL = "https://prod.danawa.com/info/?pcode={pcode}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
FETCH_TIMEOUT_SEC = 15
# 상품 목록이 비어 오는 경우가 있어 몇 번 다시 요청한다.
FETCH_RETRIES = 3
CACHE_TTL_SEC = 10 * 60
MAX_ITEMS = 12
MIN_RESULTS_BEFORE_FALLBACK = 5

_cache_lock = threading.Lock()
_search_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

# 2026-10 개편된 검색 화면: 상품마다 <li data-product-seq="상품코드"> 한 칸.
# 상품명 → 사양 줄('9800X3D / RTX 5070 Ti / … / 닫기') → 상품분류 → 옵션별 가격('3,969,000원') 순서다.
_ITEM_START_RE = re.compile(r'<li[^>]*data-product-seq="(\d+)"')
_CATEGORY_RE = re.compile(r"<dt[^>]*>상품분류</dt>\s*<dd>(.*?)</dd>", re.S)
_PRICE_RE = re.compile(r">\s*([\d,]+)원\s*<")
_NAME_RE = re.compile(r'<p[^>]*>\s*<a[^>]*pcode=\d+[^>]*>(.*?)</a>\s*</p>', re.S)
_SPEC_END_MARKERS = ("닫기", "상품분류")
_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")
_CHIP_TOKEN_RE = re.compile(r"(\d{3,5}[A-Z0-9]*)")
_DGPU_RE = re.compile(r"\b(RTX|GTX|RX|ARC)\s*[AB]?\d{3,4}", re.I)
_RAM_GB_RE = re.compile(r"(\d+)\s*GB", re.I)
_RAM_MULT_RE = re.compile(r"[×xX]\s*(\d+)")
_RAM_TB_RE = re.compile(r"(\d+)\s*TB", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def cpu_key(text: str) -> str:
    """'i5-14400F', 'Ultra 5 225F', '라이젠5 5500GT' → '14400F' / '225F' / '5500GT' (비교용 칩 이름)."""
    tokens = _CHIP_TOKEN_RE.findall((text or "").upper())
    return tokens[-1] if tokens else ""


def gpu_key(text: str) -> str:
    """'RTX 4060 Ti' → 'RTX4060TI'. 외장 그래픽카드가 아니면 빈 문자열(내장 그래픽)."""
    upper = (text or "").upper()
    if not _DGPU_RE.search(upper):
        return ""
    return re.sub(r"[^A-Z0-9]", "", upper)


def _gpu_base(key: str) -> str:
    """'RTX4060TI' → 'RTX4060' (같은 계열인지 볼 때 쓴다)."""
    match = re.match(r"([A-Z]+\d{3,4})", key)
    return match.group(1) if match else key


def ram_total_gb(name: str, qty: int = 1) -> int:
    """컴퓨존 메모리 품명 '[16GB] … (×2)' 와 수량으로 총 용량(GB)을 구한다. 모르면 0."""
    text = name or ""
    bracket = re.search(r"\[(\d+)\s*GB\]", text, re.I)
    match = bracket or _RAM_GB_RE.search(text)
    if not match:
        return 0
    per = int(match.group(1))
    mult = _RAM_MULT_RE.search(text)
    count = int(mult.group(1)) if mult else 1
    return per * count * max(int(qty or 1), 1)


def _parse_spec(spec: str) -> dict[str, Any]:
    """다나와 사양 줄에서 CPU · 그래픽 · RAM · 저장장치를 꺼낸다.

    데스크탑: 'CPU / 그래픽 / 칩셋 / … / DDR5 / 32GB / M.2 / 1TB …'
    노트북:   '노트북 / 39.6cm(15.6인치) / 1.55kg / 인텔 / 코어i5-13세대 / i5-1335U (1.3GHz) / Iris Xe / 16GB / …'
    노트북(2026-10 개편): '… 노트북 / … / [CPU] 인텔 / 코어 울트라5 / 125H (4.5GHz) / 11TOPS /
                          [그래픽] 내장그래픽 / Arc / … / [구성] 8GB / 램 교체 : 가능 / 용량 : 256GB / …'
    """
    tokens = [t.strip() for t in spec.split(" / ") if t.strip()]
    if tokens and tokens[0].endswith("노트북"):
        cpu_idx = next((i for i, t in enumerate(tokens) if "GHz" in t and cpu_key(t)), -1)
        if cpu_idx >= 0:
            storage = next((t.split(":", 1)[1].strip() for t in tokens if t.startswith("용량")), "")
            gpu_idx = next((i for i, t in enumerate(tokens) if t.startswith("[그래픽]")), -1)
            ram_tok = next((t[len("[구성]"):].strip() for t in tokens if t.startswith("[구성]")), "")
            if gpu_idx >= 0:
                gpu = tokens[gpu_idx + 1] if gpu_idx + 1 < len(tokens) else ""
            else:
                gpu = tokens[cpu_idx + 1] if cpu_idx + 1 < len(tokens) else ""
                ram_tok = tokens[cpu_idx + 2] if cpu_idx + 2 < len(tokens) else ""
            ram_m = _RAM_GB_RE.fullmatch(ram_tok)
            return {"cpu": tokens[cpu_idx], "gpu": gpu, "ram_gb": int(ram_m.group(1)) if ram_m else 0, "storage": storage}
    cpu = tokens[0] if tokens else ""
    gpu = tokens[1] if len(tokens) > 1 else ""
    ram_gb = 0
    storage = ""
    for i, tok in enumerate(tokens):
        if re.fullmatch(r"DDR\d\w*", tok, re.I) and i + 1 < len(tokens):
            m = _RAM_GB_RE.fullmatch(tokens[i + 1])
            if m and not ram_gb:
                ram_gb = int(m.group(1))
        if tok in ("M.2", "SSD", "SATA") and i + 1 < len(tokens) and not storage:
            if _RAM_GB_RE.fullmatch(tokens[i + 1]) or _RAM_TB_RE.fullmatch(tokens[i + 1]):
                storage = tokens[i + 1]
    return {"cpu": cpu, "gpu": gpu, "ram_gb": ram_gb, "storage": storage}


def _is_pc_category(category: str, notebook: bool = False) -> bool:
    cat = category or ""
    if notebook:
        return "노트북" in cat
    return ("완제품" in cat or "데스크탑" in cat or "조립PC" in cat) and "노트북" not in cat


def _fetch_search_html(query: str) -> str:
    url = f"{SEARCH_URL}?{urllib.parse.urlencode({'query': query, 'tab': 'main'})}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.danawa.com/",
            "Accept-Language": "ko-KR,ko;q=0.9",
        },
    )
    last = ""
    for _ in range(FETCH_RETRIES):
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as res:
            body = res.read().decode("utf-8", "ignore")
            final_url = res.geturl()
        if "danawa.com" in final_url and _ITEM_START_RE.search(body):
            return body
        last = body
    return last


def _spec_text(block: str, name_end: int) -> str:
    """상품명 뒤 사양 줄. 접힌 사양의 '닫기' 버튼(없으면 상품분류) 앞까지."""
    rest = block[name_end:]
    cut = min((i for i in (rest.find(m) for m in _SPEC_END_MARKERS) if i >= 0), default=len(rest))
    text = _clean(re.sub(r"<(svg|script|style)[^>]*>.*?</\1>", " ", rest[:cut], flags=re.S))
    return re.sub(r"\s*:\s*", " : ", text)


def _parse_items(body: str, notebook: bool = False) -> list[dict[str, Any]]:
    starts = list(_ITEM_START_RE.finditer(body))
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, m in enumerate(starts):
        pcode = m.group(1)
        if pcode in seen:
            continue
        block = body[m.start(): starts[i + 1].start() if i + 1 < len(starts) else m.start() + 60000]
        category = _CATEGORY_RE.search(block)
        category_text = _clean(category.group(1)) if category else ""
        if category_text and not _is_pc_category(category_text, notebook):
            continue
        name = _NAME_RE.search(block)
        if not name:
            continue  # 광고 등 상품명이 없는 칸
        seen.add(pcode)
        spec_text = _spec_text(block, name.end())
        # 옵션이 여럿이면 첫 줄(대표 구성) 가격. 상품분류 뒤에서 찾는다.
        price = _PRICE_RE.search(block, category.end() if category else 0)
        image = _IMG_RE.search(block)
        img = html.unescape(image.group(1)) if image else ""
        if img.startswith("//"):
            img = "https:" + img
        items.append({
            "pcode": pcode,
            "name": _clean(name.group(1)),
            "url": PRODUCT_URL.format(pcode=pcode),
            "image": img,
            "price": int(price.group(1).replace(",", "")) if price else 0,
            "spec": spec_text,
            **_parse_spec(spec_text),
        })
    return items


def _search(query: str, notebook: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    key = f"{'nb' if notebook else 'pc'}:{query}"
    with _cache_lock:
        hit = _search_cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SEC:
            return hit[1]
    items = _parse_items(_fetch_search_html(query), notebook)
    with _cache_lock:
        _search_cache[key] = (now, items)
    return items


def _ram_from_model(model: str) -> int:
    """노트북 모델명 괄호 '(i5-1335U/8GB/256GB/FD)' 에서 메모리 용량. 저장장치(두 번째 GB)는 버린다."""
    match = re.search(r"\(([^)]*)\)", model or "")
    if not match:
        return 0
    for token in match.group(1).split("/"):
        m = _RAM_GB_RE.fullmatch(token.strip())
        if m and int(m.group(1)) <= 128:
            return int(m.group(1))
    return 0


def search_url(query: str) -> str:
    return f"{SEARCH_URL}?{urllib.parse.urlencode({'query': query, 'tab': 'main'})}"


def capacity_gb(text: str) -> int:
    """'1TB' → 1024, '512GB' → 512, '[256GB]' → 256. 없으면 0."""
    tb = _RAM_TB_RE.search(text or "")
    if tb:
        return int(tb.group(1)) * 1024
    gb = _RAM_GB_RE.search(text or "")
    return int(gb.group(1)) if gb else 0


def _ssd_from_model(model: str) -> int:
    """노트북 모델명 괄호 '(i5-1335U/8GB/256GB/FD)' 에서 저장장치 용량(두 번째 용량)."""
    match = re.search(r"\(([^)]*)\)", model or "")
    if not match:
        return 0
    caps = [capacity_gb(t) for t in match.group(1).split("/") if _RAM_GB_RE.fullmatch(t.strip()) or _RAM_TB_RE.fullmatch(t.strip())]
    return caps[1] if len(caps) > 1 else 0


def resolve_base(
    cpu: str = "",
    gpu: str = "",
    ram: str = "",
    ram_qty: int = 1,
    ssd: str = "",
    ssd_qty: int = 1,
    model: str = "",
    store: str = "다나와",
) -> dict[str, Any]:
    """컴퓨존 견적의 부품 품명(또는 노트북 모델명)에서 비교 기준 CPU · 그래픽 · RAM · SSD 를 뽑는다."""
    cpu_s = short_cpu_name(cpu)
    gpu_s = short_gpu_name(gpu)
    # 노트북 견적(금액대로 찾기 · 노트북)은 부품 목록 없이 모델명에 '(CPU/RAM/SSD)' 만 있다.
    notebook = not (cpu or "").strip()
    if not cpu_s:
        fb_cpu, fb_gpu = _parts_from_model_paren(model)
        cpu_s = short_cpu_name(fb_cpu) or fb_cpu
        gpu_s = gpu_s or short_gpu_name(fb_gpu)
    cpu_k = cpu_key(cpu_s)
    if not cpu_k:
        raise ValueError(f"이 견적에서 CPU 모델을 찾지 못해 {store} 비교를 할 수 없습니다.")
    ram_gb = ram_total_gb(ram, ram_qty) or (_ram_from_model(model) if notebook else 0)
    ssd_gb = capacity_gb(ssd) * max(int(ssd_qty or 1), 1) if ssd else (_ssd_from_model(model) if notebook else 0)
    return {
        "cpu": cpu_s,
        "gpu": gpu_s,
        "cpu_key": cpu_k,
        "gpu_key": gpu_key(gpu_s),
        "ram_gb": ram_gb,
        "ssd_gb": ssd_gb,
        "notebook": notebook,
    }


def _size_match(want: int, have: int) -> str:
    if not want or not have:
        return ""
    if want == have:
        return "exact"
    return "near" if abs(math.log2(have / want)) <= 1 else ""


def score_item(item: dict[str, Any], base: dict[str, Any], price: int) -> dict[str, Any]:
    """CPU > 그래픽카드 > RAM > SSD 순으로 가중치를 두고, 가격이 가까우면 조금 더 준다."""
    cpu_k, gpu_k = base["cpu_key"], base["gpu_key"]
    item_cpu = cpu_key(item.get("cpu", ""))
    item_gpu = gpu_key(item.get("gpu", ""))
    cpu_match = "exact" if item_cpu == cpu_k else (
        "near" if item_cpu and re.sub(r"\D", "", item_cpu) == re.sub(r"\D", "", cpu_k) else "")
    if gpu_k:
        gpu_match = "exact" if item_gpu == gpu_k else (
            "near" if item_gpu and _gpu_base(item_gpu) == _gpu_base(gpu_k) else "")
    else:
        # 둘 다 내장 그래픽. 상품명에 CPU 도 없으면(쿠팡의 '라이젠5 5000 시리즈' 등) 사양을 모르는 것이므로 일치로 보지 않는다.
        gpu_match = "exact" if not item_gpu and item_cpu else ""
    ram_match = _size_match(base["ram_gb"], item.get("ram_gb", 0))
    ssd_match = _size_match(base["ssd_gb"], item.get("ssd_gb", 0))

    score = {"exact": 40, "near": 20, "": 0}[cpu_match]
    score += {"exact": 30, "near": 12, "": 0}[gpu_match]
    score += {"exact": 15, "near": 6, "": 0}[ram_match]
    score += {"exact": 10, "near": 4, "": 0}[ssd_match]
    item_price = item.get("price") or 0
    if price and item_price:
        score += 5 * max(0.0, 1 - abs(item_price - price) / price)
    return {
        **item,
        "score": round(score, 1),
        "match": {"cpu": cpu_match, "gpu": gpu_match, "ram": ram_match, "ssd": ssd_match},
    }


def base_summary(base: dict[str, Any], price: int) -> dict[str, Any]:
    return {
        "cpu": base["cpu"],
        "gpu": base["gpu"] or "내장 그래픽",
        "ram_gb": base["ram_gb"],
        "ssd_gb": base["ssd_gb"],
        "price": price,
        "notebook": base["notebook"],
    }


def find_similar(
    cpu: str = "",
    gpu: str = "",
    ram: str = "",
    ram_qty: int = 1,
    ssd: str = "",
    ssd_qty: int = 1,
    model: str = "",
    price: int = 0,
) -> dict[str, Any]:
    """컴퓨존 견적과 CPU · 그래픽카드 · RAM · SSD 가 가장 비슷한 다나와 완제품 PC를 점수순으로 돌려준다."""
    base = resolve_base(cpu, gpu, ram, ram_qty, ssd, ssd_qty, model, store="다나와")
    cpu_k, gpu_k, gpu_s, notebook = base["cpu_key"], base["gpu_key"], base["gpu"], base["notebook"]

    if notebook:
        query = f"{cpu_k} {gpu_s} 노트북" if gpu_k else f"{cpu_k} 노트북"
    else:
        query = f"{cpu_k} {gpu_s}" if gpu_k else f"{cpu_k} 조립PC"
    items = _search(query, notebook)
    used_query = query
    if len(items) < MIN_RESULTS_BEFORE_FALLBACK and gpu_k:
        # 같은 CPU+그래픽카드 조합이 적으면 CPU 만으로 넓혀 찾는다
        fallback = f"{cpu_k} 노트북" if notebook else f"{cpu_k} 조립PC"
        seen = {it["pcode"] for it in items}
        items = items + [it for it in _search(fallback, notebook) if it["pcode"] not in seen]
        used_query = f"{query} + {fallback}"

    for it in items:
        it["ssd_gb"] = capacity_gb(it.get("storage", ""))
    scored = [score_item(it, base, price) for it in items]
    scored.sort(key=lambda it: (-it["score"], it["price"] or 10**12))
    return {
        "query": used_query,
        "search_url": search_url(query),
        "base": base_summary(base, price),
        "items": scored[:MAX_ITEMS],
    }
