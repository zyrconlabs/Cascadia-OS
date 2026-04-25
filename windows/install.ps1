# ─────────────────────────────────────────────────────────────────────────────
# Cascadia OS — Windows Installer
# Usage:
#   powershell -ExecutionPolicy Bypass -File install.ps1
# ─────────────────────────────────────────────────────────────────────────────
#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$REPO       = "zyrconlabs/cascadia-os"
$BRANCH     = "main"
$INSTALL_DIR = "$env:USERPROFILE\cascadia-os"
$VENV_DIR   = "$INSTALL_DIR\.venv"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Info    { param($m) Write-Host "[cascadia] $m" -ForegroundColor Cyan    }
function Write-Success { param($m) Write-Host "[cascadia] $m" -ForegroundColor Green   }
function Write-Warn    { param($m) Write-Host "[cascadia] $m" -ForegroundColor Yellow  }
function Write-Err     { param($m) Write-Host "[cascadia] ERROR: $m" -ForegroundColor Red; exit 1 }

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  +-----------------------------------------+" -ForegroundColor Magenta
$_ver = try { (Select-String -Path "$INSTALL_DIR\pyproject.toml" -Pattern 'version = "([^"]+)"').Matches.Groups[1].Value } catch { "0.44.0" }
Write-Host "  |   Cascadia OS v$_ver  Windows Installer     |" -ForegroundColor Magenta
Write-Host "  |          by Zyrcon Labs                  |" -ForegroundColor Magenta
Write-Host "  +-----------------------------------------+" -ForegroundColor Magenta
Write-Host ""

# ── Pre-install disclosure ────────────────────────────────────────────────────
Write-Host "  +------------------------------------------------------------+"
Write-Host "  |                  BEFORE YOU CONTINUE                      |"
Write-Host "  +------------------------------------------------------------+"
Write-Host "  |                                                            |"
Write-Host "  |  This installer will automatically:                       |"
Write-Host "  |                                                            |"
Write-Host "  |  * Install Python packages (flask, cryptography, pystray) |"
Write-Host "  |  * Create a Python virtual environment                    |"
Write-Host "  |  * Register a Task Scheduler task  (auto-start at login)  |"
Write-Host "  |  * Start a system tray icon (pystray)                     |"
Write-Host "  |                                                            |"
Write-Host "  |  Files are written to:                                    |"
Write-Host "  |  * %USERPROFILE%\cascadia-os\   (application)             |"
Write-Host "  |                                                            |"
Write-Host "  |  Nothing else on your system is modified.                 |"
Write-Host "  |                                                            |"
Write-Host "  |  By continuing you agree to the terms in LICENSE.         |"
Write-Host "  +------------------------------------------------------------+"
Write-Host ""
$confirm = Read-Host "  Continue with installation? [y/N]"
if ($confirm -notmatch "^[Yy]$") { Write-Host "  Installation cancelled."; exit 0 }
Write-Host "  Starting installation..."
Write-Host ""

# ── 1. Check Python 3.11+ ─────────────────────────────────────────────────────
Write-Info "Checking Python..."
$PYTHON = $null
foreach ($cmd in @("python", "python3", "py")) {
    # "py" is the Windows Python Launcher; try with -3 flag to force Python 3
    $cmdArgs = $(if ($cmd -eq "py") { @("-3") } else { @() })
    try {
        $ver = & $cmd @cmdArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver -match "^(\d+)\.(\d+)$") {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -ge 3 -and $min -ge 11) {
                # Resolve to the actual executable so venv creation uses the right binary
                $PYTHON = (& $cmd @cmdArgs -c "import sys; print(sys.executable)" 2>$null).Trim()
                break
            }
        }
    } catch {}
}
if (-not $PYTHON) {
    Write-Err "Python 3.11+ is required. Download from https://python.org (tick 'Add to PATH')"
}
$pyVer = & $PYTHON --version
Write-Success "Found $pyVer"

# ── 2. Check Git ──────────────────────────────────────────────────────────────
Write-Info "Checking git..."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Err "git is required. Install from https://git-scm.com"
}
Write-Success "git found."

# ── 3. Clone or update ────────────────────────────────────────────────────────
if (Test-Path "$INSTALL_DIR\.git") {
    Write-Info "Existing installation found — pulling latest..."
    git -C $INSTALL_DIR pull --ff-only origin $BRANCH
} else {
    Write-Info "Cloning Cascadia OS into $INSTALL_DIR ..."
    git clone --branch $BRANCH --depth 1 "https://github.com/$REPO.git" $INSTALL_DIR
}
Set-Location $INSTALL_DIR

# ── 4. Apply Windows patches ──────────────────────────────────────────────────
$patchTray = "$SCRIPT_DIR\patches\tray.py"
if (Test-Path $patchTray) {
    Write-Info "Applying Windows tray patch..."
    Copy-Item $patchTray "$INSTALL_DIR\cascadia\flint\tray.py" -Force
    Write-Success "tray.py patched for Windows."
}

