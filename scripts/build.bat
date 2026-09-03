@echo off
setlocal
cd /d "%~dp0.."

rem ── Resolve version from VERSION file ─────────────────────────────────────
if not exist "VERSION" (
    echo VERSION file not found.
    exit /b 1
)
set /p "VERSION="<VERSION
set "VERSION=%VERSION:v=%"
if "%VERSION%"=="" (
    echo VERSION file is empty.
    exit /b 1
)

echo Mocha Tools build - version %VERSION%
echo.
echo Choose build type:
echo   1) Executable only (dist\Mocha Tools.exe)
echo   2) Executable + NSIS installer (dist\MochaTools-Setup-%VERSION%.exe)
set /p "CHOICE=Enter 1 or 2: "

if "%CHOICE%"=="1" (
    set "WITH_INSTALLER=0"
) else if "%CHOICE%"=="2" (
    set "WITH_INSTALLER=1"
) else (
    echo Invalid choice.
    exit /b 1
)

rem ── Set up minimal virtualenv ─────────────────────────────────────────────
set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"
if not exist "%PY%" (
    echo.
    echo Creating minimal virtualenv...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo Failed to create virtualenv.
        exit /b 1
    )
)

echo.
echo Installing build dependencies into virtualenv...
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    exit /b 1
)

rem ── Stamp version into constants.py + installer files ─────────────────────
echo.
echo Stamping version %VERSION%...
"%PY%" builditems/stamp_version.py "%VERSION%"
if errorlevel 1 (
    echo Failed to stamp version.
    exit /b 1
)

rem ── Clean stale build artifacts (env/deps may have changed) ───────────────
echo.
echo Cleaning stale build artifacts...
for %%D in (mochatools.build mochatools.dist mochatools.onefile-build) do (
    if exist "dist\%%D" rmdir /s /q "dist\%%D"
)

rem ── Stop any running app so the exe isn't locked ──────────────────────────
echo.
echo Stopping any running Mocha Tools instances...
taskkill /f /im "Mocha Tools.exe" >nul 2>&1
if exist "dist\Mocha Tools.exe" del /f /q "dist\Mocha Tools.exe" >nul 2>&1

rem ── Build executable with Nuitka ─────────────────────────────────────────
echo.
echo Building Mocha Tools %VERSION% with Nuitka...
"%PY%" -m nuitka --onefile ^
    --assume-yes-for-downloads ^
    --disable-cache=dll-dependencies ^
    --jobs=8 ^
    --enable-plugin=pyside6 ^
    --include-package=keyring ^
    --include-package-data=keyring ^
    --noinclude-pytest-mode=nofollow ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=./builditems/windows/icon.ico ^
    --company-name=nxllxvxxd2 ^
    --product-name="Mocha Tools" ^
    --file-version="%VERSION%" ^
    --product-version="%VERSION%" ^
    --output-filename="Mocha Tools.exe" ^
    --output-dir=dist ^
    mochatools.py
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

set "BIN=dist\Mocha Tools.exe"
if not exist "%BIN%" (
    echo Build finished but binary not found.
    exit /b 1
)

rem ── Build NSIS installer if requested ─────────────────────────────────────
if "%WITH_INSTALLER%"=="1" (
    echo.
    echo Building NSIS installer...
    if not exist "installer.nsi" (
        echo installer.nsi not found.
        exit /b 1
    )
    makensis installer.nsi
    if errorlevel 1 (
        echo NSIS build failed.
        exit /b 1
    )
)

echo.
echo Done. Output in dist\:
dir /b "dist"
endlocal
