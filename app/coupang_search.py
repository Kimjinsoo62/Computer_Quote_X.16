# -*- coding: utf-8 -*-
"""쿠팡 파트너스 상품 검색 API 로 컴퓨존 견적과 사양(CPU · 그래픽카드 · RAM · SSD)이 비슷한 PC를 찾는다.

API 키는 저장소에 올리지 않는 coupang_config.json(access_key, secret_key)에 둔다.
키가 없는 PC 에서는 NotConfigured 를 던지고, 화면은 일반 쿠팡 검색 페이지를 연다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.danawa_search import base_summary, resolve_base, score_item
from app.used_market import short_cpu_name, short_gpu_name

CONFIG_FILE = Path(__file__).resolve().parent.parent / "coupang_config.json"
API_DOMAIN = "https://api-gateway.coupang.com"
SEARCH_PATH = "/v2/providers/affiliate_open_api/apis/openapi/products/search"
SEARCH_PAGE = "https://www.coupang.com/np/search?component=&q={query}&channel=user"
FETCH_TIMEOUT_SEC = 15
SEARCH_LIMIT = 10  # 쿠팡 파트너스 검색 API 한 번에 최대 10개
CACHE_TTL_SEC = 10 * 60
# 쿠팡 파트너스 약관상 링크를 보여 줄 때 함께 밝혀야 하는 문구
DISCLOSURE = "이 창의 쿠팡 링크는 쿠팡 파트너스 링크로, 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있습니다."

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

_RAM_SIZES = {4, 8, 12, 16, 24, 32, 48, 64, 96}
_CAP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(TB|GB)", re.I)
# 그래픽카드 메모리(예: RTX 4060 8GB)를 시스템 RAM 으로 잘못 읽지 않도록 지운다
_VRAM_RE = re.compile(r"((?:RTX|GTX|RX|ARC)\s*[AB]?\d{3,4}[^,/()]*?)\s*\d+\s*GB", re.I)


class NotConfigured(Exception):
    """coupang_config.json 에 파트너스 키가 없다. search_url 은 대신 열 일반 쿠팡 검색 주소."""

    search_url = ""


def _load_keys() -> tuple[str, str]:
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cfg = {}
    access, secret = str(cfg.get("access_key") or "").strip(), str(cfg.get("secret_key") or "").strip()
    if not (access and secret):
        raise NotConfigured("쿠팡 파트너스 API 키가 설정되어 있지 않습니다.")
    return access, secret


def search_page_url(keyword: str) -> str:
    return SEARCH_PAGE.format(query=urllib.parse.quote(keyword.strip(), safe=""))


def _api_search(keyword: str) -> list[dict[str, Any]]:
    access, secret = _load_keys()
    query = urllib.parse.urlencode({"keyword": keyword, "limit": SEARCH_LIMIT})
    signed = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
    signature = hmac.new(
        secret.encode("utf-8"), (signed + "GET" + SEARCH_PATH + query).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    req = urllib.request.Request(
        f"{API_DOMAIN}{SEARCH_PATH}?{query}",
        headers={
            "Authorization": f"CEA algorithm=HmacSHA256, access-key={access}, signed-date={signed}, signature={signature}",
            "Content-Type": "application/json;charset=UTF-8",
        },
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as res:
        body = json.loads(res.read().decode("utf-8"))
    if str(body.get("rCode")) != "0":
        raise RuntimeError(f"쿠팡 검색 실패: {body.get('rMessage') or body.get('rCode')}")
    return list((body.get("data") or {}).get("productData") or [])


def parse_product_name(name: str) -> dict[str, Any]:
    """'…, 512GB, 16GB, Radeon Vega 7, [AMD] 라이젠5 5500GT' 같은 상품명에서 CPU · 그래픽 · RAM · SSD 를 읽는다."""
    text = name or ""
    cpu = short_cpu_name(text)
    gpu = short_gpu_name(text)
    caps = _CAP_RE.findall(_VRAM_RE.sub(r"\1", text))
    ram_gb = 0
    ssd_gb = 0
    for num, unit in caps:
        gb = int(float(num) * (1024 if unit.upper() == "TB" else 1))
        if not ram_gb and unit.upper() == "GB" and gb in _RAM_SIZES:
            ram_gb = gb
        elif not ssd_gb and gb >= 120:
            ssd_gb = gb
    return {"cpu": cpu, "gpu": gpu, "ram_gb": ram_gb, "ssd_gb": ssd_gb}


def _search(keyword: str) -> list[dict[str, Any]]:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(keyword)
        if hit and now - hit[0] < CACHE_TTL_SEC:
            return hit[1]
    items = []
    for p in _api_search(keyword):
        name = str(p.get("productName") or "")
        spec = parse_product_name(name)
        ssd_text = f"{spec['ssd_gb'] // 1024}TB" if spec["ssd_gb"] >= 1024 else (f"{spec['ssd_gb']}GB" if spec["ssd_gb"] else "")
        items.append({
            "pcode": str(p.get("productId") or ""),
            "name": name,
            "url": str(p.get("productUrl") or ""),
            "image": str(p.get("productImage") or ""),
            "price": int(float(p.get("productPrice") or 0)),
            "spec": " / ".join(x for x in (
                spec["cpu"], spec["gpu"] or "내장 그래픽",
                f"RAM {spec['ram_gb']}GB" if spec["ram_gb"] else "",
                f"SSD {ssd_text}" if ssd_text else "",
            ) if x),
            "rocket": bool(p.get("isRocket")),
            **spec,
        })
    with _cache_lock:
        _cache[keyword] = (now, items)
    return items


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
    """컴퓨존 견적과 CPU · 그래픽카드 · RAM · SSD 가 비슷한 쿠팡 상품을 점수순으로 돌려준다."""
    base = resolve_base(cpu, gpu, ram, ram_qty, ssd, ssd_qty, model, store="쿠팡")
    cpu_s, gpu_s, gpu_k, notebook = base["cpu"], base["gpu"], base["gpu_key"], base["notebook"]
    kind = "노트북" if notebook else "조립PC"
    keyword = f"{kind} {cpu_s} {gpu_s}" if gpu_k else f"{kind} {cpu_s}"
    keyword = re.sub(r"\s+", " ", keyword).strip()
    try:
        items = _search(keyword)
    except NotConfigured as e:
        e.search_url = search_page_url(keyword)
        raise
    scored = [score_item(it, base, price) for it in items]
    scored.sort(key=lambda it: (-it["score"], it["price"] or 10**12))
    return {
        "query": keyword,
        "search_url": search_page_url(keyword),
        "base": base_summary(base, price),
        "items": scored,
        "disclosure": DISCLOSURE,
    }


__all__ = ["NotConfigured", "find_similar", "parse_product_name", "search_page_url"]
