#Requires -Version 5.1

<#
.SYNOPSIS
    Local build launcher for Mocha Tools.

.DESCRIPTION
    Wraps build.py (the unified orchestrator) with interactive prompts.

.PARAMETER OS
    Target OS: windows, linux, or macos. If omitted, prompts the user.

.PARAMETER Variant
    Build variant: portable, installer, or all. If omitted, prompts the user.

.PARAMETER Version
    Version string (e.g. 7.1.0). If omitted, reads from VERSION file or prompts.

.EXAMPLE
    .\build.ps1 windows all 7.1.0
    .\build.ps1 linux
    .\build.ps1
#>

param(
    [ValidateSet("windows", "linux", "macos")]
    [string]$OS,

    [ValidateSet("portable", "installer", "all")]
    [string]$Variant,

    [string]$Version
)

$ErrorActionPreference = "Stop"
$AppRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

# ── 1. Choose target OS ─────────────────────────────────────────────────────
$currentOS = if ($IsWindows) { "windows" } elseif ($IsMacOS) { "macos" } elseif ($IsLinux) { "linux" } else { "windows" }

if (-not $OS) {
    $defaultNum = switch ($currentOS) {
        "windows" { "1" }
        "linux" { "2" }
        "macos" { "3" }
    }
    Write-Host ""
    Write-Host "Choose target OS:"
    Write-Host "  1) Windows"
    Write-Host "  2) Linux"
    Write-Host "  3) macOS"
    $choice = Read-Host "Enter 1, 2, or 3 [$defaultNum]"
    switch ($choice) {
        "1" { $OS = "windows" }
        "2" { $OS = "linux" }
        "3" { $OS = "macos" }
        default { $OS = $currentOS }
    }
}

# ── 2. Choose variant ───────────────────────────────────────────────────────
if (-not $Variant) {
    Write-Host ""
    Write-Host "Choose build variant:"
    Write-Host "  1) Portable"
    Write-Host "  2) Installer"
    Write-Host "  3) All"
    $choice = Read-Host "Enter 1, 2, or 3 [3]"
    switch ($choice) {
        "1" { $Variant = "portable" }
        "2" { $Variant = "installer" }
        default { $Variant = "all" }
    }
}

# ── 3. Choose version (optional) ────────────────────────────────────────────
if (-not $Version) {
    $defaultVersion = $null
    $versionFile = Join-Path $AppRoot "VERSION"
    if (Test-Path $versionFile) {
        $line = Select-String -Path $versionFile -Pattern "^version:" | Select-Object -First 1
        if ($line) {
            $defaultVersion = ($line.Line -split ":", 2)[1].Trim()
        }
    }

    if ($defaultVersion) {
        $input = Read-Host "Version [$defaultVersion]"
        if ($input) { $Version = $input } else { $Version = $defaultVersion }
    } else {
        $Version = Read-Host "Enter version (e.g. 7.1.0, or leave blank for VERSION file)"
    }
}

# ── 4. Warn when cross-compiling ────────────────────────────────────────────
if ($OS -ne $currentOS) {
    Write-Host ""
    Write-Host "WARNING: Nuitka does not cross-compile. Building for $OS must be done" -ForegroundColor Yellow
    Write-Host "on a $OS machine, and the packaging tools are platform-specific." -ForegroundColor Yellow
    Write-Host "This build will likely fail if run on $currentOS." -ForegroundColor Yellow
    Write-Host ""
    $continue = Read-Host "Continue anyway? [y/N]"
    if ($continue -ne "y") { exit 1 }
}

# ── 5. Check dependencies ───────────────────────────────────────────────────
Write-Host ""
Write-Host "Checking dependencies..."
$missing = @()

# Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    $missing += "python"
} elseif ($OS -ne "windows") {
    # Verify Python can compile (needed for Nuitka) — skip on Windows where Nuitka finds MSVC itself
    $canCompile = & python -c "import sysconfig; print(sysconfig.get_config_var('CC') or '')" 2>$null
    if (-not $canCompile) {
        $pyVer = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($IsLinux) {
            $missing += "gcc  (sudo dnf install gcc  /  sudo apt install build-essential)"
        } elseif ($IsMacOS) {
            $missing += "Xcode Command Line Tools  (xcode-select --install)"
        }
    }
}

