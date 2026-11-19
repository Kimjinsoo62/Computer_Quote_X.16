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
| 오프라인 설치 패키지 갱신 | `Offline Install Package\download.bat` — 현재 `.venv`의 `pip freeze` 버전으로 휠(`wheels/`)과 Python 설치 파일(`python/`)을 다시 받는다(둘 다 gitignore). `install.bat`은 `wheels/`가 있으면 인터넷 없이 먼저 설치를 시도한다 |
| 서식 썸네일 재생성 | `.venv\Scripts\python.exe tools\make_form_thumbnails.py` — 폼1~8 디자인을 고쳤을 때만. Excel(COM)이 필요하고 잠깐 Excel 창이 뜬다. 샘플 견적 + 기본(가짜) 견적처 정보로 `static/img/forms/formN.png`(목록용 480px)와 `formN_large.png`(크게 보기 1200px)를 만든다 |

런타임 데이터는 모두 `.gitignore`에 있다. 업무 데이터는 **`data/` 폴더**에 종류별 하위 폴더로 둔다(`app/data_paths.py`가 경로를 한곳에서 정한다. 새 데이터 파일도 여기에 상수를 추가하고 모듈은 그 상수만 쓴다): `data/settings/`(quote_info · app_settings), `data/customers/`, `data/trade_statements/`(+ `signed/`), `data/task_reports/`, `data/manual_quotes/`, `data/quote_archive/`, `data/cache/`(Apple · 컴퓨존 스냅샷). 예전 버전은 프로젝트 루트에 뒀으므로 `data_paths`를 처음 import 할 때(서버 시작) 옛 위치에만 있는 항목을 옮긴다. 두 곳 다 있으면 아무것도 지우지 않고 새 위치를 쓰며, 옮기다 실패하면(잠긴 파일) 그 항목은 옛 위치를 계속 쓴다(`MIGRATION_LOG`). 그래서 **`app.main`을 import 하면 실제 데이터가 옮겨질 수 있다** — 시험할 때 루트에 데이터 파일을 복원하면 다음 import 때 다시 옮겨지거나 conflict 가 난다. `.excel_worker.pid`는 `stop.bat`/`force_stop.bat`이 상대 이름으로 읽으므로 루트에 그대로 두고, 안 쓰는 `coupang_config.json`도 루트에 남는다. 아래 설명의 파일 이름은 이 폴더 안의 것이다. `.excel_worker.pid`(미리보기용 상주 Excel PID), `quote_info.json`(견적처/공급자/계좌), `task_reports.json`(작업내역서 DB), `manual_quotes.json`(📝 새 견적 만들기로 직접 작성한 견적, `app/manual_quote.py`. 폼1~8 중 하나 + 품목(부가세 포함 단가, 최대 20행). `to_result()`가 `TodayQuotesResult` 한 건으로 바꿔 `build_excel()`에 넘기며 마진 · 올림 없음), `apple_catalog_cache.json`(Apple 스냅샷), `compuzone_parts_cache.json`(맞춤자동견적용 컴퓨존 추천부품 카탈로그 스냅샷), `coupang_config.json`(예전 쿠팡 파트너스 API 키. 지금은 코드가 읽지 않는다 — `coupang_search.py`는 일반 쿠팡 검색 주소만 만든다. 남아 있어도 절대 커밋 금지), `quote_archive/`(견적서 보관함: 카드별 다운로드(`archive=1`)의 xlsx 사본 + `index.json`, `app/quote_archive.py`. 파일명은 `{거래처명}_{구분}_{모델}_…_{id}.xlsx`. 보관본은 `gallery=False`로 다시 만들어 서식 목록 시트를 뺀다), `customers.json`(거래처 목록, `app/customer_book.py`. [설정 → 📒 거래처] 탭의 생성·검색·삭제. 상호명 기준 upsert), `trade_statements.json` + `trade_statements/`(🧾 거래명세서, `app/trade_statement.py`. 거래명세표 목록 · 서명 상태와 서명받은 명세서 스캔/사진 `{id}_signed.ext`), `app_settings.json`(작업내역서·보관함 공용 백업 폴더, 견적 메일용 Gmail 계정 `mail_smtp` — 앱 비밀번호는 Windows DPAPI 로 암호화, `app/mail_send.py` — 등 모달 밖 설정. `quote_info.save_quote_info()`는 모달이 안 보낸 키를 기본값으로 되돌리므로 거기에 넣지 않는다).

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
- 카테고리 키: 컴퓨존 `ai`(AI PC 기획전) · `premium`/`iworks`/`recommend`(`config.ASSEMBLED_CATEGORIES`) · `custom`(`custom_builder.py` 컴퓨존 추천부품 기반 최저가·추천가·최고가 3종 맞춤자동견적. 목록 API 는 `needs_custom_wizard`만 돌려주고 실제 견적은 `GET /api/custom-quote/options` → `POST /api/custom-quote/build`. 부품 카탈로그는 8개 부품군을 병렬 수집해 메모리 15분(`PART_CATALOG_TTL_SEC`) 캐시, 필수 부품군이 하나라도 비면 `compuzone_parts_cache.json` 스냅샷으로 폴백) · `game`(게임으로 PC 찾기, 위자드) · `price`(금액대로 찾기, `price_type=assembled|notebook`) · Apple `apple_<cat>`(`config.APPLE_CATEGORIES`).
- 수집은 **urllib + 정규식 + 페이지에 내장된 JSON 파싱만** 쓴다. README는 BeautifulSoup을 언급하지만 `requirements.txt`에도 없고 import하지 않는다. 새 수집 코드도 urllib 기반으로 맞춘다.
- 서비스 모듈: `shutdown_service.py`(종료 API 실제 동작), `update_service.py`(git fetch/pull/재시작), `calc_window.py`(계산기를 별도 브라우저 프로세스로 띄움, 프로필 `AI-PC-Quote-Calc`), `quote_info.py` · `task_report.py`(JSON 파일 저장).
- 📤 견적서 올리기(`POST /api/quotes/upload`): `.xlsx`는 그대로, PDF 는 `app/pdf_to_excel.py`가 **PDF 모양 그대로 Excel** 로 바꿔 둘 다 📥 보관 파일(`kind: "upload"`)로 넣는다 — 폼1~8 에 맞추지 않는다. 원본 PDF 는 보관 xlsx 옆 같은 이름 `.pdf`(`quote_archive.attach_source_pdf` / `source_pdf_path`, 삭제 · 백업 ZIP 에 같이). 변환: PDFium(잠금 안)에서 글자(한 글자씩 loose charbox → 줄 · 단어로 다시 묶음. ezPDF 처럼 글자마다 따로 놓는 PDF 때문), 선(얇은 칠한 사각형 · 선분, 점선 조각 묶음), 배경(흰색 제외), 그림 영역(이미지 + 곡선 · 복잡한 도형을 묶어 2배 렌더링)을 꺼내고, 그 x/y 로 격자를 만들어 글자를 감싸는 선 · 배경 칸으로 병합한다. 글꼴 크기 = PDF 글꼴 크기 × 글자 개체 행렬 배율(Edge 는 24 × 0.75). 경로 점도 개체 행렬을 곱해 쪽 좌표로. Excel 열 너비 단위가 인쇄될 때 몇 pt 인지는 PC(화면 배율 · 기본 글꼴)마다 달라(이 PC 는 7px 공식보다 약 9% 넓음) 서버를 켠 뒤 첫 변환 때 Excel 로 시험 시트를 인쇄해 잰다(`_excel_metrics`, Excel 없으면 7px 공식 + 한 쪽 맞춤). 열 · 행은 누적 위치를 픽셀로 반올림해 나눈다. 검증은 xlsx → Excel 워커 PDF → 글자 비교 + PNG 나란히(폼1~8 · 실제 견적서 doPDF/ezPDF · Edge 인쇄 표 · 가로 쪽). `app/quote_import.py`는 이제 목록용 거래처 · 합계 · 견적일만 읽는다(`parse_rows`). XII.18 의 PDF→📝 작성본 경로는 없앴고, 그때 만든 작성본의 `source_pdf`(`data/manual_quotes/pdf/`)는 계속 보인다.
- 🧺 컴퓨존 장바구니(`POST /api/compuzone/cart`, 진행 `GET /api/compuzone/cart/{id}`): `app/compuzone_cart.py`가 계정(`app_settings` `compuzone_account`, 비밀번호 DPAPI — `mail_send._dpapi`)을 들고 작업 프로그램 `python -m app.compuzone_cart_runner <data/AI-PC-Quote-Compuzone>`(Playwright, `channel="msedge"` 보이는 창, 앱 전용 영구 프로필)을 띄워 stdin 으로 작업 JSON(비밀번호 포함, 명령줄에 넣지 않음) · stdout 으로 진행 JSON 을 주고받는다. **sync Playwright 는 Playwright 쪽에서 기다릴 때만 이벤트(응답 · 대화상자)를 처리**하므로 runner 의 대기는 `time.sleep` 이 아니라 `page.wait_for_timeout`(`pause`/`wait`)이다. 컴퓨존 구조: 로그인 `#member_id`·`#member_password`·`#login-btn`, 완제품 `a.cart[href*=delivery]`(비회원 가능, 장바구니로 이동), 단품 `a.cart`→`buy_direct`(회원만, 비회원이면 `#limit_login_lypop`), 수량 `button.bt_increase[onclick*='last_<pno>']`, 장바구니 행 `input[name="SelectOrderCartNo[]"][data-pno]`. 맞춤자동견적은 `custom_meta.tiers[i].part_details`(pno · qty, 조립비 3877 포함), 완제품은 `Quote.product_no`. 보안 확인(캡차 · 추가 인증)은 우회하지 않고 창에서 사람이 마치기를 기다린다. 담김 판정은 버튼 응답이 아니라 **장바구니 전후 수량 비교**(`basket_counts`: 행의 `ea[장바구니번호]`, 수량 0 서비스 줄 제외), 로그인 확인은 따로 연 탭에서 회원 전용 `/mypage/wish_list.htm`(비회원이면 login.htm)으로 본다. 대화상자는 자동 입력 · 담기 동안만 자동 확인(`auto_dialogs`). **로그인 상태 유지**: 컴퓨존 로그인 · 장바구니는 세션 쿠키(CSID · OrderCartCode)라 창을 닫으면 사라지므로, 로그인 성공 · 작업 끝 · 종료(quit) 때 컴퓨존 쿠키를 DPAPI 로 암호화해 `<프로필>/cz_session.bin`에 두고 창을 새로 열 때 `add_cookies`로 되살린다(비회원 장바구니로 창 닫기 → 다시 열기 후 유지 확인). 계정을 바꾸거나 지우면 `clear_saved_session()`, 설정의 🧹 버튼은 `POST /api/compuzone/session/clear`. 종료 정리는 프로필 폴더 이름 `AI-PC-Quote-Compuzone`(Edge)과 `app.compuzone_cart_runner`(python)만 대상(shutdown_service · stop.bat · force_stop.bat). 시험용 `CZ_HEADLESS=1` + 작업의 `guest_test`(비회원, 화면 없음).
- 포트 8091은 어느 코드도 바인딩하지 않는다. 종료 스크립트가 8090과 함께 정리하는 예약 포트일 뿐이다.

