@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ==========================================
echo   AI PC Quote - 최초 설치 (install.bat)
echo ==========================================
echo.

REM ── 0) 압축을 풀지 않고 ZIP 안에서 실행했는지 확인 ──
if not exist "%~dp0app\main.py" (
    echo [오류] 프로그램 파일^(app 폴더^)을 찾을 수 없습니다.
    echo.
    echo   GitHub에서 받은 ZIP 파일을 먼저 압축 해제하세요.
    echo   ZIP 파일 오른쪽 클릭 - [압축 풀기] 후, 풀린 폴더 안의 install.bat 을 실행합니다.
    echo   경로가 너무 길면 설치가 실패할 수 있으니 C:\ 처럼 짧은 위치에 푸는 것을 권장합니다.
    echo.
    pause
    exit /b 1
)

REM ── 1) Python 확인 ──
echo [1/4] Python 설치 확인...
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo [오류] Python이 설치되어 있지 않거나 PATH에 없습니다.
    echo.
    echo   1. https://www.python.org/downloads/ 에서 Python 3.10 이상 설치
    echo      ^(인터넷이 없으면 "Offline Install Package\python\python-3.12.10-amd64.exe" 실행^)
    echo   2. 설치 시 "Add python.exe to PATH" 체크
    echo   3. 설치 후 명령 프롬프트를 다시 열고 install.bat 재실행
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%v in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2^>nul') do set "PYVER=%%v"
echo   - Python %PYVER% 확인됨

python -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [오류] Python 3.10 이상이 필요합니다. 현재: %PYVER%
    echo.
    pause
    exit /b 1
)

REM ── 2) 가상환경 생성 ──
echo.
echo [2/4] 가상환경 생성 (.venv)...
set "NEED_VENV=1"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; exit(0)" >nul 2>&1
    if not errorlevel 1 (
        set "NEED_VENV=0"
        echo   - 기존 .venv 사용
    ) else (
        echo   - 기존 .venv가 다른 PC/경로를 가리켜 재생성합니다...
        rmdir /s /q ".venv" 2>nul
    )
)
if "!NEED_VENV!"=="1" (
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo [오류] 가상환경 생성 실패. python -m venv 모듈을 확인하세요.
        echo.
        pause
        exit /b 1
    )
    echo   - .venv 생성 완료
)

set "PY=%~dp0.venv\Scripts\python.exe"
set "PIP=%~dp0.venv\Scripts\python.exe -m pip"

REM ── 3) pip / 의존성 설치 ──
REM "Offline Install Package\wheels"에 휠이 있으면 인터넷 없이 먼저 설치를 시도하고,
REM 실패하면(Python 버전 불일치 등) 아래 온라인 설치로 넘어간다.
set "WHEELS=%~dp0Offline Install Package\wheels"
if exist "%WHEELS%\*.whl" (
    echo.
    echo [3/4] 오프라인 설치 ^(Offline Install Package\wheels^)...
    "%PY%" -m pip install --no-index --find-links "%WHEELS%" --upgrade pip
    "%PY%" -m pip install --no-index --find-links "%WHEELS%" -r "%~dp0requirements.txt"
    if not errorlevel 1 (
        echo   - 오프라인 설치 완료
        goto :verify
    )
    echo   - 오프라인 설치 실패 ^(휠은 Python 3.12용^). 온라인 설치를 시도합니다...
)

echo.
echo [3/4] pip 업그레이드 및 라이브러리 설치...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo   - [경고] pip 업그레이드 실패. 기존 pip 로 계속 설치합니다.
)

"%PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo [오류] requirements.txt 설치 실패
    echo.
    pause
    exit /b 1
)

echo   - requirements.txt 설치 완료

REM ── 4) 설치 검증 ──
:verify
echo.
echo [4/4] 설치 검증...
"%PY%" -c "import fastapi, uvicorn, jinja2, openpyxl, PIL, pypdfium2; print('   - fastapi, uvicorn, jinja2, openpyxl, Pillow, pypdfium2 OK')"
if errorlevel 1 (
    echo.
    echo [오류] 패키지 import 검증 실패
    echo.
    pause
    exit /b 1
)

"%PY%" -c "from app.main import app; print('   - app.main 로드 OK')"
if errorlevel 1 (
    echo.
    echo [오류] 앱 모듈 로드 실패 ^(app 폴더 위치 확인^)
    echo.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   설치 완료
echo.
echo   설치된 패키지 (requirements.txt):
echo     - fastapi, uvicorn, jinja2
echo     - openpyxl, python-multipart, Pillow, pypdfium2
echo.
echo   가상환경: .venv
echo.
echo   다음 단계:
echo     START.bat    서버 시작 (http://127.0.0.1:8090)
echo     stop.bat     서버 중지
echo ==========================================
echo.
REM START.bat calls with /auto: continue without waiting
if /I not "%~1"=="/auto" pause
exit /b 0
