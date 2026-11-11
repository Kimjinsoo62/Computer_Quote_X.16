# -*- coding: utf-8 -*-
"""견적서 Excel → PDF 변환 (이 PC 의 Excel, COM).

카드 서식 미리보기 창이 실제 견적서를 PDF 로 보여 주고(목록 썸네일도 그 PDF 를 그림으로 바꾼 것),
견적 메일에 PDF 를 첨부할 때 쓴다. Excel 이 없는 PC 에서는 RuntimeError 를 내고, 화면은 샘플 그림으로 대신한다.

빠르게 하려고 세 가지를 한다.
1. 상주 워커: PowerShell 하나가 숨긴 Excel 을 띄워 둔 채 stdin 으로 "xlsx<TAB>pdf" 요청을 받아 변환한다.
   매번 Excel 을 띄우고 닫는 시간(약 5초)과 첫 내보내기 예열 시간이 없어진다. 앱 시작 때 미리 띄우고(prewarm),
   IDLE_QUIT_SEC 동안 안 쓰면 닫는다. 서버가 죽으면 stdin 이 끊겨 워커가 Excel 을 닫고 스스로 끝난다.
   워커를 WORKER_COUNT 개 두고 서식별로 나눠 동시에 변환한다(폼1~8: 1개 약 7초 → 3개 약 2.5초).
   혹시 남을 때를 위해 PID 를 WORKER_PID_FILE 에 적어 두고 stop.bat 이 그것만 정리한다(사용자 Excel 은 건드리지 않음).
2. 빠른 프린터: Excel 의 PDF 내보내기는 활성 프린터 드라이버에 쪽 배치를 묻는다. 기본 프린터가 네트워크 복합기면
   8장에 12~16초가 걸려, 이 Excel 인스턴스 안에서만 'Microsoft XPS Document Writer' 로 바꾼다(약 3~7초).
   Windows 기본 프린터는 바꾸지 않는다. 바꿀 수 없으면 기본 프린터 그대로 쓴다.
3. 캐시: 같은 key(카드 · 서식 · 견적정보 · 품목)의 PDF · 썸네일은 CACHE_TTL_SEC 동안 다시 만들지 않고,
   동시에 같은 key 를 요청하면 한 번만 만든다(cached).

Excel 이 내보내기 중에 띄우는 "게시 중..." 진행 창은 _hider_loop 가 바로 숨긴다(앱 시작 예열 · 미리보기 때 화면에 안 보이게).
"""
from __future__ import annotations

import atexit
import base64
import io
import os
import queue
import subprocess
import tempfile
import threading
import time
import winreg
from pathlib import Path

import pypdfium2 as pdfium

CONVERT_TIMEOUT_SEC = 90
START_TIMEOUT_SEC = 60
IDLE_QUIT_SEC = 15 * 60  # 이만큼 안 쓰면 상주 Excel 을 닫는다(다음 요청 때 다시 띄움)
CACHE_TTL_SEC = 10 * 60  # 같은 카드 · 같은 서식을 다시 열면 Excel 을 다시 쓰지 않는다
CACHE_MAX = 120  # 카드 한 건이 서식별 PDF 8 + 썸네일 8 = 16칸을 쓴다
# 상주 Excel 수(하나에 메모리 약 150MB). 코어가 적은 PC 에서는 줄인다.
WORKER_COUNT = max(1, min(3, (os.cpu_count() or 2) // 2))
FAST_PRINTER = "Microsoft XPS Document Writer"
WORKER_PID_FILE = Path(__file__).resolve().parent.parent / ".excel_worker.pid"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, bytes]] = {}
_inflight: dict[str, threading.Event] = {}