### 프론트엔드

`static/js/app.js` 한 파일(약 6,200줄)에 모듈 전역 상태(`currentStore`, `quoteData`, `currentCat`, `quoteMarginPercent`, `include*` 토글, CTO 상태)와 렌더 함수가 모두 있다. 번들러 없음. `templates/index.html`은 Jinja2로 서버 정보(LAN IP 등)만 주입받는다. 🧩 견적요소(`elements`: 공급자 · 견적일 · 추가 금액 · 계좌) | 📒 거래처(`customer`: 거래처 목록 + 견적처) · 🗂️ 견적서(`archive`) · 📋 작업내역서(`taskreport`) 목록은 `#settingsModal`(⚙️ 설정) 한 창의 탭이며, 기존 요소 id(`qi*`, `qa*`, `trListPanel` 등)를 그대로 쓴다. `qi*` 칸이 두 탭에 나뉘어 있어도 두 💾 저장(`.qi-save-btn`)은 `qiFields` 18칸을 모두 보낸다(`save_quote_info()`가 빠진 키를 기본값으로 되돌리므로 한 탭 칸만 보내면 안 됨). 경고 배너 · 상태 줄은 탭마다 하나씩이라 class(`.qi-warning-banner`, `.qi-status`)로 함께 갱신한다. 작업내역서 작성 창(`#taskReportModal`)은 따로 있고 인쇄 CSS가 그 id에 묶여 있다. 🧾 거래명세서(`statement`) 탭은 `#tradeStatementModal`(작성 창)을 연다. 견적 카드의 🧾 버튼은 `displayParts(q)`(화면 마진가)를 그대로 담는다. `static/js/iconbtn.js`는 이모지로 시작하는 메뉴 버튼의 한글을 `.ib-label`로 감싸 숨기고 마우스오버 말풍선(`#ibTip`)으로 보여 준다(MutationObserver 로 나중에 생긴 버튼도 처리). 상단 견적 카테고리 줄(`.hero-actions`)은 예외라 한글이 그대로 보인다. 새 메뉴 버튼은 이모지로 시작하게 쓰고, 값 표시 span 은 `.ib-keep`을 붙인다. `templates/calc.html` + `static/js/calc.js`는 독립 계산기 창, `templates/사용설명서.html`은 `/manual`로 서빙된다.