if ($OS -eq "linux") {
    # patchelf (required for standalone/onefile)
    if (-not (Get-Command patchelf -ErrorAction SilentlyContinue)) {
        $missing += "patchelf  (sudo dnf install patchelf  /  sudo apt install patchelf)"
    }
    # Python development headers
    $pyVer = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    $headerPath = "/usr/include/python$pyVer/Python.h"
    if (-not (Test-Path $headerPath)) {
        $missing += "python3-devel / python3-dev  (sudo dnf install python${pyVer}-devel  /  sudo apt install python3-dev)"
    }
    # appimagetool (portable variant)
    if ($Variant -eq "portable" -or $Variant -eq "all") {
        if (-not (Get-Command appimagetool -ErrorAction SilentlyContinue)) {
            $missing += "appimagetool  (https://github.com/AppImage/AppImageKit/releases)"
        }
    }
    # fpm (installer variant — deb/rpm)
    if ($Variant -eq "installer" -or $Variant -eq "all") {
        if (-not (Get-Command fpm -ErrorAction SilentlyContinue)) {
            $missing += "fpm  (sudo dnf install rubygem-fpm  /  gem install fpm)"
        }
        if (-not (Get-Command rpmbuild -ErrorAction SilentlyContinue)) {
            $missing += "rpmbuild  (sudo dnf install rpm-build  /  sudo apt install rpm)"
        }
        if (-not (Get-Command dpkg-deb -ErrorAction SilentlyContinue)) {
            $missing += "dpkg-deb  (sudo apt install dpkg  — only needed for .deb targets)"
        }
    }
}

if ($OS -eq "macos") {
    # Xcode CLI tools (provides clang, pkgbuild, hdiutil)
    if (-not (Get-Command xcode-select -ErrorAction SilentlyContinue)) {
        $missing += "Xcode Command Line Tools  (xcode-select --install)"
    } elseif (-not (& xcode-select -p 2>$null)) {
        $missing += "Xcode Command Line Tools not installed  (xcode-select --install)"
    }
}

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "Missing dependencies:" -ForegroundColor Red
    foreach ($dep in $missing) {
        Write-Host "  - $dep" -ForegroundColor Red
    }
    Write-Host ""
    $continue = Read-Host "Continue anyway? [y/N]"
    if ($continue -ne "y") { exit 1 }
} else {
    Write-Host "All dependencies found." -ForegroundColor Green
}

# ── 6. Set up virtualenv + install dependencies ─────────────────────────────
$venv = Join-Path $AppRoot ".venv"
if ($IsWindows) {
    $py = Join-Path $venv "Scripts\python.exe"
} else {
    $py = Join-Path $venv "bin\python"
}

if (-not (Test-Path $py)) {
    Write-Host ""
    Write-Host "Creating virtualenv..."
    & python -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to create virtualenv." -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "Installing build dependencies..."
& $py -m pip install --upgrade pip *> $null
& $py -m pip install -r (Join-Path $AppRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to install dependencies." -ForegroundColor Red
    exit 1
}

# ── 7. Stop running app so the exe isn't locked (Windows only) ─────────────
if ($OS -eq "windows") {
    Write-Host ""
    Write-Host "Stopping any running Mocha Tools instances..."
    Stop-Process -Name "Mocha Tools" -Force -ErrorAction SilentlyContinue | Out-Null
}

# ── 7. Build ────────────────────────────────────────────────────────────────
Write-Host ""
$buildArgs = @(
    (Join-Path $AppRoot "build\build.py")
    "--platform", $OS
    "--variant", $Variant
)

if ($Version) {
    Write-Host "Building Mocha Tools $Version for $OS ($Variant)..."
    $buildArgs += "--version", $Version
} else {
    Write-Host "Building Mocha Tools for $OS ($Variant) using VERSION file..."
}

& $py @buildArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Done. Output in $(Join-Path $AppRoot "dist"):"
Get-ChildItem (Join-Path $AppRoot "dist") -Name