WORKER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$out = New-Object IO.StreamWriter([Console]::OpenStandardOutput(), (New-Object Text.UTF8Encoding($false)))
$out.AutoFlush = $true
$in = New-Object IO.StreamReader([Console]::OpenStandardInput(), [Text.Encoding]::UTF8)
Add-Type -Namespace QuoteApp -Name Win -MemberDefinition '[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);'
$app = New-Object -ComObject Excel.Application
$app.Visible = $false
$app.DisplayAlerts = $false
$app.ScreenUpdating = $false
$excelPid = [uint32]0
[void][QuoteApp.Win]::GetWindowThreadProcessId([IntPtr]$app.Hwnd, [ref]$excelPid)
$out.WriteLine("READY $excelPid")
$printerSet = $false
try {
  while ($true) {
    $line = $in.ReadLine()
    if ($null -eq $line -or $line -eq 'QUIT') { break }
    $req = $line -split "`t"
    try {
      $wb = $app.Workbooks.Open($req[0], 0, $true)
      if (-not $printerSet) {
        # 활성 프린터는 통합 문서가 열려 있어야 바꿀 수 있다. 이름 형식은 언어마다 달라("Ne00:에 있는 X" / "X on Ne00:")
        # 지금 값에서 프린터 이름만 바꾸고 포트 번호(NeNN)는 0~30 을 차례로 시도한다.
        $printerSet = $true
        try {
          $orig = $app.ActivePrinter
          $defName = ((Get-ItemProperty 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Windows').Device -split ',')[0]
          if ($defName -and $orig.Contains($defName) -and $defName -ne '{FAST}') {
            $tmpl = $orig.Replace($defName, '{FAST}')
            for ($i = 0; $i -le 30; $i++) {
              try { $app.ActivePrinter = ($tmpl -replace 'Ne\d\d:', ('Ne{0:D2}:' -f $i)); break } catch { }
            }
          }
        } catch { }
      }
      # xlTypePDF=0, xlQualityStandard=0, IncludeDocProperties, IgnorePrintAreas=false
      $wb.ExportAsFixedFormat(0, $req[1], 0, $true, $false)
      $wb.Close($false)
      $out.WriteLine('OK')
    } catch {
      $msg = ($_.Exception.Message -replace '\s+', ' ')
      try { if ($wb) { $wb.Close($false) } } catch { }
      $out.WriteLine("ERR $msg")
    } finally {
      $wb = $null
    }
  }
} finally {
  $app.Quit()
  [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($app)
}
""".replace("{FAST}", FAST_PRINTER)


# ── "게시 중..." 진행 창 숨기기 ─────────────────────────────────────────────
# Excel 은 Visible=$false 여도 ExportAsFixedFormat 중에 "게시 중..." 진행 창(클래스 CMsoProgressBarWindow)을
# 화면에 띄운다. 워커 PowerShell 은 내보내기 동안 막혀 있으므로 서버 쪽 스레드가 대신 숨긴다.
# 1) SetWinEventHook(EVENT_OBJECT_SHOW): 창이 뜨는 즉시 알림을 받아 숨긴다.
# 2) 0.1초 간격 훑기: 훅이 놓친 창(훅 설치 전에 뜬 창 등)을 변환 중에만 찾아 숨긴다.
# 대상은 워커가 띄운 Excel PID 의 그 진행 창뿐이다(사용자 Excel · 다른 창은 건드리지 않음).
import ctypes
from ctypes import wintypes

_PROGRESS_CLASS = "CMsoProgressBarWindow"
_EVENT_OBJECT_SHOW = 0x8002
_OBJID_WINDOW = 0
_SW_HIDE = 0
_PM_REMOVE = 0x0001
_QS_ALLINPUT = 0x04FF
_SWEEP_SEC = 0.1

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_WinEventProc = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD
)
_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.SetWinEventHook.restype = wintypes.HANDLE
_user32.SetWinEventHook.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, _WinEventProc, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD
]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
_user32.MsgWaitForMultipleObjects.argtypes = [
    wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD
]
_user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]

_hider_lock = threading.Lock()
_hider_started = False
_converting = 0  # 지금 변환 중인 워커 수(0 이면 훑기를 쉰다)


def _worker_excel_pids() -> set[int]:
    return {w.excel_pid for w in _workers if w.excel_pid}


def _hide_if_progress(hwnd, pids: set[int]) -> None:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value not in pids:
        return
    cls = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(hwnd, cls, 64)
    if cls.value == _PROGRESS_CLASS and _user32.IsWindowVisible(hwnd):
        # Excel UI 스레드는 내보내기로 바쁘니 동기 ShowWindow 대신 비동기로 숨긴다
        _user32.ShowWindowAsync(hwnd, _SW_HIDE)


def _on_show(_hook, _event, hwnd, id_object, id_child, _thread, _time) -> None:
    if id_object != _OBJID_WINDOW or id_child != 0 or not hwnd:
        return
    try:
        pids = _worker_excel_pids()
        if pids:
            _hide_if_progress(hwnd, pids)
    except Exception:  # 콜백 안 예외는 조용히 넘긴다
        pass


def _on_enum(hwnd, _lparam) -> bool:
    try:
        _hide_if_progress(hwnd, _worker_excel_pids())
    except Exception:
        pass
    return True


_on_show_cb = _WinEventProc(_on_show)  # GC 되지 않게 모듈 전역에 둔다
_on_enum_cb = _EnumWindowsProc(_on_enum)


def _hider_loop() -> None:
    # 훅은 설치한 스레드의 메시지 루프로 전달된다(WINEVENT_OUTOFCONTEXT=0)
    hook = _user32.SetWinEventHook(_EVENT_OBJECT_SHOW, _EVENT_OBJECT_SHOW, None, _on_show_cb, 0, 0, 0)
    msg = wintypes.MSG()
    while True:
        _user32.MsgWaitForMultipleObjects(0, None, False, int(_SWEEP_SEC * 1000), _QS_ALLINPUT)
        while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        if _converting or not hook:
            _user32.EnumWindows(_on_enum_cb, 0)


def _ensure_hider() -> None:
    global _hider_started
    with _hider_lock:
        if not _hider_started:
            _hider_started = True
            threading.Thread(target=_hider_loop, name="excel-progress-hider", daemon=True).start()


def excel_available() -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application\CLSID"))
        return True
    except OSError:
        return False


class _Worker:
    """상주 PowerShell + 숨긴 Excel 하나. 한 번에 한 요청만 처리한다(_free 큐에서 꺼낸 쪽만 부른다)."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.excel_pid = 0
        self.lines: queue.Queue = queue.Queue()
        self.last_used = 0.0

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        encoded = base64.b64encode(WORKER_SCRIPT.encode("utf-16-le")).decode("ascii")
        self.lines = queue.Queue()
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        threading.Thread(target=self._pump, args=(self.proc, self.lines), daemon=True).start()
        line = self._read(START_TIMEOUT_SEC)
        if not line or not line.startswith("READY"):
            self.kill()
            raise RuntimeError("Excel 을 시작하지 못했습니다.")
        try:
            self.excel_pid = int(line.split()[1])
        except (IndexError, ValueError):
            self.excel_pid = 0
        self.last_used = time.time()

    @staticmethod
    def _pump(proc: subprocess.Popen, lines: queue.Queue) -> None:
        for raw in proc.stdout:
            lines.put(raw.decode("utf-8", "replace").strip())
        lines.put(None)  # 워커가 끝났다

    def _read(self, timeout: float) -> str | None:
        try:
            return self.lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def convert(self, xlsx: Path, pdf: Path) -> None:
        global _converting
        with _hider_lock:
            _converting += 1
        try:
            self._convert(xlsx, pdf)
        finally:
            with _hider_lock:
                _converting -= 1

    def _convert(self, xlsx: Path, pdf: Path) -> None:
        try:
            self.proc.stdin.write(f"{xlsx}\t{pdf}\n".encode("utf-8"))
            self.proc.stdin.flush()
        except OSError as e:
            self.kill()
            raise ConnectionError("Excel 워커가 끝나 있습니다.") from e
        line = self._read(CONVERT_TIMEOUT_SEC)
        self.last_used = time.time()
        if line is None:
            self.kill()
            raise ConnectionError("Excel 이 응답하지 않습니다.")
        if line != "OK":
            raise RuntimeError("PDF 변환에 실패했습니다. " + line.removeprefix("ERR ")[:200])

    def stop(self) -> None:
        if self.alive():
            try:
                self.proc.stdin.write(b"QUIT\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.kill()

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        if self.excel_pid:
            # 이 워커가 띄운 Excel 만 정리한다(이미 닫혔으면 아무 일도 없음)
            subprocess.run(
                ["taskkill", "/PID", str(self.excel_pid), "/F"],
                capture_output=True,
                creationflags=_NO_WINDOW,
            )
        self.proc = None
        self.excel_pid = 0


_workers = [_Worker() for _ in range(WORKER_COUNT)]
_free: queue.Queue = queue.Queue()  # 지금 놀고 있는 워커
for _w in _workers:
    _free.put(_w)
_pid_lock = threading.Lock()
_idle_thread_started = False
_idle_thread_lock = threading.Lock()


def _write_pid_file() -> None:
    """살아 있는 워커의 PowerShell · Excel PID 를 모두 적는다(stop.bat 비상 정리용). 없으면 파일을 지운다."""
    with _pid_lock:
        pids = [p for w in _workers if w.alive() for p in (w.proc.pid, w.excel_pid) if p]
        try:
            if pids:
                WORKER_PID_FILE.write_text("".join(f"{p}\n" for p in pids), encoding="ascii")
            else:
                WORKER_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def _ensure_alive(w: _Worker) -> None:
    global _idle_thread_started
    _ensure_hider()
    if not w.alive():
        w.kill()  # 죽은 워커가 남긴 Excel 정리
        try:
            w.start()
        finally:
            _write_pid_file()
    with _idle_thread_lock:
        if not _idle_thread_started:
            _idle_thread_started = True
            threading.Thread(target=_idle_watch, daemon=True).start()


def _idle_watch() -> None:
    while True:
        time.sleep(30)
        for _ in range(WORKER_COUNT):
            try:
                w = _free.get_nowait()  # 변환 중인 워커는 큐에 없으니 건드리지 않는다
            except queue.Empty:
                break
            try:
                if w.alive() and time.time() - w.last_used > IDLE_QUIT_SEC:
                    w.stop()
                    _write_pid_file()
            finally:
                _free.put(w)


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _proc_name_and_start(pid: int) -> tuple[str, int] | None:
    """(실행 파일 이름 소문자, 시작 시각 FILETIME 정수). 없거나 못 열면 None."""
    h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _kernel32.GetProcessTimes(h, *[ctypes.byref(t) for t in times]):
            return None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return os.path.basename(buf.value).lower(), created
    finally:
        _kernel32.CloseHandle(h)


def cleanup_stale_workers() -> list[int]:
    """이전 서버가 비정상 종료돼 남긴 미리보기 Excel · PowerShell 을 정리한다(앱 시작 때 1회).

    서버가 죽으면 워커 PowerShell 이 Excel.Quit() 을 못 하고 끝나 '/automation' Excel 이 남을 수 있다.
    그 PID 는 .excel_worker.pid 에 남아 있으므로 stop.bat 과 같은 규칙(EXCEL · powershell 이름만)으로
    그것만 닫는다. PID 재사용에 대비해 이 서버보다 먼저 시작된 프로세스만 대상으로 한다. 사용자 Excel 은 건드리지 않는다.
    """
    try:
        pids = [int(x) for x in WORKER_PID_FILE.read_text(encoding="ascii").split() if x.strip().isdigit()]
    except (OSError, ValueError):
        return []
    me = _proc_name_and_start(os.getpid())
    killed = []
    for pid in pids:
        info = _proc_name_and_start(pid)
        if not info or info[0] not in ("excel.exe", "powershell.exe"):
            continue
        if me and info[1] >= me[1]:
            continue  # 이 서버 이후에 생긴 프로세스(PID 재사용) — 건드리지 않는다
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=_NO_WINDOW)
        killed.append(pid)
    try:
        WORKER_PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    return killed


def prewarm(make_xlsx) -> None:
    """앱 시작 때 백그라운드에서 워커 Excel 들을 띄우고 make_xlsx() 를 한 번씩 변환해 둔다. 실패는 조용히 넘긴다.

    Excel 은 서식 · 글꼴이 같은 통합 문서를 처음 내보낼 때만 오래 걸린다(폼1~8: 첫 회 약 8초 → 이후 약 4초).
    빈 통합 문서로는 이 예열이 되지 않아, 폼1~8 샘플 견적을 넘겨받는다.
    """

    def _run() -> None:
        try:
            xlsx_to_pdf(make_xlsx())
        except (OSError, RuntimeError):
            pass

    if not excel_available():
        return
    # 워커 수만큼 동시에 부르면 각자 다른 워커를 하나씩 잡는다
    for _ in range(WORKER_COUNT):
        threading.Thread(target=_run, daemon=True).start()


def stop_worker() -> None:
    """앱 종료 때 상주 Excel 을 모두 닫는다. 변환 중이라 못 잡은 워커는 강제로 정리한다."""
    deadline = time.time() + 15
    got = []
    for _ in range(WORKER_COUNT):
        try:
            got.append(_free.get(timeout=max(0.1, deadline - time.time())))
        except queue.Empty:
            break
    for w in _workers:
        if w in got:
            w.stop()
        else:
            w.kill()
    for w in got:
        _free.put(w)
    _write_pid_file()


atexit.register(stop_worker)


def xlsx_to_pdf(xlsx_bytes: bytes) -> bytes:
    if not excel_available():
        raise RuntimeError("이 PC 에 Microsoft Excel 이 없어 PDF 를 만들 수 없습니다.")
    try:
        w = _free.get(timeout=CONVERT_TIMEOUT_SEC)
    except queue.Empty as e:
        raise RuntimeError("PDF 변환 대기열이 밀려 있습니다. 잠시 뒤 다시 시도해 주세요.") from e
    # Excel 이 파일을 잡고 있어도 임시 폴더 정리 오류가 원래 오류를 가리지 않게 한다
    try:
        with tempfile.TemporaryDirectory(prefix="quote-pdf-", ignore_cleanup_errors=True) as tmp:
            xlsx = Path(tmp) / "quote.xlsx"
            pdf = Path(tmp) / "quote.pdf"
            xlsx.write_bytes(xlsx_bytes)
            for attempt in range(2):
                try:
                    _ensure_alive(w)
                    w.convert(xlsx, pdf)
                    break
                except ConnectionError as e:
                    # 워커가 죽어 있었다(유휴 종료 직후 · Excel 강제 종료 등) → 한 번 다시 띄워 재시도
                    _write_pid_file()
                    if attempt:
                        raise RuntimeError(str(e)) from e
            if not pdf.is_file() or pdf.stat().st_size == 0:
                raise RuntimeError("PDF 변환에 실패했습니다.")
            return pdf.read_bytes()
    finally:
        _free.put(w)


def cache_get(key: str) -> bytes | None:
    now = time.time()
    with _cache_lock:
        for k in [k for k, (at, _) in _cache.items() if now - at > CACHE_TTL_SEC]:
            _cache.pop(k, None)
        hit = _cache.get(key)
    return hit[1] if hit else None


def cache_put(key: str, data: bytes) -> None:
    with _cache_lock:
        if key not in _cache and len(_cache) >= CACHE_MAX:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]), None)
        _cache[key] = (time.time(), data)