### 마진 · 금액 규칙

- 컴퓨존 견적만 마진이 붙는다. 기본 `CONSUMER_MARGIN_PERCENT = 30`, 순환 `30 → 25 → 20 → 15 → 10 → 0`.
- 서버는 **Excel 다운로드 시에만** `apply_quote_margin()`을 적용하고, 화면 표시는 `app.js`가 같은 로직(`markedUpAmount`, `ceilToManwon`, `displayParts`)을 클라이언트에서 재계산한다. 한쪽 수식을 바꾸면 반드시 다른 쪽도 맞춘다.
- 금액은 만원 단위 올림(`ceil_to_manwon`). 단가 올림으로 생긴 잔액은 `reconcile_parts_to_total()`이 `할인` 행에 흡수시켜 품목 합계 = 총액을 유지한다.
- Apple 견적은 마진 · 올림 없이 공식몰 원단위 가격 그대로 쓴다.

### Apple 수집 캐시 3층

1. 메모리 HTML 캐시(`HTML_TTL_SEC` 15분) → 2. 메모리 카탈로그 캐시(`CATALOG_TTL_SEC` 30분) → 3. `apple_catalog_cache.json` 스냅샷(라이브 수집 실패 시 경고와 함께 폴백).
`refresh=1`/`/api/apple/refresh`는 메모리 두 층만 비우고 다시 수집한다. 스냅샷 파일은 지우지 않으며, 수집 성공 시 덮어쓰고 실패 시 폴백으로 남는다. 직전 스냅샷보다 모델 수가 30% 이상 줄면 `meta.warnings`에 페이지 구조 변경 의심 경고를 넣고 화면에 띄운다.
Mac 계열(`MAC_TIER_PREFIXES`)은 공식몰 구성하기 API로 **실시간 CTO**(`fetch_apple_cto_live`)를, 그 외 제품은 구매 페이지 비교 옵션(`fetch_apple_cto_options`)을 준다.
Apple 견적 행은 `quote_engine._build_apple_catalog_parts`가 만든다: `제품` 1행(공식가) + 금액 0 세부사양 행. 세부사양은 `apple_scraper`가 구매 페이지 `productSelectionData`에서 모은 `specs["detail"]`(칩 설명 `_chip_detail`, 색상, 기본 구성의 전원 어댑터 · 이더넷 · 키보드 `BASE_DETAIL_DIMS`, 마감 · 스탠드, 액세서리 소개 문단 `_page_summary`)이다. 사양 행 구분명(`CPU/SoC` · `메모리` · `SSD` · `전원 어댑터` · `이더넷` · `연결` · `디스플레이 마감` · `스탠드`)은 `CTO_BASE_ROW`의 대상이라 바꾸면 양쪽을 맞춘다. 옛 스냅샷에는 `detail`이 없어 칩 · 메모리 · 용량 · 크기만 나온다.

