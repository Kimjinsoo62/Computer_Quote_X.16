# -*- coding: utf-8 -*-
"""계산기를 견적 창과 별도 OS 창으로 연다. window.open 은 --app 모드에서 크기를 무시한다."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

CALC_W = 280
CALC_H = 540
_proc: subprocess.Popen | None = None


def _browser() -> Path | None:
    pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    for p in (
        pf86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        pf / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        pf / "Google" / "Chrome" / "Application" / "chrome.exe",
    ):
        if p.is_file():
            return p
    return None


def _profile_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    d = base / "AI-PC-Quote-Calc"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _no_window() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def open_calc_window(
    url: str,
    *,
    left: int = 80,
    top: int = 80,
    width: int = CALC_W,
    height: int = CALC_H,
) -> dict:
    """이미 있으면 앞으로 가져오고, 없으면 작은 --app 창을 연다."""
    global _proc
    if _proc is not None and _proc.poll() is None:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(New-Object -ComObject WScript.Shell).AppActivate('계산기') | Out-Null",
            ],
            capture_output=True,
            timeout=8,
            creationflags=_no_window(),
        )
        return {"ok": True, "already": True}

    browser = _browser()
    if browser is None:
        return {"ok": False, "reason": "browser"}

    width = max(268, min(int(width or CALC_W), 420))
    height = max(520, min(int(height or CALC_H), 800))
    left = int(left)
    top = int(top)

    _proc = subprocess.Popen(
        [
            str(browser),
            f"--app={url}",
            f"--user-data-dir={_profile_dir()}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={width},{height}",
            f"--window-position={left},{top}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_no_window(),
    )
    return {"ok": True, "already": False}


def close_calc_window() -> None:
    global _proc
    if _proc is None:
        return
    if _proc.poll() is None:
        try:
            _proc.terminate()
        except OSError:
            pass
    _proc = None


# ---------------------------------------------------------------------------
# 🗒️ 메모장 창 — 계산기와 같은 방식(별도 --app 창, 전용 프로필 AI-PC-Quote-Memo). 창 크기는 마우스로 조절한다.
# 프로필 폴더 이름이 종료 정리(shutdown_service · stop.bat)의 표식이므로 바꾸지 않는다.
# 창 제목(templates/memo.html 의 <title>)은 Windows 메모장(Notepad)과 헷갈리지 않게 '견적 메모장'으로 둔다.
# ---------------------------------------------------------------------------
MEMO_W = 448  # 사용자가 맞춘 창 크기(XII.53, 448×550)
MEMO_H = 550
MEMO_TITLE = "견적 메모장"
_memo_proc: subprocess.Popen | None = None


def _memo_profile_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    d = base / "AI-PC-Quote-Memo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def open_memo_window(
    url: str,
    *,
    left: int = 120,
    top: int = 80,
    width: int = MEMO_W,
    height: int = MEMO_H,
) -> dict:
    """이미 열려 있으면 앞으로 가져오고, 없으면 메모장 --app 창을 연다(크기 조절 가능)."""
    global _memo_proc
    if _memo_proc is not None and _memo_proc.poll() is None:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(New-Object -ComObject WScript.Shell).AppActivate('{MEMO_TITLE}') | Out-Null"],
            capture_output=True,
            timeout=8,
            creationflags=_no_window(),
        )
        return {"ok": True, "already": True}

    browser = _browser()
    if browser is None:
        return {"ok": False, "reason": "browser"}

    width = max(360, min(int(width or MEMO_W), 3840))
    height = max(400, min(int(height or MEMO_H), 2160))
    _memo_proc = subprocess.Popen(
        [
            str(browser),
            f"--app={url}",
            f"--user-data-dir={_memo_profile_dir()}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={width},{height}",
            f"--window-position={int(left)},{int(top)}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_no_window(),
    )
    return {"ok": True, "already": False}
