# -*- coding: utf-8 -*-
"""쿠팡 검색 링크: 컴퓨존 견적의 CPU · 그래픽카드로 쿠팡 일반 검색 페이지 주소를 만든다.

쿠팡 파트너스 API 는 쓰지 않는다(키 없음). 카드의 🛒 쿠팡 버튼이 이 주소를 새 창으로 연다.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from app.danawa_search import resolve_base

SEARCH_PAGE = "https://www.coupang.com/np/search?component=&q={query}&channel=user"


def search_page_url(keyword: str) -> str:
    return SEARCH_PAGE.format(query=urllib.parse.quote(keyword.strip(), safe=""))


def search_link(
    cpu: str = "",
    gpu: str = "",
    ram: str = "",
    ram_qty: int = 1,
    ssd: str = "",
    ssd_qty: int = 1,
    model: str = "",
) -> dict[str, Any]:
    """'조립PC 9800X3D RTX 5070 Ti' / '노트북 125H' 같은 사양 검색어와 쿠팡 검색 주소."""
    base = resolve_base(cpu, gpu, ram, ram_qty, ssd, ssd_qty, model, store="쿠팡")
    kind = "노트북" if base["notebook"] else "조립PC"
    keyword = f"{kind} {base['cpu']} {base['gpu']}" if base["gpu_key"] else f"{kind} {base['cpu']}"
    keyword = re.sub(r"\s+", " ", keyword).strip()
    return {"query": keyword, "search_url": search_page_url(keyword)}


__all__ = ["search_link", "search_page_url"]