### Excel

**거래명세표**(`app/trade_statement.py`의 `build_statement_excel`)는 폼1~8 과 별개다. 폼5(리테일) 모양에 제목만 "거 래 명 세 표"로 바꾼 한 벌을 `_write_copy()`로 A4 한 장에 위(공급자 보관용) · 아래(공급받는자 보관용) 두 번 그리고 사이에 절취선을 둔다. 견적유효기간 대신 납품 문구, 각 벌 아래 인수자 서명 칸. 품목 표는 최소 `MIN_ROWS`(10)행, 최대 `MAX_ITEMS`(30, `app.js`의 `TS_MAX_ROWS`와 같아야 함). 품목이 많으면 `_statement_layout()`이 제목 · 안내 줄 · 합계 · 서명 칸을 `_LAY_COMPACT`로 줄이고 남는 높이로 품목 행 높이 · 글자(`ITEM_FONT_RATIO`)를 정해 두 벌을 한 장에 담는다. 행 높이는 Excel 픽셀 단위(0.75pt, `_snap`)로 계산해야 쪽을 넘지 않고, 여백은 상하 5mm · 좌우 8mm(15mm 면 Excel 이 92% 로 축소)다. 절취선은 위아래 빈 줄 높이를 같게 + 세로 가운데 맞춤으로 쪽 정중앙에 둔다. 할인 행용으로 음수 단가를 허용한다. 기록의 `show_prices`(작성 창 `#tsUnitPrice`, 카드 · 새 명세서는 메인창 `showUnitPrices`를 따름)가 False 면 품목 금액 칸을 비우고 합계만 찍는다(PDF 캐시 키에 포함). 폼5 모양을 고치면 여기도 맞춘다. 작업내역서 저장(`POST`/`PUT /api/task-reports`)은 `trade_statement.sync_from_task_report()`로 연결된 명세서(`task_report_id`)를 만들거나 고친다(품목 · 고객 · 날짜 · 제목만 갱신, 번호 · 서명 · 서명본 · 비고 · `show_prices`는 유지). **한 방향만**이다: 명세서 저장은 작업내역서를 만들지 않고, 작업내역서를 지워도 명세서는 남는다.

