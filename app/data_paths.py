# -*- coding: utf-8 -*-
"""앱 데이터 저장 위치를 한곳에서 정한다 (프로젝트 폴더의 data/).

    data/
      README.txt                  폴더 안내 (자동 생성)
      settings/                   quote_info.json(견적처 · 공급자 · 계좌) · app_settings.json(백업 폴더 · 메일 계정)
      customers/                  customers.json(거래처 목록)
      trade_statements/           trade_statements.json(거래명세서 목록) + signed/(서명받은 명세서 스캔 · 사진)
      task_reports/               task_reports.json(작업내역서)
      manual_quotes/              manual_quotes.json(📝 새 견적 만들기 작성본) + pdf/(📤 올린 PDF 원본)
      quote_archive/              index.json + 보관한 견적서 xlsx
      cache/                      Apple · 컴퓨존 수집 스냅샷(지워도 다시 받음)

예전 버전은 이 파일들을 프로젝트 폴더 바로 아래에 두었다. 이 모듈을 처음 불러올 때(서버 시작) 옛 위치에만 있는
항목을 새 위치로 옮긴다. 두 곳에 다 있으면 아무것도 지우지 않고 새 위치를 쓰며 경고를 찍는다. 옮기다 실패하면
(파일이 열려 잠김 등) 그 항목은 옛 위치를 그대로 쓴다 — 빈 새 위치로 시작해 데이터가 사라진 것처럼 보이거나
두 곳으로 갈라지지 않게 하려는 것이다. 모든 모듈은 여기 상수만 쓴다. data/ 는 .gitignore 에 있다.

.excel_worker.pid 는 stop.bat · force_stop.bat 이 이름으로 읽으므로 프로젝트 폴더에 그대로 둔다.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

_SUBDIRS = ("settings", "customers", "trade_statements", "task_reports", "manual_quotes", "quote_archive", "cache")

README_TEXT = """컴퓨존 견적 자동화 — 데이터 폴더

이 폴더에 앱이 만든 업무 데이터가 모두 들어 있습니다. 프로그램을 업데이트해도 지워지지 않습니다(GitHub 에 올라가지 않음).
다른 PC 로 옮기거나 통째로 백업할 때는 이 data 폴더를 그대로 복사하면 됩니다.

  settings\\quote_info.json          견적처 · 공급자 · 추가 품목 금액 · 입금 계좌
  settings\\app_settings.json        백업 폴더 위치 · 견적 메일(Gmail) 계정 (앱 비밀번호는 이 PC 에서만 풀리게 암호화)
  customers\\customers.json          📒 거래처 목록
  trade_statements\\trade_statements.json   🧾 거래명세서 목록
  trade_statements\\signed\\          서명받은 거래명세서 스캔 · 사진
  task_reports\\task_reports.json    📋 작업내역서
  manual_quotes\\manual_quotes.json  📝 새 견적 만들기 작성본
  manual_quotes\\pdf\\              📤 견적서 올리기로 넣은 PDF 원본 (작성본마다 하나)
  quote_archive\\                    🗂️ 견적서 보관함 (index.json + 견적서 xlsx)
  cache\\                            Apple · 컴퓨존 수집 스냅샷 (지워도 다음 수집 때 다시 만들어짐)

