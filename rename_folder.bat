@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul

REM ── 프로젝트 폴더 이름을 현재 버전(app\main.py 의 FastAPI version)에 맞춰 바꾼다 ──
REM 실행 중인 배치 파일이 들어 있는 폴더는 이름을 바꿀 수 없으므로,
REM 자기 자신을 %TEMP% 로 복사해 거기서 다시 실행한다 (인자 1 = 프로젝트 폴더).

if "%~1"=="" (
    set "SELF_TMP=%TEMP%\cq_rename_folder.bat"
    copy /y "%~f0" "!SELF_TMP!" >nul
    set "PROJ=%~dp0"
    if "!PROJ:~-1!"=="\" set "PROJ=!PROJ:~0,-1!"
    cd /d "%TEMP%"
    "!SELF_TMP!" "!PROJ!"
    exit /b
)

set "PROJ=%~1"
cd /d "%TEMP%"

echo.
echo ==========================================
echo   AI PC Quote - 폴더 이름 바꾸기 (rename_folder.bat)
echo ==========================================
echo.

if not exist "%PROJ%\app\main.py" (
    echo [오류] 프로젝트 폴더를 찾을 수 없습니다: %PROJ%
    goto :end
)

REM 1) 버전 읽기
set "VER="
for /f "usebackq delims=" %%v in (`powershell -NoProfile -Command "$m = Select-String -Path '%PROJ%\app\main.py' -Pattern 'version=.(X\.[0-9]+)' | Select-Object -First 1; if ($m) { $m.Matches[0].Groups[1].Value }"`) do set "VER=%%v"
if "%VER%"=="" (
    echo [오류] app\main.py 에서 버전을 읽지 못했습니다.
    goto :end
)

for %%p in ("%PROJ%") do (
    set "PARENT=%%~dpp"
    set "OLDNAME=%%~nxp"
)
set "NEWNAME=Computer_Quote_%VER%"
set "NEWPATH=%PARENT%%NEWNAME%"

echo   현재 폴더 : %PROJ%
echo   현재 버전 : %VER%
echo   바꿀 이름 : %NEWNAME%
echo.

if /i "%OLDNAME%"=="%NEWNAME%" (
    echo [안내] 폴더 이름이 이미 버전과 같습니다. 바꿀 필요가 없습니다.
    goto :end
)
if exist "%NEWPATH%" (
    echo [오류] 같은 이름의 폴더가 이미 있습니다: %NEWPATH%
    echo        기존 폴더를 옮기거나 지운 뒤 다시 실행하세요.
    goto :end
)

REM 2) 서버가 켜져 있으면 중단 (폴더 안 파일을 쓰고 있어 이름을 바꿀 수 없음)
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
    echo [중단] 앱 서버(포트 8090^)가 실행 중입니다.
    echo        앱의 [종료] 버튼이나 stop.bat 으로 먼저 끈 뒤 다시 실행하세요.
    goto :end
)

choice /c YN /m "폴더 이름을 바꿀까요"
if errorlevel 2 (
    echo 취소했습니다.
    goto :end
)

REM 3) 이름 바꾸기
ren "%PROJ%" "%NEWNAME%"
if errorlevel 1 (
    echo.
    echo [오류] 이름을 바꾸지 못했습니다. 이 폴더를 연 탐색기 · VS Code · 명령 창 · Claude Code 등을
    echo        모두 닫고 다시 실행하세요.
    goto :end
)

echo.
echo [완료] %OLDNAME%  →  %NEWNAME%
echo        바탕화면 바로가기 등이 예전 경로를 가리키면 새 폴더의 START.bat 으로 다시 만드세요.
start "" explorer "%NEWPATH%"

:end
echo.
pause
REM 임시 복사본은 다음 실행 때 덮어쓴다 (실행 중인 자신은 지울 수 없음)
endlocal