`excel_export.build_excel()`은 `견적요약` 시트 1장 + **견적(카드)마다** 폼1~8 시트 8장을 만든다. `forms=[1,3]`을 주면 전부 만든 뒤 `_keep_selected_forms()`가 견적요약과 선택 외 폼 시트를 지운다(폼 생성 코드는 한 함수에 순서대로 있어 쪼개지 않는다). 시트 하나만 남는 카드별 추출은 `main.SINGLE_SHEET_TITLE`("견   적   서", 공백 3칸)로 이름을 바꾼다. `/api/download/excel`은 카드 한 건(`index>=0`) 전용이다(목록 전체 비교 파일은 없앰, `index<0`이면 400). 화면은 카드의 [📥 엑셀 견적서]가 서식 미리보기 창(`openFormGallery`)을 열고, 고른 서식 하나만 `forms=N`으로 받는다(서식 선택 저장 · 일괄 다운로드는 없앰). `forms` 없이 받는 전체 파일은 맨 앞에 `서식 목록` 시트(`_add_form_gallery_sheet`, 썸네일 + 시트 내부 링크)가 붙는다. 썸네일은 `static/img/forms/formN.png`로 저장소에 포함되며 Excel 삽입에 Pillow가 필요하다. 화면 갤러리(`openFormGallery`)는 이 PNG 대신 그 카드의 **실제 견적서** 썸네일을 쓴다: 폼1~8 을 `forms=N&thumb=1`로 동시에 요청하고(다 된 칸부터 채움) 서버는 PDF 를 `pypdfium2`로 PNG 로 바꾼다(PDFium 은 스레드 안전하지 않아 `render_png`가 `_pdfium_lock` 안에서만 부른다. 잠금 없이 동시 호출하면 서버 프로세스가 로그 없이 죽는다. PDFium 을 새로 쓰는 코드도 이 잠금을 쓴다). 한 장을 누르면 `pdf=1`(보관 안 함)로 받아 iframe 에 띄운다. Excel 없으면 503 → 화면은 `formN.png`/`formN_large.png` 샘플로 폴백. `app/pdf_export.py`는 **상주 Excel 워커 풀**(`WORKER_COUNT` 최대 3, PowerShell 이 숨긴 Excel 을 띄워 stdin 으로 "xlsx<TAB>pdf" 요청 처리, 앱 시작 때 `prewarm`이 `sample_result()` 폼1~8 로 예열, 15분 유휴 시 종료, 서버가 죽으면 stdin EOF 로 스스로 종료, 비상용 PID 는 `.excel_worker.pid` → `stop.bat`/`force_stop.bat`이 그 PID 만 정리. 서버가 비정상 종료돼 남은 것은 다음 시작 때 `cleanup_stale_workers()`가 같은 파일로 정리한다 — EXCEL/powershell 이름이고 이 서버보다 먼저 시작된 프로세스만)이며, 워커 Excel 안에서만 활성 프린터를 `Microsoft XPS Document Writer`로 바꾼다(네트워크 기본 프린터면 내보내기가 2~4배 느림, Windows 기본 프린터는 그대로). PDF · PNG 는 요청 조건+견적정보+품목 키로 10분 캐시하고 같은 키 동시 요청은 한 번만 만든다(`cached`). `main._shared_fetch`는 화면 목록 API 가 수집한 결과(`fresh=True`)를 `/api/download/excel`(`fresh=False`)이 10분간 그대로 써서 컴퓨존 재수집(요청당 약 3초)을 없애고 카드 · 견적서 가격을 일치시킨다. 거기서 [📥 다운로드] · [✉️ 이메일]을 고른다. 이메일은 `email=1`이 같은 xlsx 를 서버 메모리 초안(`mail_send.add_draft`, 1시간)으로 두고 받는 사람(견적처) · 참조(공급자) · 제목 · 본문 JSON 을 돌려주며, 앱 안 발송 창의 [보내기]가 `POST /api/mail/send`(`attach: ["xlsx","pdf"]` 중 선택, PDF 는 미리보기와 같은 캐시 키로 재사용)로 Gmail SMTP(465) 발송한다(브라우저 mailto 는 첨부 불가, 이 PC 의 Outlook 은 계정이 없어 .eml 방식은 버림). 폼 시트는 A4 세로 1페이지 강제(`_apply_page_setup`, 견적요약만 예외), 총 열 너비는 `MAX_TOTAL_COL_WIDTH = 88`을 `_assert_print_width()`가 검사한다. 열을 추가하면 다른 열 너비를 줄여 88 안에 맞춘다. 행 높이는 `_auto_fit_row_heights()`가 글자 폭 추정으로 계산한다. 견적처 · 공급자의 담당자가 상호와 같으면 `excel_export.distinct_manager()`(화면 `app.js distinctManager`, 같은 규칙)가 빈칸을 돌려줘 `상호 (담당자)`처럼 한 줄에 붙여 쓰는 곳(폼3 · 4 · 5 · 7 · 8 · 거래명세표 · 화면 견적서 · 메일 초안)에서 한 번만 찍는다. 담당자 칸이 따로 있는 폼1 · 2 · 6 은 그대로.