앱이 켜져 있을 때 이 파일들을 직접 고치지 마세요(앱이 저장하면서 덮어씁니다).
"""

# (새 위치, 옛 위치) — 옛 위치는 프로젝트 폴더 바로 아래
_PLAN: dict[str, tuple[Path, Path]] = {
    "quote_info": (DATA_DIR / "settings" / "quote_info.json", ROOT / "quote_info.json"),
    "app_settings": (DATA_DIR / "settings" / "app_settings.json", ROOT / "app_settings.json"),
    "customers": (DATA_DIR / "customers" / "customers.json", ROOT / "customers.json"),
    "trade_statements": (DATA_DIR / "trade_statements" / "trade_statements.json", ROOT / "trade_statements.json"),
    "trade_signed": (DATA_DIR / "trade_statements" / "signed", ROOT / "trade_statements"),
    "task_reports": (DATA_DIR / "task_reports" / "task_reports.json", ROOT / "task_reports.json"),
    "manual_quotes": (DATA_DIR / "manual_quotes" / "manual_quotes.json", ROOT / "manual_quotes.json"),
    "quote_archive": (DATA_DIR / "quote_archive", ROOT / "quote_archive"),
    "apple_cache": (DATA_DIR / "cache" / "apple_catalog_cache.json", ROOT / "apple_catalog_cache.json"),
    "parts_cache": (DATA_DIR / "cache" / "compuzone_parts_cache.json", ROOT / "compuzone_parts_cache.json"),
}

# 마지막 이전 결과(시작 로그 · 시험용): {키: "moved" | "kept-legacy" | "conflict" | "new" | "ok"}
MIGRATION_LOG: dict[str, str] = {}


def _warn(msg: str) -> None:
    try:
        print(f"[data] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _is_empty_dir(p: Path) -> bool:
    try:
        return p.is_dir() and not any(p.iterdir())
    except OSError:
        return False


def _migrate_one(key: str, new: Path, old: Path) -> Path:
    """옛 위치 → 새 위치로 옮기고, 실제로 쓸 경로를 돌려준다."""
    old_exists = old.exists() and not _is_empty_dir(old)
    new_exists = new.exists() and not _is_empty_dir(new)
    if not old_exists:
        MIGRATION_LOG[key] = "ok" if new_exists else "new"
        return new
    if new_exists:
        MIGRATION_LOG[key] = "conflict"
        _warn(f"{old.name}: 옛 위치({old})와 새 위치({new})에 모두 있습니다. 새 위치를 쓰고 옛 것은 그대로 둡니다.")
        return new
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        if _is_empty_dir(new):
            new.rmdir()  # 빈 새 폴더가 미리 생겼으면 치우고 통째로 옮긴다
        shutil.move(str(old), str(new))
        MIGRATION_LOG[key] = "moved"
        _warn(f"{old.name} → {new.relative_to(ROOT)} 로 옮겼습니다.")
        return new
    except OSError as e:
        # 일부만 옮겨졌으면 옛 위치에 남은 것을 계속 쓴다(새 위치에 생긴 조각은 지우지 않음)
        MIGRATION_LOG[key] = "kept-legacy"
        _warn(f"{old.name} 을(를) 옮기지 못해 옛 위치를 계속 씁니다: {e}")
        return old


def _setup() -> dict[str, Path]:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for sub in _SUBDIRS:
            (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _warn(f"data 폴더를 만들지 못했습니다: {e}")
    resolved = {key: _migrate_one(key, new, old) for key, (new, old) in _PLAN.items()}
    readme = DATA_DIR / "README.txt"
    try:
        if not readme.exists() or readme.read_text(encoding="utf-8-sig") != README_TEXT:
            readme.write_text(README_TEXT, encoding="utf-8-sig")
    except OSError:
        pass
    return resolved


_P = _setup()

QUOTE_INFO_FILE: Path = _P["quote_info"]
APP_SETTINGS_FILE: Path = _P["app_settings"]
CUSTOMERS_FILE: Path = _P["customers"]
TRADE_STATEMENTS_FILE: Path = _P["trade_statements"]
TRADE_SIGNED_DIR: Path = _P["trade_signed"]
TASK_REPORTS_FILE: Path = _P["task_reports"]
MANUAL_QUOTES_FILE: Path = _P["manual_quotes"]
QUOTE_ARCHIVE_DIR: Path = _P["quote_archive"]
APPLE_CACHE_FILE: Path = _P["apple_cache"]
PARTS_CACHE_FILE: Path = _P["parts_cache"]
# 📤 견적서 올리기로 PDF 에서 만든 작성본의 원본 PDF (옛 위치 없음 — 이전 대상 아님)
MANUAL_PDF_DIR: Path = MANUAL_QUOTES_FILE.parent / "pdf"


def describe() -> dict[str, str]:
    """화면 · 설명용: 각 데이터가 실제로 있는 곳(프로젝트 폴더 기준 상대 경로)."""
    out = {}
    for key, p in _P.items():
        try:
            out[key] = str(p.relative_to(ROOT))
        except ValueError:
            out[key] = str(p)
    return out