def cached(key: str, make) -> bytes:
    """key 별로 make() 결과를 잠시 보관한다. 같은 key 를 동시에 요청하면(썸네일 + 크게 보기 등) 한 번만 만든다."""
    while True:
        data = cache_get(key)
        if data is not None:
            return data
        with _cache_lock:
            event = _inflight.get(key)
            owner = event is None
            if owner:
                event = _inflight[key] = threading.Event()
        if not owner:
            event.wait(CONVERT_TIMEOUT_SEC)
            continue  # 만든 쪽이 실패했으면 캐시에 없으니 이번엔 직접 만든다
        try:
            data = make()
            cache_put(key, data)
            return data
        finally:
            with _cache_lock:
                _inflight.pop(key, None)
            event.set()


def cached_pdf(key: str, make_xlsx) -> bytes:
    """key(요청 조건)별 PDF. openpyxl 은 만들 때마다 바이트가 달라 xlsx 해시로는 키를 못 만든다."""
    return cached(key, lambda: xlsx_to_pdf(make_xlsx()))


# PDFium 은 스레드 안전하지 않다. 엑셀 견적서 창이 폼1~8 썸네일을 동시에 요청하면 FastAPI 스레드풀에서
# 여러 스레드가 동시에 부르게 되는데, 잠금 없이 부르면 서버 프로세스가 로그 없이 통째로 죽는다(재현 3/3).
# PDFium 을 쓰는 곳은 반드시 이 잠금 안에서만 부른다. PNG 저장(Pillow)은 잠금 밖에서 한다.
_pdfium_lock = threading.Lock()


def render_png(pdf_bytes: bytes, width: int) -> bytes:
    """PDF 첫 쪽을 폭 width(px) PNG 로."""
    with _pdfium_lock:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            page = doc[0]
            bitmap = page.render(scale=width / page.get_width())
            img = bitmap.to_pil().copy()  # PDFium 버퍼와 떼어 낸 뒤 닫는다
            bitmap.close()
            page.close()
        finally:
            doc.close()
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
