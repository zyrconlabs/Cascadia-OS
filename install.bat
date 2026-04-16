@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM Cascadia OS — One-Click Installer  (Windows)
REM Usage: Download and double-click, or run in PowerShell:
REM   irm https://raw.githubusercontent.com/YOUR_USERNAME/cascadia-os/main/install.bat | iex
REM ─────────────────────────────────────────────────────────────────────────────
setlocal EnableDelayedExpansion

set REPO=YOUR_USERNAME/cascadia-os
set BRANCH=main
set INSTALL_DIR=%USERPROFILE%\cascadia-os
set VENV_DIR=%INSTALL_DIR%\.venv

echo.
echo   ╔══════════════════════════════════════╗
echo   ║       Cascadia OS v0.2 Installer     ║
echo   ╚══════════════════════════════════════╝
echo.

REM ── 1. Check Python ──────────────────────────────────────────────────────────
echo [cascadia] Checking Python version...
python --version >nul 2>&1
if errorlevel 1 (
    echo [cascadia] ERROR: Python 3.11+ is required.
    echo            Download from https://python.org and re-run.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [cascadia] Found Python %PY_VER%

REM ── 2. Check Git ─────────────────────────────────────────────────────────────
echo [cascadia] Checking git...
git --version >nul 2>&1
if errorlevel 1 (
    echo [cascadia] ERROR: git is required. Install from https://git-scm.com
    pause
    exit /b 1
)
echo [cascadia] git found.

REM ── 3. Clone or update ───────────────────────────────────────────────────────
if exist "%INSTALL_DIR%\.git" (
    echo [cascadia] Existing installation found — pulling latest...
    git -C "%INSTALL_DIR%" pull --ff-only origin %BRANCH%
) else (
    echo [cascadia] Cloning Cascadia OS into %INSTALL_DIR%...
    git clone --branch %BRANCH% --depth 1 https://github.com/%REPO%.git "%INSTALL_DIR%"
)

cd /d "%INSTALL_DIR%"

REM ── 4. Virtual environment ───────────────────────────────────────────────────
if not exist "%VENV_DIR%" (
    echo [cascadia] Creating virtual environment...
    python -m venv "%VENV_DIR%"
)
call "%VENV_DIR%\Scripts\activate.bat"
echo [cascadia] Virtual environment ready.

REM ── 5. Install package ───────────────────────────────────────────────────────
echo [cascadia] Installing Cascadia OS...
pip install --quiet --upgrade pip
pip install --quiet -e .
echo [cascadia] Package installed.

REM ── 6. Config ────────────────────────────────────────────────────────────────
if not exist "%INSTALL_DIR%\config.json" (
    copy "%INSTALL_DIR%\config.example.json" "%INSTALL_DIR%\config.json" >nul
    echo [cascadia] config.json created from example. Edit it before starting.
) else (
    echo [cascadia] config.json already exists — skipping.
)

REM ── 7. First-time setup ──────────────────────────────────────────────────────
echo [cascadia] Running first-time setup...
python -m cascadia.installer.once
echo [cascadia] Setup complete.

REM ── 8. Launcher batch file ───────────────────────────────────────────────────
set LAUNCHER=%USERPROFILE%\AppData\Local\Microsoft\WindowsApps\cascadia.bat
echo @echo off > "%LAUNCHER%"
echo call "%VENV_DIR%\Scripts\activate.bat" >> "%LAUNCHER%"
echo python -m cascadia.kernel.watchdog --config "%INSTALL_DIR%\config.json" %%* >> "%LAUNCHER%"

REM ── 9. Done ──────────────────────────────────────────────────────────────────
echo.
echo [cascadia] ════════════════════════════════════════
echo [cascadia]  Cascadia OS v0.2 installed successfully
echo [cascadia] ════════════════════════════════════════
echo.
echo   Start:   cascadia
echo   Config:  %INSTALL_DIR%\config.json
echo.
pause