## 반드시 지켜야 할 불변 조건

1. **버전 올리기는 정확히 5개 파일**: `README.md`(맨 위에 "Ver XII.NN 주요 변경" 절 추가), `app/main.py`의 `FastAPI(version=...)`, `app/daangn_search.py`의 `NOMINATIM_UA`, `templates/index.html`(`<title>`과 헤더 문구, 2곳), `templates/사용설명서.html`(제목 · 헤더 · 개요 · 변경 절 · 푸터). 커밋 메시지는 `chore: Bump version to XII.NN`. 프로젝트 폴더 이름(`Computer_Quote_XII.NN`, `rename_folder.bat`)도 버전을 따른다. 버전 체계는 X.42 다음 `XI.NN`(XI.01~XI.20), XI.20 다음부터 `XII.NN`(XII.01 부터)이다.
2. **GitHub 저장소 이름은 `Computer_Quote_X.16`으로 고정**이다. `update_service.REPO_NAME`과 `update.bat :fixremote`가 X.16을 가리키는 것은 버그가 아니다. 버전을 올려도 바꾸지 않는다.
3. **`CTO_BASE_ROW`는 `app/main.py`와 `static/js/app.js`에 중복 정의**돼 있고 반드시 동일해야 한다(양쪽 주석에 명시). 새 견적 만들기의 최대 품목 행 수도 `app/manual_quote.py`의 `MAX_ITEMS`와 `app.js`의 `MQ_MAX_ROWS`로 중복돼 있어 같이 바꾼다. 거래명세표도 `app/trade_statement.py`의 `MAX_ITEMS`와 `app.js`의 `TS_MAX_ROWS`가 같아야 한다.
4. **종료 시 자기 자신을 taskkill하지 않는다.** `shutdown_service.protected_pids()`로 자신과 부모를 제외하고, 자신은 `os._exit(0)`로만 끝낸다. 콘솔에 붙은 프로세스를 강제 종료하면 conhost가 크래시한다. 브라우저도 `--app=http://127.0.0.1:8090` 창과 `AI-PC-Quote-Calc` 프로필만 닫는다.
5. **기능이 바뀌면 `templates/사용설명서.html`을 같이 갱신**한다(README의 버전 변경 절과 짝을 이룬다).
6. Windows 전용이다. 서브프로세스는 PowerShell/`cmd`를 호출하며 `CREATE_NO_WINDOW`를 쓴다. POSIX 호환을 위한 리팩터링은 하지 않는다.
7. `app/daangn_search.py`는 `used_market`의 키워드 정규화에서만 쓰이는 거의 휴면 모듈이지만, 버전 문자열을 갖고 있어 1번 bump 대상에 포함된다.
