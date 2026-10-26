# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

컴퓨존(Compuzone) 조립PC와 Apple 공식몰(한국) 가격을 실시간으로 수집해 견적 카드로 보여 주고, 마진 조절 · 8종 Excel 견적서 · 작업내역서 · 다나와/쿠팡/중고 시세 비교를 제공하는 Windows 전용 로컬 웹앱이다. FastAPI 백엔드(포트 8090) + 프레임워크 없는 단일 vanilla JS 프론트엔드. 주석 · UI 문구 · README · 사용설명서는 모두 한국어, 커밋 메시지는 영어 conventional-commit 형식(`feat:` `fix:` `chore:` `style:` `docs:`)이다.

사용자 전역 지침(`~/.claude/CLAUDE.md`)의 11개 필수 기능은 이 프로젝트에 이미 구현돼 있다. 단, 11번의 `STOP.bat`은 여기서 `stop.bat`(소문자)과 `force_stop.bat`으로 존재한다.

## 명령

빌드 단계 · 린터 · 테스트 스위트는 없다(`test_*.py` 없음). 검증은 아래 import 스모크 체크와 실제 실행으로 한다.

| 목적 | 명령 |
|---|---|
| 앱 실행(사용자용) | `START.bat` — `.venv`가 없거나 패키지가 빠지면 `install.bat /auto`를 먼저 호출하고, uvicorn을 띄운 뒤 Edge/Chrome `--app` 모드 창을 연다 |
| 의존성 설치 | `install.bat` — `.venv` 생성 → `requirements.txt` 설치 → `from app.main import app` 로드 검증 |
| 개발용 직접 실행 | `.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8090` (`--reload`를 붙여도 됨) |
| import 스모크 체크 | `.venv\Scripts\python.exe -c "from app.main import app"` |
| 종료 | `stop.bat` — `POST /api/shutdown` 호출 후 포트 8090/8091 · uvicorn · `--app` 브라우저 창을 정리. 서버가 죽었을 땐 `force_stop.bat`(관리자 권한) |
| 업데이트 | `update.bat` — `git pull origin main` → pip 재설치 → `START.bat` 재시작. 화면 [업데이트] 버튼은 `update_service.py`를 통해 같은 일을 한다 |
| 캐시 삭제만 | `.venv\Scripts\python.exe -c "from app.shutdown_service import clear_python_cache; clear_python_cache()"` |
| 서식 썸네일 재생성 | `.venv\Scripts\python.exe tools\make_form_thumbnails.py` — 폼1~8 디자인을 고쳤을 때만. Excel(COM)이 필요하고 잠깐 Excel 창이 뜬다. 샘플 견적 + 기본(가짜) 견적처 정보로 `static/img/forms/formN.png`(480px)를 만든다 |

런타임 데이터 파일은 모두 `.gitignore`에 있다: `quote_info.json`(견적처/공급자/계좌), `task_reports.json`(작업내역서 DB), `apple_catalog_cache.json`(Apple 스냅샷), `coupang_config.json`(쿠팡 파트너스 API 키 — 절대 커밋 금지), `quote_archive/`(견적서 보관함: 카드별 다운로드(`archive=1`)의 xlsx 사본 + `index.json`, `app/quote_archive.py`. 파일명은 `{거래처명}_{구분}_{모델}_…_{id}.xlsx`. 보관본은 `gallery=False`로 다시 만들어 서식 목록 시트를 뺀다), `customers.json`(거래처 목록, `app/customer_book.py`. [설정관리 → 견적정보] 탭의 생성·검색·삭제. 상호명 기준 upsert), `app_settings.json`(작업내역서·보관함 공용 백업 폴더 등 모달 밖 설정. `quote_info.save_quote_info()`는 모달이 안 보낸 키를 기본값으로 되돌리므로 거기에 넣지 않는다).

## 아키텍처

### 요청 흐름

```
templates/index.html + static/js/app.js  ──fetch──▶  app/main.py (모든 라우트)
                                                         │
                 ┌───────────────────────────────────────┼──────────────────────────┐
                 ▼                                       ▼                          ▼
      app/quote_engine.py                      app/apple_scraper.py        비교/부가 모듈
      컴퓨존 수집 + 데이터클래스               Apple 카탈로그 · 실시간 CTO    danawa_search, coupang_search,
      (QuotePart/Quote/TodayQuotesResult)                                   used_market(+daangn_search),
      마진 · 만원 올림                                                        image_lookup
                 │                                       │
                 └──────────────► app/excel_export.py ◄──┘   (TodayQuotesResult → 견적요약 + 카드별 폼1~8 .xlsx)
```

