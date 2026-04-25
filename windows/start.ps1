# ─────────────────────────────────────────────────────────────────────────────
# Cascadia OS — Windows startup script
# Starts: llama.cpp (optional) + Cascadia OS (11 components) + tray icon
# ─────────────────────────────────────────────────────────────────────────────
#Requires -Version 5.1
$ErrorActionPreference = "SilentlyContinue"

$INSTALL_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $INSTALL_DIR

# PS 5.1 compat: inline if-expression requires $() wrapper
$PYTHON = $(if (Test-Path "$INSTALL_DIR\.venv\Scripts\python.exe") { "$INSTALL_DIR\.venv\Scripts\python.exe" } else { "python" })
$LOG_DIR     = "$INSTALL_DIR\data\logs"
$CONFIG      = "$INSTALL_DIR\config.json"
New-Item $LOG_DIR -ItemType Directory -Force | Out-Null

function Test-Service($port) {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$port/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        return $r.StatusCode -lt 400
    } catch { return $false }
}

function Get-OwningPid($port) {
    (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
}

# Helper: find processes by command line pattern (works on PS 5.1 and PS 7+)
function Get-ProcessByCommandLine($pattern) {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$pattern*" }
}

Write-Host "Starting Cascadia OS full stack..." -ForegroundColor Cyan
Write-Host ""

# ── 1. llama.cpp ──────────────────────────────────────────────────────────────
try {
    $cfg         = Get-Content $CONFIG -Raw | ConvertFrom-Json
    $llmProvider = $cfg.llm.provider
    $modelsDir   = $cfg.llm.models_dir
    $modelFile   = $cfg.llm.model
    $llamaBin    = $cfg.llm.llama_bin
    $nGpuLayers  = $(if ($cfg.llm.n_gpu_layers) { $cfg.llm.n_gpu_layers } else { 99 })

    if (-not [System.IO.Path]::IsPathRooted($modelsDir)) {
        $modelsDir = $modelsDir -replace '^\.[\\/]', ''
        $modelsDir = Join-Path $INSTALL_DIR $modelsDir
    }
    $modelPath = Join-Path $modelsDir $modelFile
} catch { $llmProvider = "" }

if ($llmProvider -eq "llamacpp") {
    if (Test-Service 8080) {
        Write-Host "  [ok] llama.cpp already running" -ForegroundColor Green
    } elseif (-not $llamaBin -or -not (Test-Path $llamaBin)) {
        Write-Host "  [--] llama.cpp not installed — skipping  (run: .\setup-llm.ps1)" -ForegroundColor Yellow
    } elseif (-not (Test-Path $modelPath)) {
        Write-Host "  [--] AI model not found — open PRISM -> Settings or run: .\setup-llm.ps1" -ForegroundColor Yellow
    } else {
        Write-Host "  [>>] Starting llama.cpp..." -ForegroundColor Cyan
        $pid8080 = Get-OwningPid 8080
        if ($pid8080) { Stop-Process -Id $pid8080 -Force -ErrorAction SilentlyContinue; Start-Sleep 1 }

        $llamaArgs = "--model `"$modelPath`" --host 127.0.0.1 --port 8080 --ctx-size 4096 --n-gpu-layers $nGpuLayers"
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName  = $llamaBin
        $psi.Arguments = $llamaArgs
        $psi.WorkingDirectory     = $INSTALL_DIR
        $psi.CreateNoWindow       = $true
        $psi.UseShellExecute      = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError  = $true
        $p = [System.Diagnostics.Process]::Start($psi)

        # Pipe stdout+stderr to log file — hold task references to prevent GC
        $llamaLogPath = "$LOG_DIR\llamacpp.log"
        $script:llamaOutTask = $p.StandardOutput.BaseStream.CopyToAsync(
            (New-Object System.IO.FileStream($llamaLogPath, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite))
        )
        $script:llamaErrTask = $p.StandardError.BaseStream.CopyToAsync(
            (New-Object System.IO.FileStream($llamaLogPath, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite))
        )

        Start-Sleep 6
        if (Test-Service 8080) {
            Write-Host "  [ok] llama.cpp ready" -ForegroundColor Green
        } else {
            Write-Host "  [!!] llama.cpp failed — check $LOG_DIR\llamacpp.log" -ForegroundColor Red
        }
    }
}

# ── 2. Cascadia OS ────────────────────────────────────────────────────────────
$cascadiaRunning = $false
if (Test-Service 4011) {
    # Check if this is a stale instance from a different install path
    $runningProc = Get-ProcessByCommandLine "cascadia.kernel.watchdog" | Select-Object -First 1
    $runningFromHere = $runningProc -and ($runningProc.CommandLine -like "*$INSTALL_DIR*")
    if ($runningFromHere -or -not $runningProc) {
        Write-Host "  [ok] Cascadia OS already running" -ForegroundColor Green
        $cascadiaRunning = $true
    } else {
        Write-Host "  [>>] Restarting Cascadia OS (stale instance from different path)..." -ForegroundColor Cyan
        Get-ProcessByCommandLine "cascadia.kernel" |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep 2
    }
}

if (-not $cascadiaRunning) {
    Write-Host "  [>>] Starting Cascadia OS..." -ForegroundColor Cyan
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName               = $PYTHON
    $startInfo.Arguments              = "-m cascadia.kernel.watchdog --config `"$CONFIG`""
    $startInfo.WorkingDirectory       = $INSTALL_DIR
    $startInfo.CreateNoWindow         = $true
    $startInfo.UseShellExecute        = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError  = $true
    $proc = [System.Diagnostics.Process]::Start($startInfo)

    # Pipe output to log — hold task references to prevent GC
    $flintLogPath = "$LOG_DIR\flint.log"
    $script:flintOutTask = $proc.StandardOutput.BaseStream.CopyToAsync(
        (New-Object System.IO.FileStream($flintLogPath, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite))
    )
    $script:flintErrTask = $proc.StandardError.BaseStream.CopyToAsync(
        (New-Object System.IO.FileStream($flintLogPath, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite))
    )

    Write-Host "  [..] Waiting for services (up to 30s)..." -ForegroundColor Cyan
    $waited = 0
    while (-not (Test-Service 4011) -and $waited -lt 30) { Start-Sleep 2; $waited += 2 }
    if (Test-Service 4011) {
        Write-Host "  [ok] Cascadia OS ready" -ForegroundColor Green
    } else {
        Write-Host "  [!!] Cascadia OS failed to start — check $LOG_DIR\flint.log" -ForegroundColor Red
    }
}

# ── 3. System tray icon ───────────────────────────────────────────────────────
$trayRunning = Get-ProcessByCommandLine "cascadia.flint.tray"
if (-not $trayRunning) {
    Write-Host "  [>>] Starting tray icon..." -ForegroundColor Cyan
    $trayInfo = New-Object System.Diagnostics.ProcessStartInfo
    $trayInfo.FileName         = $PYTHON
    $trayInfo.Arguments        = "-m cascadia.flint.tray"
    $trayInfo.WorkingDirectory = $INSTALL_DIR
    $trayInfo.CreateNoWindow   = $true
    $trayInfo.UseShellExecute  = $false
    [System.Diagnostics.Process]::Start($trayInfo) | Out-Null
    Write-Host "  [ok] Tray icon started" -ForegroundColor Green
}

Write-Host ""
Write-Host "  ========================================================" -ForegroundColor Cyan
Write-Host "   Cascadia OS stack is up." -ForegroundColor Cyan
Write-Host "  ========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  PRISM dashboard  ->  http://localhost:6300/" -ForegroundColor White
Write-Host ""
