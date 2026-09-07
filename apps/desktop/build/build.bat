@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ─────────────────────────────────────────────────────────────────────────────
rem  build.bat — local build launcher for Mocha Tools
rem
rem  Wraps build.py (the unified orchestrator) with interactive prompts.
rem
rem  Usage:
rem      build.bat [os] [variant] [version]
rem
rem    os      = windows | linux | macos     (default: prompt)
rem    variant = portable | installer | all  (default: prompt)
rem    version = e.g. 7.1.0                  (optional; default: from VERSION file)
rem
rem  Examples:
rem      build.bat windows all 7.1.0
rem      build.bat linux
rem      build.bat
rem ─────────────────────────────────────────────────────────────────────────────

set "APP_ROOT=%~dp0.."
set "OS_ARG=%~1"
set "VARIANT_ARG=%~2"
set "VERSION_ARG=%~3"

rem ── 1. Choose target OS ─────────────────────────────────────────────────────
set "OS="
if /i "%OS_ARG%"=="windows" set "OS=windows"
if /i "%OS_ARG%"=="linux"   set "OS=linux"
if /i "%OS_ARG%"=="macos"   set "OS=macos"
if defined OS_ARG if not defined OS (
    echo Invalid OS: %OS_ARG%  ^(expected windows, linux, or macos^)
    exit /b 1
)
if not defined OS (
    echo.
    echo Choose target OS:
    echo   1^) Windows
    echo   2^) Linux
    echo   3^) macOS
    set /p "OS=Enter 1, 2, or 3 [1]: "
    if "!OS!"=="2" (
        set "OS=linux"
    ) else if "!OS!"=="3" (
        set "OS=macos"
    ) else (
        set "OS=windows"
    )
)

rem ── 2. Choose variant ───────────────────────────────────────────────────────
set "VARIANT="
if /i "%VARIANT_ARG%"=="portable"   set "VARIANT=portable"
if /i "%VARIANT_ARG%"=="installer"  set "VARIANT=installer"
if /i "%VARIANT_ARG%"=="all"        set "VARIANT=all"
if defined VARIANT_ARG if not defined VARIANT (
    echo Invalid variant: %VARIANT_ARG%  ^(expected portable, installer, or all^)
    exit /b 1
)
if not defined VARIANT (
    echo.
    echo Choose build variant:
    echo   1^) Portable
    echo   2^) Installer
    echo   3^) All
    set /p "VARIANT=Enter 1, 2, or 3 [3]: "
    if "!VARIANT!"=="1" (
        set "VARIANT=portable"
    ) else if "!VARIANT!"=="2" (
        set "VARIANT=installer"
    ) else (
        set "VARIANT=all"
    )
)

rem ── 3. Choose version (optional) ────────────────────────────────────────────
set "VERSION=%VERSION_ARG%"
if not defined VERSION (
    set "DEFAULT_VERSION="
    if exist "%APP_ROOT%\VERSION" (
        for /f "tokens=2" %%V in ('findstr /b "version:" "%APP_ROOT%\VERSION"') do set "DEFAULT_VERSION=%%V"
    )
    if defined DEFAULT_VERSION (
        set /p "VERSION=Version [!DEFAULT_VERSION!]: "
        if not defined VERSION set "VERSION=!DEFAULT_VERSION!"
    ) else (
        set /p "VERSION=Enter version (e.g. 7.1.0, or leave blank for VERSION file): "
    )
)

rem ── 4. Warn when cross-compiling ────────────────────────────────────────────
if /i not "%OS%"=="windows" (
    echo.
    echo WARNING: Nuitka does not cross-compile. Building for %OS% must be done
    echo on a %OS% machine, and the packaging tools are platform-specific.
    echo This build will likely fail if run on Windows.
    echo.
    set "CONTINUE="
    set /p "CONTINUE=Continue anyway? [y/N]: "
    if /i not "!CONTINUE!"=="y" exit /b 1
)

rem ── 5. Set up virtualenv + install dependencies ─────────────────────────────
set "VENV=%APP_ROOT%\.venv"
set "PY=%VENV%\Scripts\python.exe"
if not exist "%PY%" (
    echo.
    echo Creating virtualenv...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo Failed to create virtualenv.
        exit /b 1
    )
)

echo.
echo Installing build dependencies...
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -r "%APP_ROOT%\requirements.txt"
if errorlevel 1 (
    echo Failed to install dependencies.
    exit /b 1
)

rem ── 6. Stop running app so the exe isn't locked (Windows only) ─────────────
if /i "%OS%"=="windows" (
    echo.
    echo Stopping any running Mocha Tools instances...
    taskkill /f /im "Mocha Tools.exe" >nul 2>&1
)

rem ── 7. Build ────────────────────────────────────────────────────────────────
echo.
if defined VERSION (
    echo Building Mocha Tools %VERSION% for %OS% ^(%VARIANT%^)...
    "%PY%" "%APP_ROOT%\build\build.py" --platform "%OS%" --variant "%VARIANT%" --version "%VERSION%"
) else (
    echo Building Mocha Tools for %OS% ^(%VARIANT%^) using VERSION file...
    "%PY%" "%APP_ROOT%\build\build.py" --platform "%OS%" --variant "%VARIANT%"
)
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Done. Output in %APP_ROOT%\dist\:
dir /b "%APP_ROOT%\dist"
endlocal