- `main.py`는 라우트와 소수의 요청 전용 헬퍼(`_apply_cto_upgrades`, 파일명 라벨, `get_lan_ip`)만 갖는다. 모든 견적 API는 `TodayQuotesResult`를 만들어 `today_quotes_to_dict()`로 직렬화하고, Excel은 같은 결과를 `build_excel()`에 넘긴다. 즉 **화면 JSON과 Excel은 같은 데이터클래스에서 나온다.**
- 카테고리 키: 컴퓨존 `ai`(AI PC 기획전) · `premium`/`iworks`/`recommend`(`config.ASSEMBLED_CATEGORIES`) · `game`(게임으로 PC 찾기, 위자드) · `price`(금액대로 찾기, `price_type=assembled|notebook`) · Apple `apple_<cat>`(`config.APPLE_CATEGORIES`).
- 수집은 **urllib + 정규식 + 페이지에 내장된 JSON 파싱만** 쓴다. README는 BeautifulSoup을 언급하지만 `requirements.txt`에도 없고 import하지 않는다. 새 수집 코드도 urllib 기반으로 맞춘다.
- 서비스 모듈: `shutdown_service.py`(종료 API 실제 동작), `update_service.py`(git fetch/pull/재시작), `calc_window.py`(계산기를 별도 브라우저 프로세스로 띄움, 프로필 `AI-PC-Quote-Calc`), `quote_info.py` · `task_report.py`(JSON 파일 저장).
- 포트 8091은 어느 코드도 바인딩하지 않는다. 종료 스크립트가 8090과 함께 정리하는 예약 포트일 뿐이다.

### 프론트엔드

`static/js/app.js` 한 파일(약 3,100줄)에 모듈 전역 상태(`currentStore`, `quoteData`, `currentCat`, `quoteMarginPercent`, `include*` 토글, CTO 상태)와 렌더 함수가 모두 있다. 번들러 없음. `templates/index.html`은 Jinja2로 서버 정보(LAN IP 등)만 주입받는다. 견적정보·거래처 / 서식폼 / 견적보관함 / 작업내역서 목록은 `#settingsModal`(⚙️ 설정관리) 한 창의 탭이며, 기존 요소 id(`qi*`, `qa*`, `trListPanel` 등)를 그대로 쓴다. 작업내역서 작성 창(`#taskReportModal`)은 따로 있고 인쇄 CSS가 그 id에 묶여 있다. `templates/calc.html` + `static/js/calc.js`는 독립 계산기 창, `templates/사용설명서.html`은 `/manual`로 서빙된다.

### 마진 · 금액 규칙

- 컴퓨존 견적만 마진이 붙는다. 기본 `CONSUMER_MARGIN_PERCENT = 30`, 순환 `30 → 25 → 20 → 15 → 10 → 0`.
- 서버는 **Excel 다운로드 시에만** `apply_quote_margin()`을 적용하고, 화면 표시는 `app.js`가 같은 로직(`markedUpAmount`, `ceilToManwon`, `displayParts`)을 클라이언트에서 재계산한다. 한쪽 수식을 바꾸면 반드시 다른 쪽도 맞춘다.
- 금액은 만원 단위 올림(`ceil_to_manwon`). 단가 올림으로 생긴 잔액은 `reconcile_parts_to_total()`이 `할인` 행에 흡수시켜 품목 합계 = 총액을 유지한다.
- Apple 견적은 마진 · 올림 없이 공식몰 원단위 가격 그대로 쓴다.

### Apple 수집 캐시 3층

1. 메모리 HTML 캐시(`HTML_TTL_SEC` 15분) → 2. 메모리 카탈로그 캐시(`CATALOG_TTL_SEC` 30분) → 3. `apple_catalog_cache.json` 스냅샷(라이브 수집 실패 시 경고와 함께 폴백).
`refresh=1`/`/api/apple/refresh`는 메모리 두 층만 비우고 다시 수집한다. 스냅샷 파일은 지우지 않으며, 수집 성공 시 덮어쓰고 실패 시 폴백으로 남는다. 직전 스냅샷보다 모델 수가 30% 이상 줄면 `meta.warnings`에 페이지 구조 변경 의심 경고를 넣고 화면에 띄운다.
Mac 계열(`MAC_TIER_PREFIXES`)은 공식몰 구성하기 API로 **실시간 CTO**(`fetch_apple_cto_live`)를, 그 외 제품은 구매 페이지 비교 옵션(`fetch_apple_cto_options`)을 준다.