# ── 5. Virtual environment ────────────────────────────────────────────────────
if (-not (Test-Path $VENV_DIR)) {
    Write-Info "Creating virtual environment..."
    & $PYTHON -m venv $VENV_DIR
}
$PYTHON_VENV = "$VENV_DIR\Scripts\python.exe"
$PIP_VENV    = "$VENV_DIR\Scripts\pip.exe"
Write-Success "Virtual environment ready."

# ── 6. Install package ────────────────────────────────────────────────────────
Write-Info "Installing Cascadia OS and dependencies..."
& $PIP_VENV install --quiet --upgrade pip
& $PIP_VENV install --quiet -e ".[operators,tray]"
Write-Success "Package installed."

# ── 7. Config ─────────────────────────────────────────────────────────────────
if (-not (Test-Path "$INSTALL_DIR\config.json")) {
    Copy-Item "$INSTALL_DIR\config.example.json" "$INSTALL_DIR\config.json"
    Write-Success "config.json created."
} else {
    Write-Info "config.json already exists — skipping."
}

# Auto-generate signing secret if still set to the placeholder value
$cfgRaw = Get-Content "$INSTALL_DIR\config.json" -Raw
if ($cfgRaw -match "replace-with-output") {
    Write-Info "Generating encryption signing secret..."
    $secret = (& $PYTHON_VENV -c "import secrets; print(secrets.token_hex(32))").Trim()
    $cfg = $cfgRaw | ConvertFrom-Json
    $cfg.curtain.signing_secret = $secret
    $cfg | ConvertTo-Json -Depth 10 | Set-Content "$INSTALL_DIR\config.json" -Encoding UTF8
    Write-Success "Signing secret auto-generated."
}

# ── 8. Silent first-time setup ────────────────────────────────────────────────
Write-Info "Running silent setup (directories, database, defaults)..."
& $PYTHON_VENV -m cascadia.installer.once --dir $INSTALL_DIR --config config.json --no-browser
Write-Success "Setup complete."

# ── 9. Copy Windows scripts into install dir ──────────────────────────────────
foreach ($f in @("start.ps1","stop.ps1","run.ps1","setup-llm.ps1","uninstall.ps1","cascadia-task.xml")) {
    $src = "$SCRIPT_DIR\$f"
    if (Test-Path $src) { Copy-Item $src "$INSTALL_DIR\$f" -Force }
}

# ── 9b. AI model setup ───────────────────────────────────────────────────────
Write-Host ""
Write-Info "Setting up AI model (you can skip and configure later in PRISM)..."
& "$INSTALL_DIR\setup-llm.ps1"

# ── 10. Register Task Scheduler task (auto-start at logon) ───────────────────
Write-Info "Registering Task Scheduler task..."
$xmlTemplate = Get-Content "$SCRIPT_DIR\cascadia-task.xml" -Raw -Encoding UTF8
$xmlFilled   = $xmlTemplate -replace "__INSTALL_DIR__", $INSTALL_DIR
$xmlTmp      = "$env:TEMP\cascadia-task.xml"
[System.IO.File]::WriteAllText($xmlTmp, $xmlFilled, [System.Text.Encoding]::Unicode)
$rc = (Start-Process schtasks -ArgumentList "/Create /TN `"CascadiaOS`" /XML `"$xmlTmp`" /F" -Wait -PassThru -WindowStyle Hidden).ExitCode
Remove-Item $xmlTmp -ErrorAction SilentlyContinue
if ($rc -eq 0) {
    Write-Success "Task Scheduler task registered — starts automatically at every login."
} else {
    Write-Warn "Could not register Task Scheduler task. Start manually: .\start.ps1"
}

# ── 11. Start Cascadia OS ─────────────────────────────────────────────────────
Write-Host ""
Write-Info "Starting Cascadia OS..."
& "$INSTALL_DIR\start.ps1"

# ── 12. Open dashboard ────────────────────────────────────────────────────────
if (-not (Test-Path "$INSTALL_DIR\.setup_complete")) {
    Write-Info "Opening PRISM dashboard — choose your AI mode in Settings..."
    Start-Process "http://localhost:6300/#settings"
    New-Item "$INSTALL_DIR\.setup_complete" -ItemType File -Force | Out-Null
} else {
    Write-Info "Opening PRISM dashboard..."
    Start-Process "http://localhost:6300"
}

Write-Host ""
Write-Host "  +----------------------------------------------+" -ForegroundColor Magenta
Write-Host "  |  Cascadia OS is running.                     |" -ForegroundColor Magenta
Write-Host "  |                                              |" -ForegroundColor Magenta
Write-Host "  |  -> PRISM dashboard opened in your browser   |" -ForegroundColor Magenta
Write-Host "  |  -> Tray icon shows live system status       |" -ForegroundColor Magenta
Write-Host "  |  -> Starts automatically at every login      |" -ForegroundColor Magenta
Write-Host "  +----------------------------------------------+" -ForegroundColor Magenta
Write-Host ""
