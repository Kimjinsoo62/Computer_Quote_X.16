# -*- coding: utf-8 -*-
import json
import socket
from dataclasses import replace
from datetime import date
import urllib.error
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import (
    APPLE_CATEGORIES,
    ASSEMBLED_CATEGORIES,
    CONSUMER_MARGIN_PERCENT,
    GAMING_PC_URL,
    PRICE_SEARCH_LIMIT,
    QUOTE_DISCLAIMER,
)
from app.excel_export import FORM_LABELS, build_excel
from app.image_lookup import find_representative_image
from app.apple_scraper import fetch_apple_cto_live, fetch_apple_cto_options
from app import coupang_search
from app.danawa_search import find_similar as danawa_find_similar
from app.used_market import used_market_links
from app.quote_info import load_quote_info, reset_quote_info, save_quote_info
from app.quote_engine import (
    QuotePart,
    apply_manwon_rounding,
    apply_quote_margin,
    fetch_apple_quotes,
    fetch_assembled_quotes,
    fetch_assembled_quotes_by_price,
    fetch_notebook_quotes_by_price,
    fetch_game_pc_meta,
    fetch_game_pc_quotes,
    fetch_today_quotes,
    get_logic_steps,
    is_mac_apple_cat,
    margin_kind_label,
    today_quotes_to_dict,
)
from app.calc_window import close_calc_window, open_calc_window
from app import customer_book, quote_archive
from app.shutdown_service import schedule_full_shutdown
from app.update_service import apply_update, check_update, schedule_restart
from app.task_report import (
    backup_all_task_reports,
    build_task_report_excel,
    choose_backup_dir_dialog,
    create_task_report,
    delete_task_report,
    get_backup_dir,
    get_task_report,
    load_task_reports,
    open_in_explorer,
    set_backup_dir,
    update_task_report,
)

BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(title="AI PC & Apple Store 견적 추출기", version="X.32")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def get_lan_ip() -> str:
    """실행 중인 컴퓨터의 로컬 네트워크(LAN) IPv4 주소 반환"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


@app.get("/api/server-info")
def api_server_info():
    """로컬 및 네트워크 접근 URL 반환"""
    lan_ip = get_lan_ip()
    port = 8090
    return {
        "local_url": f"http://127.0.0.1:{port}/",
        "network_url": f"http://{lan_ip}:{port}/",
        "lan_ip": lan_ip,
        "port": port,
    }


def _extra_label(
    include_monitor: bool,
    include_keyboard: bool,
    include_ram32: bool = False,
    include_setup: bool = False,
    is_apple: bool = False,
    apple_cat: str = "",
) -> str:
    parts: list[str] = []
    if is_apple:
        if is_mac_apple_cat(apple_cat) and not include_setup:
            parts.append("초기세팅비제외")
        return ("_" + "_".join(parts)) if parts else ""
    if not include_monitor:
        parts.append("모니터제외")
    if not include_keyboard:
        parts.append("무선키보드제외")
    if not include_setup:
        parts.append("초기세팅비제외")
    if include_ram32:
        parts.append("메모리32GB")
    return ("_" + "_".join(parts)) if parts else ""


def _margin_filename_label(margin: int, is_apple: bool) -> str:
    if is_apple:
        return ""
    if margin == CONSUMER_MARGIN_PERCENT:
        return "_소비자견적"
    if margin == 0:
        return "_원가견적"
    return f"_마진{margin}"


# 카드 한 건을 서식 하나로 뽑을 때의 시트 이름(공백 3칸 포함, 폼1 제목과 같다).
SINGLE_SHEET_TITLE = "견   적   서"


def _parse_forms(forms: str) -> list[int]:
    """'1,3' → [1, 3]. 비어 있으면 [] (전체). 1~8 밖의 값은 400."""
    if not (forms or "").strip():
        return []
    picked: list[int] = []
    for token in forms.split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit() or int(token) not in FORM_LABELS:
            raise HTTPException(status_code=400, detail=f"서식 번호가 올바르지 않습니다: {token}")
        n = int(token)
        if n not in picked:
            picked.append(n)
    return picked


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    lan_ip = get_lan_ip()
    port = 8090
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "today": date.today().isoformat(),
            "logic_steps": get_logic_steps(),
            "quote_disclaimer": QUOTE_DISCLAIMER,
            "apple_categories": APPLE_CATEGORIES,
            "lan_ip": lan_ip,
            "port": port,
            "network_url": f"http://{lan_ip}:{port}/",
            "local_url": f"http://127.0.0.1:{port}/",
        },
    )


@app.get("/manual", response_class=HTMLResponse)
def manual(request: Request):
    """사용설명서 — 상단 메뉴에서 연다."""
    return templates.TemplateResponse(request, "사용설명서.html", {})


@app.get("/calc", response_class=HTMLResponse)
def calc_page(request: Request):
    """독립 창으로 띄우는 계산기. 메인 견적 창 밖으로 옮길 수 있다."""
    return templates.TemplateResponse(request, "calc.html", {})


@app.post("/api/calc/open")
def api_calc_open(request: Request, payload: dict = Body(default={})):
    """견적 --app 창과 별도 프로세스로 계산기를 연다. 모든 버튼이 보이게 크기를 고정한다."""
    base = str(request.base_url).rstrip("/")
    result = open_calc_window(
        f"{base}/calc",
        left=int(payload.get("left") or 80),
        top=int(payload.get("top") or 80),
        width=int(payload.get("width") or 280),
        height=int(payload.get("height") or 540),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail="계산기 창을 열 브라우저를 찾지 못했습니다.")
    return result


@app.get("/api/logic")
def api_logic():
    return {"steps": get_logic_steps()}



@app.get("/api/used/links")
def api_used_links(q: str = "", cpu: str = "", gpu: str = ""):
    model = (q or "").strip()
    cpu_name = (cpu or "").strip()
    gpu_name = (gpu or "").strip()
    if not (model or cpu_name or gpu_name):
        raise HTTPException(status_code=400, detail="검색어(q) 또는 CPU/그래픽카드가 필요합니다.")
    try:
        return used_market_links(model, cpu=cpu_name, gpu=gpu_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e





@app.get("/api/danawa/similar")
def api_danawa_similar(
    cpu: str = "",
    gpu: str = "",
    ram: str = "",
    ram_qty: int = 1,
    ssd: str = "",
    ssd_qty: int = 1,
    model: str = "",
    price: int = 0,
):
    """컴퓨존 견적의 CPU · 그래픽카드 · RAM · SSD 기준으로 다나와에서 가장 비슷한 완제품 PC 목록"""
    try:
        return danawa_find_similar(
            cpu=cpu, gpu=gpu, ram=ram, ram_qty=ram_qty, ssd=ssd, ssd_qty=ssd_qty, model=model, price=price
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HTTPException(status_code=502, detail=f"다나와 검색에 실패했습니다: {e}") from e


@app.get("/api/coupang/similar")
def api_coupang_similar(
    cpu: str = "",
    gpu: str = "",
    ram: str = "",
    ram_qty: int = 1,
    ssd: str = "",
    ssd_qty: int = 1,
    model: str = "",
    price: int = 0,
):
    """컴퓨존 견적의 CPU · 그래픽카드 · RAM · SSD 기준으로 쿠팡(파트너스 검색 API)에서 비슷한 PC 목록.

    파트너스 키가 없는 PC 는 503 과 함께 일반 쿠팡 검색 주소를 돌려준다(화면이 그 주소를 연다).
    """
    try:
        return coupang_search.find_similar(
            cpu=cpu, gpu=gpu, ram=ram, ram_qty=ram_qty, ssd=ssd, ssd_qty=ssd_qty, model=model, price=price
        )
    except coupang_search.NotConfigured as e:
        raise HTTPException(status_code=503, detail={"message": str(e), "search_url": e.search_url}) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=f"쿠팡 검색에 실패했습니다: {e}") from e


@app.get("/api/update/check")
def api_update_check():
    """GitHub 원격 저장소에 새 커밋이 있는지 확인"""
    return check_update()


@app.post("/api/update/apply")
def api_update_apply():
    """승인 후 git pull 로 프로젝트 갱신"""
    return apply_update()


@app.post("/api/update/restart")
def api_update_restart():
    """의존성 확인 후 서버 재시작 (update.bat restart)"""
    return schedule_restart()


@app.post("/api/shutdown")
def api_shutdown():
    """백엔드 종료 · 포트 정리 · 캐시 삭제 · 앱 브라우저 창 닫기"""
    schedule_full_shutdown(delay_sec=0.5)
    return {
        "ok": True,
        "message": "서버 종료를 시작했습니다. 포트 정리·캐시 삭제·브라우저 닫기를 진행합니다.",
    }


@app.get("/api/today")
def api_today(monitor: int = 0, keyboard: int = 0, ram32: int = 0, setup: int = 0):
    result = fetch_today_quotes(
        include_monitor=bool(monitor),
        include_keyboard=bool(keyboard),
        include_ram32=bool(ram32),
        include_setup=bool(setup),
    )
    return today_quotes_to_dict(result)


@app.get("/api/today/{tier_key}")
def api_today_tier(
    tier_key: str, monitor: int = 0, keyboard: int = 0, ram32: int = 0, setup: int = 0
):
    try:
        result = fetch_today_quotes(
            tier_key=tier_key,
            include_monitor=bool(monitor),
            include_keyboard=bool(keyboard),
            include_ram32=bool(ram32),
            include_setup=bool(setup),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return today_quotes_to_dict(result)


@app.get("/api/quote-info")
def api_quote_info_get():
    return load_quote_info()


@app.post("/api/quote-info")
def api_quote_info_save(info: dict = Body(...)):
    return save_quote_info(info)


@app.delete("/api/quote-info")
def api_quote_info_delete():
    return reset_quote_info()


# 거래처 목록 (customers.json) — [설정관리 → 견적정보] 탭
@app.get("/api/customers")
def api_customers_list():
    return {"customers": customer_book.list_customers()}


@app.post("/api/customers")
def api_customers_save(payload: dict = Body(...)):
    try:
        return {"customer": customer_book.save_customer(payload)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/customers/{customer_id}")
def api_customers_delete(customer_id: str):
    if not customer_book.delete_customer(customer_id):
        raise HTTPException(status_code=404, detail="거래처를 찾을 수 없습니다.")
    return {"ok": True}


@app.get("/api/category/{cat_key}")
def api_category(cat_key: str, monitor: int = 0, keyboard: int = 0, setup: int = 0):
    # 구버전 UI가 /api/category/game-pc 로 호출해도 404 대신 위자드 유도
    if cat_key in ("game-pc", "game"):
        return {
            "quote_date": date.today().isoformat(),
            "event_url": GAMING_PC_URL,
            "event_title": "게임으로PC찾기",
            "series": "게임으로PC찾기",
            "disclaimer": QUOTE_DISCLAIMER,
            "logic_steps": get_logic_steps(),
            "quotes": [],
            "needs_game_wizard": True,
        }
    if cat_key in ("price", "price-search"):
        return {
            "quote_date": date.today().isoformat(),
            "event_url": "https://www.compuzone.co.kr/product/Allassembled.htm",
            "event_title": "금액대로 찾기",
            "series": "금액대로 찾기",
            "disclaimer": QUOTE_DISCLAIMER,
            "logic_steps": get_logic_steps(),
            "quotes": [],
            "needs_price_wizard": True,
        }
    if cat_key not in ASSEMBLED_CATEGORIES:
        raise HTTPException(status_code=404, detail=f"Unknown category: {cat_key}")
    result = fetch_assembled_quotes(
        cat_key,
        include_monitor=bool(monitor),
        include_keyboard=bool(keyboard),
        include_setup=bool(setup),
    )
    return today_quotes_to_dict(result)


@app.get("/api/game-pc/meta")
def api_game_pc_meta():
    try:
        return fetch_game_pc_meta()
    except (RuntimeError, OSError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post("/api/game-pc/recommend")
def api_game_pc_recommend(payload: dict = Body(...)):
    games = payload.get("games") or []
    resolution = payload.get("resolution") or ""
    titles = payload.get("titles") or []
    monitor = bool(payload.get("monitor", 0))
    keyboard = bool(payload.get("keyboard", 0))
    setup = bool(payload.get("setup", 0))
    try:
        result = fetch_game_pc_quotes(
            game_ids=[str(g) for g in games],
            resolution=str(resolution),
            include_monitor=monitor,
            include_keyboard=keyboard,
            include_setup=setup,
            game_titles=[str(t) for t in titles],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (OSError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    data = today_quotes_to_dict(result)
    data["games"] = [str(g) for g in games]
    data["titles"] = [str(t) for t in titles]
    data["resolution"] = str(resolution).upper()
    return data


@app.post("/api/price-search")
def api_price_search(payload: dict = Body(...)):
    try:
        min_price = int(payload.get("min_price") or 0)
        max_price = int(payload.get("max_price") or 0)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="최저가와 최고가는 숫자로 입력해주세요.") from e
    
    target_type = payload.get("target_type", "assembled")
    monitor = bool(payload.get("monitor", 0))
    keyboard = bool(payload.get("keyboard", 0))
    setup = bool(payload.get("setup", 0))
    try:
        if target_type == "notebook":
            result, meta = fetch_notebook_quotes_by_price(
                min_price,
                max_price,
                limit=PRICE_SEARCH_LIMIT,
            )
        else:
            result, meta = fetch_assembled_quotes_by_price(
                min_price,
                max_price,
                include_monitor=monitor,
                include_keyboard=keyboard,
                include_setup=setup,
                limit=PRICE_SEARCH_LIMIT,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (OSError, TimeoutError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    data = today_quotes_to_dict(result)
    data.update(meta)
    return data


@app.get("/api/apple/{cat_key}")
def api_apple(cat_key: str, monitor: int = 0, keyboard: int = 0, setup: int = 0, refresh: int = 0):
    try:
        result = fetch_apple_quotes(
            cat_key,
            include_monitor=bool(monitor),
            include_setup=bool(setup),
            force_refresh=bool(refresh),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return today_quotes_to_dict(result)


@app.get("/api/apple/refresh")
@app.post("/api/apple/refresh")
def api_apple_refresh(cat_key: str = "all"):
    """Apple 공식몰 실시간 크롤링 강제 갱신: 기존 캐시 및 이전 데이터를 완전 삭제하고 공홈 실시간 데이터로 100% 교체"""
    from app.apple_scraper import clear_apple_catalog_cache
    clear_apple_catalog_cache(cat_key if cat_key != "all" else None)
    result = fetch_apple_quotes(cat_key, force_refresh=True)
    return {
        "success": True,
        "message": "Apple 공식몰 최신 데이터로 완전 갱신 완료",
        "data": today_quotes_to_dict(result),
    }


@app.get("/api/image")
def api_image(url: str, redirect: int = 0):
    """카드 원본 페이지의 대표 이미지. redirect=1 이면 이미지로 바로 이동한다([🖼️ 이미지] 버튼)."""
    try:
        image = find_representative_image(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if redirect:
        if not image:
            return HTMLResponse(
                "<meta charset='utf-8'><p style='font-family:sans-serif;font-size:10pt'>"
                "원본 페이지에서 대표 이미지를 찾지 못했습니다.</p>",
                status_code=404,
            )
        return RedirectResponse(image)
    return {"url": url, "image": image}


@app.get("/api/apple/cto/{tier_key}")
def api_apple_cto(tier_key: str, sel: str = "", changed: str = ""):
    """Mac 은 공식몰 구성하기 API 로 실시간 옵션·총액을(live), 그 외는 구매 페이지 비교 옵션을 준다.
    sel: 현재 선택 {차원: 값} JSON. 바뀔 때마다 다시 호출해 정확한 총액을 받는다."""
    selections: dict = {}
    if sel:
        try:
            parsed = json.loads(sel)
            if isinstance(parsed, dict):
                selections = {str(k): str(v) for k, v in parsed.items()}
        except ValueError:
            selections = {}
    live = fetch_apple_cto_live(tier_key, selections, changed)
    if live:
        return {
            "tier_key": tier_key,
            "live": True,
            "options": live["groups"],
            "total": live["total"],
            "base_total": live["base_total"],
            "selected": live["selected"],
            "parts": live["parts"],
            "notice": live.get("notice") or "",
            "source": "apple_cto_api",
        }
    options = fetch_apple_cto_options(tier_key)
    return {
        "tier_key": tier_key,
        "live": False,
        "options": options,
        "source": "apple_official_catalog",
    }


# CTO 항목 → 같은 사양을 나타내는 기본 사양 행 (static/js/app.js CTO_BASE_ROW 와 같게 유지)
CTO_BASE_ROW = {"통합 메모리": "메모리", "저장장치": "SSD", "프로세서": "CPU/SoC"}


def _apply_cto_upgrades(result, cto_json: str):
    """화면에서 적용한 CTO 추가 품목({tier_key: [{category, name, amount}]})을 견적에 더한다."""
    try:
        upgrades = json.loads(cto_json)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="CTO 정보 형식이 올바르지 않습니다.") from e
    if not isinstance(upgrades, dict):
        raise HTTPException(status_code=400, detail="CTO 정보 형식이 올바르지 않습니다.")

    quotes = []
    for q in result.quotes:
        added = upgrades.get(q.tier_key)
        if not isinstance(added, list) or not added:
            quotes.append(q)
            continue
        parts = [replace(p) for p in q.parts]
        delta = 0
        for item in added[:20]:
            if not isinstance(item, dict):
                continue
            try:
                amount = int(item.get("amount") or 0)
            except (TypeError, ValueError):
                continue
            category = str(item.get("category") or "CTO")[:40]
            name = str(item.get("name") or "CTO 업그레이드")[:120]
            delta += amount
            # 메모리·SSD·칩처럼 기본 사양 행이 있으면 그 행을 업그레이드 사양으로 바꾸고 금액을 더한다.
            base_row = next((p for p in parts if p.category == CTO_BASE_ROW.get(category)), None)
            if base_row:
                base_row.name = name
                base_row.amount += amount
                base_row.unit_price = base_row.amount
                continue
            # 금액 0 행도 넣는다: 칩 변경으로 함께 바뀐 구성을 보여 준다.
            parts.append(
                QuotePart(no=0, category=category, name=name, qty=1, unit_price=amount, amount=amount)
            )
        for i, p in enumerate(parts, 1):
            p.no = i
        quotes.append(
            replace(
                q,
                parts=parts,
                total=q.total + delta,
                parts_subtotal=q.parts_subtotal + delta,
                price_source="apple_cto",
            )
        )
    return replace(result, quotes=quotes)


@app.get("/api/download/excel")
def download_excel(
    cat: str = "ai",
    monitor: int = 0,
    keyboard: int = 0,
    ram32: int = 0,
    setup: int = 0,
    unitprice: int = 1,
    margin: int = CONSUMER_MARGIN_PERCENT,
    games: str = "",
    resolution: str = "",
    titles: str = "",
    min_price: int = 0,
    max_price: int = 0,
    price_type: str = "assembled",
    index: int = -1,
    cto: str = "",
    forms: str = "",
    archive: int = 0,
):
    """카드 한 건(index)의 견적서를 내려준다. 목록 전체 비교 파일(전체 비교 Excel)은 없앴다(index 필수).
    archive=1 이면 만든 파일을 견적서 보관함(quote_archive/)에도 저장한다.
    파일명은 "{거래처명}_{구분}_{모델}_..." 로 시작해 보관함·탐색기에서 거래처별로 정렬된다.

    forms 를 주면(예: "3" 또는 "1,3") 견적요약 없이 해당 폼 시트만 담는다. 화면은 서식마다 따로 호출해
    파일을 하나씩 받는다. 서식 하나면 시트 이름은 "견   적   서" 가 된다.
    """
    if index < 0:
        raise HTTPException(status_code=400, detail="견적 카드(index)를 지정해야 합니다.")
    selected_forms = _parse_forms(forms)
    include_monitor = bool(monitor)
    include_keyboard = bool(keyboard)
    include_ram32 = bool(ram32)
    include_setup = bool(setup)
    hide_prices = not bool(unitprice)
    is_apple = cat.startswith("apple_") or cat == "apple"
    is_notebook = cat in ("price", "price-search") and price_type == "notebook"
    try:
        margin_pct = int(margin)
    except (TypeError, ValueError):
        margin_pct = CONSUMER_MARGIN_PERCENT
    if margin_pct not in (CONSUMER_MARGIN_PERCENT, 25, 20, 15, 10, 0):
        margin_pct = CONSUMER_MARGIN_PERCENT
    if cat == "ai":
        result = fetch_today_quotes(
            include_monitor=include_monitor,
            include_keyboard=include_keyboard,
            include_ram32=include_ram32,
            include_setup=include_setup,
        )
        kr_label = "오늘의견적"
    elif cat in ASSEMBLED_CATEGORIES:
        result = fetch_assembled_quotes(
            cat,
            include_monitor=include_monitor,
            include_keyboard=include_keyboard,
            include_setup=include_setup,
        )
        kr_label = f"{ASSEMBLED_CATEGORIES[cat]['label']}_견적"
    elif cat in ("game", "game-pc"):
        game_ids = [g for g in games.split("|") if g.strip()]
        title_list = [t for t in titles.split("|") if t.strip()]
        try:
            result = fetch_game_pc_quotes(
                game_ids=game_ids,
                resolution=resolution,
                include_monitor=include_monitor,
                include_keyboard=include_keyboard,
                include_setup=include_setup,
                game_titles=title_list,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        kr_label = f"게임으로PC찾기_{resolution or 'PC'}_견적"
    elif cat in ("price", "price-search"):
        try:
            if is_notebook:
                result, _meta = fetch_notebook_quotes_by_price(
                    min_price,
                    max_price,
                    limit=PRICE_SEARCH_LIMIT,
                )
            else:
                result, _meta = fetch_assembled_quotes_by_price(
                    min_price,
                    max_price,
                    include_monitor=include_monitor,
                    include_keyboard=include_keyboard,
                    include_setup=include_setup,
                    limit=PRICE_SEARCH_LIMIT,
                )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        kind = "노트북금액대로찾기" if is_notebook else "금액대로찾기"
        kr_label = f"{kind}_{min_price}_{max_price}_견적"
    elif cat.startswith("apple_") or cat == "apple":
        apple_sub = cat.replace("apple_", "") if cat != "apple" else "macbook"
        try:
            result = fetch_apple_quotes(
                apple_sub,
                include_monitor=include_monitor,
                include_setup=include_setup,
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        cat_name = APPLE_CATEGORIES.get(apple_sub, {}).get("label", "애플스토어")
        kr_label = f"Apple_{cat_name}_견적"
    else:
        raise HTTPException(status_code=404, detail=f"Unknown category: {cat}")

    if is_apple and cto:
        result = _apply_cto_upgrades(result, cto)

    apple_sub = cat.replace("apple_", "") if is_apple and cat != "apple" else ("macbook" if cat == "apple" else "")
    # 노트북은 모니터·키보드·세팅비·메모리 옵션이 적용되지 않으므로 파일명에도 붙이지 않는다.
    if not is_notebook:
        kr_label += _extra_label(
            include_monitor,
            include_keyboard,
            include_ram32,
            include_setup=include_setup,
            is_apple=is_apple,
            apple_cat=apple_sub,
        )
    result = (
        result
        if is_apple
        else apply_quote_margin(result, margin_pct)
    )
    if not is_apple:
        kr_label += _margin_filename_label(margin_pct, is_apple=False)
    if hide_prices:
        kr_label += "_단가미표시"

    # 선택한 카드 하나만 남긴다.
    if index >= len(result.quotes):
        raise HTTPException(
            status_code=404, detail=f"{index}번 견적 항목이 없습니다."
        )
    picked = result.quotes[index]
    result = replace(result, quotes=[picked])
    # tab_label 만 쓰면 "표준형" 이 여러 건이라 파일명이 겹친다. 모델명을 함께 붙인다.
    model_head = (picked.model or "").split("(")[0].strip()
    for ch in '\\/:*?"<>|':
        model_head = model_head.replace(ch, " ")
    # "6코어 CPU / 5코어 GPU" 처럼 / 를 지우면 공백이 겹치므로 하나로 줄인다.
    model_head = " ".join(model_head.split())
    info = load_quote_info()
    # 파일명 맨 앞은 거래처(견적처) 상호명. 비어 있으면 "거래처미지정".
    customer_name = quote_archive.safe_filename_part(info.get("customer_name") or "", 40) or "거래처미지정"
    name_parts = [
        p for p in (customer_name, (picked.tab_label or picked.tier or "").strip(), model_head) if p
    ]
    kr_label = f"{'_'.join(name_parts)}_{kr_label}"

    kind_label = "" if is_apple else margin_kind_label(margin_pct)
    form_tag = ""
    if selected_forms:
        kr_label += "_" + "_".join(f"폼{n}{FORM_LABELS[n]}" for n in selected_forms)
        form_tag = "_form" + "-".join(str(n) for n in selected_forms)
    single_title = SINGLE_SHEET_TITLE if len(selected_forms) == 1 else ""
    try:
        buf = build_excel(
            result,
            hide_unit_prices=hide_prices,
            quote_kind_label=kind_label,
            forms=selected_forms or None,
            single_sheet_title=single_title,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    ascii_name = f"quote_{cat}_{index}{form_tag}_{result.quote_date}.xlsx"
    display_name = f"{kr_label}_{result.quote_date}.xlsx"
    utf_name = quote(display_name)
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf_name}'
        ),
    }
    if archive:
        # 보관본은 서식 목록(썸네일) 시트 없이 저장한다. 서식을 골랐으면 애초에 그 시트가 없어 그대로 쓴다.
        archive_bytes = buf.getvalue()
        if not selected_forms:
            archive_bytes = build_excel(
                result,
                hide_unit_prices=hide_prices,
                quote_kind_label=kind_label,
                gallery=False,
            ).getvalue()
        entry = quote_archive.add_entry(
            archive_bytes,
            filename=display_name,
            meta={
                "kind": "card",
                "category": cat,
                "model": picked.model or "",
                "tab_label": picked.tab_label or picked.tier or "",
                "quotes": 1,
                "forms": selected_forms,
                "margin": None if is_apple else margin_pct,
                "total": picked.total,
                "customer": (info.get("customer_name") or "").strip(),
                "quote_date": result.quote_date,
            },
        )
        headers["X-Archive-Id"] = entry["id"]
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# 견적서 보관함 API (quote_archive/)
# ---------------------------------------------------------------------------
@app.get("/api/quotes/archive")
def api_quote_archive_list():
    return {"entries": quote_archive.list_entries(), **quote_archive.archive_stats()}


@app.get("/api/quotes/archive/{entry_id}/download")
def api_quote_archive_download(entry_id: str):
    entry = quote_archive.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="보관된 견적서를 찾을 수 없습니다.")
    path = quote_archive.entry_path(entry)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="보관 파일이 삭제되었거나 옮겨졌습니다.")
    return Response(
        content=path.read_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="archive_{entry_id}.xlsx"; '
                f"filename*=UTF-8''{quote(entry['filename'])}"
            ),
        },
    )


@app.put("/api/quotes/archive/{entry_id}")
def api_quote_archive_update(entry_id: str, payload: dict = Body(...)):
    entry = quote_archive.update_note(entry_id, str(payload.get("note") or ""))
    if not entry:
        raise HTTPException(status_code=404, detail="보관된 견적서를 찾을 수 없습니다.")
    return {"ok": True, "entry": entry}


@app.delete("/api/quotes/archive/{entry_id}")
def api_quote_archive_delete(entry_id: str):
    if not quote_archive.delete_entry(entry_id):
        raise HTTPException(status_code=404, detail="보관된 견적서를 찾을 수 없습니다.")
    return {"ok": True}


@app.post("/api/quotes/archive/backup")
def api_quote_archive_backup(payload: dict = Body(default={})):
    """보관함 전체를 백업 폴더(작업내역서와 같은 폴더)에 ZIP 한 파일로 저장. 폴더가 없으면 428."""
    folder = get_backup_dir()
    if not folder:
        raise HTTPException(status_code=428, detail={"code": "no_dir", "message": "백업 폴더를 먼저 지정하세요."})
    try:
        result = quote_archive.backup_archive(folder)
    except FileNotFoundError as e:
        raise HTTPException(status_code=428, detail={"code": "no_dir", "message": str(e)}) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"백업 파일을 쓰지 못했습니다: {e}") from e
    return {"ok": True, **result}


@app.post("/api/quotes/archive/open-dir")
def api_quote_archive_open_dir(payload: dict = Body(default={})):
    """보관 폴더(quote_archive/)를 서버 PC 화면의 탐색기로 연다. 항목 id 를 주면 그 파일을 선택한 상태로."""
    quote_archive.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    target = str(quote_archive.ARCHIVE_DIR)
    entry = quote_archive.get_entry(str(payload.get("id") or ""))
    if entry and quote_archive.entry_path(entry).is_file():
        target = str(quote_archive.entry_path(entry))
    try:
        if not open_in_explorer(target):
            raise HTTPException(status_code=404, detail=f"경로를 찾을 수 없습니다: {target}")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"탐색기를 열지 못했습니다: {e}") from e
    return {"ok": True, "opened": target}


# ---------------------------------------------------------------------------
# 컴퓨터 작업내역서 API
# ---------------------------------------------------------------------------
@app.get("/api/task-reports")
def api_get_task_reports():
    """저장된 작업내역서 목록 조회"""
    return {"reports": load_task_reports()}


@app.post("/api/task-reports")
def api_create_task_report(data: dict = Body(...)):
    """새 작업내역서 저장"""
    created = create_task_report(data)
    return {"status": "ok", "report": created}


@app.get("/api/task-reports/backup/settings")
def api_task_report_backup_settings():
    """작업내역서 백업 폴더(서버 PC 기준 경로)"""
    return {"dir": get_backup_dir()}


@app.post("/api/task-reports/backup/choose-dir")
def api_task_report_backup_choose_dir(payload: dict = Body(default={})):
    """백업 폴더 지정. dir 를 주면 그 경로를, 없으면 서버 PC 화면에 폴더 선택 창을 띄운다."""
    typed = str(payload.get("dir") or "").strip()
    try:
        if typed:
            return {"ok": True, "dir": set_backup_dir(typed), "source": "typed"}
        picked = choose_backup_dir_dialog(get_backup_dir())
        if not picked:
            return {"ok": False, "dir": get_backup_dir(), "message": "폴더를 고르지 않았습니다."}
        return {"ok": True, "dir": set_backup_dir(picked), "source": "dialog"}
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"폴더를 쓸 수 없습니다: {e}") from e


@app.post("/api/task-reports/backup")
def api_task_report_backup(payload: dict = Body(default={})):
    """저장된 작업내역서 전체를 백업 폴더에 ZIP 한 파일로 저장. 폴더가 없으면 428."""
    folder = get_backup_dir()
    if not folder:
        raise HTTPException(status_code=428, detail={"code": "no_dir", "message": "백업 폴더를 먼저 지정하세요."})
    try:
        result = backup_all_task_reports(folder)
    except FileNotFoundError as e:
        raise HTTPException(status_code=428, detail={"code": "no_dir", "message": str(e)}) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"백업 파일을 쓰지 못했습니다: {e}") from e
    return {"ok": True, **result}


@app.post("/api/task-reports/backup/open-dir")
def api_task_report_backup_open_dir(payload: dict = Body(default={})):
    """백업 폴더(또는 방금 만든 ZIP 을 선택한 상태)를 서버 PC 화면의 탐색기로 연다."""
    folder = get_backup_dir()
    if not folder:
        raise HTTPException(status_code=428, detail={"code": "no_dir", "message": "백업 폴더가 지정되어 있지 않습니다."})
    target = str(payload.get("path") or "")
    # 백업 폴더 안의 파일만 선택해 연다(다른 경로는 폴더만 연다).
    if not target or not Path(target).resolve().parent == Path(folder).resolve():
        target = folder
    try:
        if not open_in_explorer(target):
            raise HTTPException(status_code=404, detail=f"경로를 찾을 수 없습니다: {target}")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"탐색기를 열지 못했습니다: {e}") from e
    return {"ok": True, "opened": target}


@app.get("/api/task-reports/{report_id}")
def api_get_task_report_detail(report_id: str):
    """작업내역서 단건 조회"""
    report = get_task_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="작업내역서를 찾을 수 없습니다.")
    return {"status": "ok", "report": report}


@app.put("/api/task-reports/{report_id}")
def api_update_task_report(report_id: str, data: dict = Body(...)):
    """작업내역서 수정"""
    updated = update_task_report(report_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="작업내역서를 찾을 수 없습니다.")
    return {"status": "ok", "report": updated}


@app.delete("/api/task-reports/{report_id}")
def api_delete_task_report(report_id: str):
    """작업내역서 삭제"""
    deleted = delete_task_report(report_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="작업내역서를 찾을 수 없습니다.")
    return {"status": "ok", "message": "삭제되었습니다."}


@app.post("/api/task-reports/download/excel")
def api_download_task_report_excel(data: dict = Body(...)):
    """현재 작성 중인 작업내역서 데이터로 즉시 엑셀 파일 생성 다운로드"""
    buf = build_task_report_excel(data)
    cust_name = data.get("customer_name") or "고객"
    doc_no = data.get("doc_no") or date.today().strftime("%Y%m%d")
    filename = f"작업내역서_{cust_name}_{doc_no}.xlsx"
    utf_name = quote(filename)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="task_report.xlsx"; filename*=UTF-8\'\'{utf_name}',
        },
    )


@app.get("/api/task-reports/{report_id}/download/excel")
def api_download_saved_task_report_excel(report_id: str):
    """저장된 작업내역서 ID로 엑셀 파일 다운로드"""
    report = get_task_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="작업내역서를 찾을 수 없습니다.")
    buf = build_task_report_excel(report)
    cust_name = report.get("customer_name") or "고객"
    doc_no = report.get("doc_no") or report_id
    filename = f"작업내역서_{cust_name}_{doc_no}.xlsx"
    utf_name = quote(filename)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="task_report_{report_id}.xlsx"; filename*=UTF-8\'\'{utf_name}',
        },
    )

