@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

echo.
echo ==========================================
echo   오프라인 설치 패키지 다시 받기 (인터넷 필요)
echo ==========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [오류] .venv가 없습니다. 먼저 install.bat을 실행하세요.
    pause
    exit /b 1
)

set "PKG=%~dp0"
if not exist "%PKG%wheels" mkdir "%PKG%wheels"
if not exist "%PKG%python" mkdir "%PKG%python"

REM 현재 .venv에서 검증된 버전 그대로 받는다
echo [1/2] 라이브러리 휠 다운로드 (.venv 버전 기준)...
".venv\Scripts\python.exe" -m pip freeze > "%TEMP%\aipc_constraints.txt"
".venv\Scripts\python.exe" -m pip download -r requirements.txt pip -c "%TEMP%\aipc_constraints.txt" -d "%PKG%wheels" --only-binary=:all:
if errorlevel 1 (
    echo [오류] 휠 다운로드 실패
    pause
    exit /b 1
)
del "%TEMP%\aipc_constraints.txt" 2>nul

echo.
echo [2/2] Python 3.12.10 설치 파일 다운로드...
if exist "%PKG%python\python-3.12.10-amd64.exe" (
    echo   - 이미 있음, 건너뜀
) else (
    curl -fL -o "%PKG%python\python-3.12.10-amd64.exe" https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
    if errorlevel 1 (
        echo [오류] Python 설치 파일 다운로드 실패
        pause
        exit /b 1
    )
)

echo.
echo   완료: %PKG%
echo.
pause
exit /b 0