### Excel

`excel_export.build_excel()`은 `견적요약` 시트 1장 + **견적(카드)마다** 폼1~8 시트 8장을 만든다. `forms=[1,3]`을 주면 전부 만든 뒤 `_keep_selected_forms()`가 견적요약과 선택 외 폼 시트를 지운다(폼 생성 코드는 한 함수에 순서대로 있어 쪼개지 않는다). 시트 하나만 남는 카드별 추출은 `main.SINGLE_SHEET_TITLE`("견   적   서", 공백 3칸)로 이름을 바꾼다. `/api/download/excel`은 카드 한 건(`index>=0`) 전용이다(목록 전체 비교 파일은 없앰, `index<0`이면 400). 화면은 서식마다 `forms=N`으로 따로 호출해 파일을 하나씩 받고, 선택은 브라우저 localStorage `excelForms`에만 저장한다(종료 시 캐시 삭제에서 보존). 서식 선택이 없는 전체 파일은 맨 앞에 `서식 목록` 시트(`_add_form_gallery_sheet`, 썸네일 + 시트 내부 링크)가 붙는다. 썸네일은 `static/img/forms/formN.png`로 저장소에 포함되며 Excel 삽입에 Pillow가 필요하다. 화면 갤러리(`openFormGallery`)도 같은 PNG를 쓴다. 폼 시트는 A4 세로 1페이지 강제(`_apply_page_setup`, 견적요약만 예외), 총 열 너비는 `MAX_TOTAL_COL_WIDTH = 88`을 `_assert_print_width()`가 검사한다. 열을 추가하면 다른 열 너비를 줄여 88 안에 맞춘다. 행 높이는 `_auto_fit_row_heights()`가 글자 폭 추정으로 계산한다.

## 반드시 지켜야 할 불변 조건

1. **버전 올리기는 정확히 5개 파일**: `README.md`(맨 위에 "Ver X.NN 주요 변경" 절 추가), `app/main.py`의 `FastAPI(version=...)`, `app/daangn_search.py`의 `NOMINATIM_UA`, `templates/index.html`(`<title>`과 헤더 문구, 2곳), `templates/사용설명서.html`(제목 · 헤더 · 개요 · 변경 절 · 푸터). 커밋 메시지는 `chore: Bump version to X.NN`. 프로젝트 폴더 이름(`Computer_Quote_X.NN`)도 버전을 따른다.
2. **GitHub 저장소 이름은 `Computer_Quote_X.16`으로 고정**이다. `update_service.REPO_NAME`과 `update.bat :fixremote`가 X.16을 가리키는 것은 버그가 아니다. 버전을 올려도 바꾸지 않는다.
3. **`CTO_BASE_ROW`는 `app/main.py`와 `static/js/app.js`에 중복 정의**돼 있고 반드시 동일해야 한다(양쪽 주석에 명시).
4. **종료 시 자기 자신을 taskkill하지 않는다.** `shutdown_service.protected_pids()`로 자신과 부모를 제외하고, 자신은 `os._exit(0)`로만 끝낸다. 콘솔에 붙은 프로세스를 강제 종료하면 conhost가 크래시한다. 브라우저도 `--app=http://127.0.0.1:8090` 창과 `AI-PC-Quote-Calc` 프로필만 닫는다.
5. **기능이 바뀌면 `templates/사용설명서.html`을 같이 갱신**한다(README의 버전 변경 절과 짝을 이룬다).
6. Windows 전용이다. 서브프로세스는 PowerShell/`cmd`를 호출하며 `CREATE_NO_WINDOW`를 쓴다. POSIX 호환을 위한 리팩터링은 하지 않는다.
7. `app/daangn_search.py`는 `used_market`의 키워드 정규화에서만 쓰이는 거의 휴면 모듈이지만, 버전 문자열을 갖고 있어 1번 bump 대상에 포함된다.
