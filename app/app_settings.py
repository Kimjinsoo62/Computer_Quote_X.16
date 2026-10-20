# -*- coding: utf-8 -*-
"""앱 설정(app_settings.json). 견적 정보(quote_info.json)와 분리해 둔다.

quote_info.save_quote_info() 는 화면 모달이 보내지 않은 키를 기본값으로 되돌리므로,
백업 폴더처럼 모달 밖에서 정하는 값은 여기에 둔다.
"""
from __future__ import annotations

import json
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "app_settings.json"


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def get_setting(key: str, default=None):
    return load_settings().get(key, default)


def set_setting(key: str, value) -> dict:
    data = load_settings()
    if value in (None, ""):
        data.pop(key, None)
    else:
        data[key] = value
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
