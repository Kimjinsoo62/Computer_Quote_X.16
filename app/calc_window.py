# -*- coding: utf-8 -*-
"""계산기를 견적 창과 별도 OS 창으로 연다. window.open 은 --app 모드에서 크기를 무시한다."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

CALC_W = 280
CALC_H = 540
CALC_TITLE = "계산기"  # templates/calc.html 의 <title> 과 같아야 한다(창을 앞으로 가져올 때 찾는 이름)
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


# ---------------------------------------------------------------------------
# 창을 맨 앞으로 — 서버(백그라운드 프로그램)가 띄운 창은 Windows 가 '앞으로 끼어들기'를 막아 메인 창 뒤에 뜬다.
# 그래서 창이 뜰 때까지 제목으로 찾아(Edge · Chrome 창만), 잠깐 '항상 위'로 올렸다 풀고 포커스를 준다.
# 제목은 명령줄 대신 환경 변수(CQ_WIN_TITLE)로 넘긴다(한글이 명령줄에서 깨지지 않게).
# ---------------------------------------------------------------------------
_FRONT_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class CqFront {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
}
"@
$title = $env:CQ_WIN_TITLE
$deadline = (Get-Date).AddSeconds([double]$env:CQ_WIN_WAIT)
while ((Get-Date) -lt $deadline) {
  $script:hit = [IntPtr]::Zero
  [CqFront]::EnumWindows({ param($h, $l)
    if (-not [CqFront]::IsWindowVisible($h)) { return $true }
    $sb = New-Object System.Text.StringBuilder 256
    [void][CqFront]::GetWindowText($h, $sb, 256)
    if ($sb.ToString() -ne $title) { return $true }
    $procId = 0; [void][CqFront]::GetWindowThreadProcessId($h, [ref]$procId)
    $p = Get-Process -Id $procId
    if ($p -and $p.ProcessName -match '^(msedge|chrome)$') { $script:hit = $h; return $false }
    return $true
  }, [IntPtr]::Zero) | Out-Null
  if ($script:hit -ne [IntPtr]::Zero) {
    $h = $script:hit
    if ([CqFront]::IsIconic($h)) { [void][CqFront]::ShowWindow($h, 9) }
    [CqFront]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [CqFront]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
    [void][CqFront]::SetWindowPos($h, [IntPtr](-1), 0, 0, 0, 0, 0x0043)
    [void][CqFront]::SetWindowPos($h, [IntPtr](-2), 0, 0, 0, 0, 0x0043)
    [void][CqFront]::SetForegroundWindow($h)
    exit 0
  }
  Start-Sleep -Milliseconds 250
}
exit 1
"""


def bring_to_front(title: str, wait_sec: float = 12.0) -> subprocess.Popen | None:
    """제목이 title 인 Edge/Chrome 창이 뜨기를 기다렸다가 맨 앞으로 가져온다(기다리지 않고 바로 돌아옴)."""
    try:
        env = dict(os.environ, CQ_WIN_TITLE=title, CQ_WIN_WAIT=str(wait_sec))
        return subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _FRONT_PS],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_no_window(),
        )
    except OSError:
        return None


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
        bring_to_front(CALC_TITLE, 3)
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
    bring_to_front(CALC_TITLE)  # 서버가 띄운 창은 메인 창 뒤에 뜨므로 앞으로 가져온다
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
        bring_to_front(MEMO_TITLE, 3)
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
    bring_to_front(MEMO_TITLE)  # 서버가 띄운 창은 메인 창 뒤에 뜨므로 앞으로 가져온다
    return {"ok": True, "already": False}
