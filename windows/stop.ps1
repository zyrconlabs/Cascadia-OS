# Cascadia OS — Windows shutdown script
#Requires -Version 5.1
$ErrorActionPreference = "SilentlyContinue"

Write-Host "Stopping Cascadia OS stack..." -ForegroundColor Cyan

function Stop-ByCommandLine($pattern) {
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$pattern*" }
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    return ($procs | Measure-Object).Count
}

# Stop watchdog (FLINT runs as a thread inside it, not a separate process)
$n = Stop-ByCommandLine "cascadia.kernel.watchdog"
if ($n -gt 0) { Write-Host "  [ok] Watchdog stopped" -ForegroundColor Green }

# Stop any orphaned component processes (crew, vault, sentinel, etc.)
# These are normally stopped by FLINT's graceful shutdown, but if FLINT
# was force-killed, they may still be running.
$componentPatterns = @(
    "cascadia.registry.crew",
    "cascadia.memory.vault",
    "cascadia.security.sentinel",
    "cascadia.encryption.curtain",
    "cascadia.orchestrator.beacon",
    "cascadia.automation.stitch",
    "cascadia.gateway.vanguard",
    "cascadia.bridge.handshake",
    "cascadia.chat.bell",
    "cascadia.guide.almanac",
    "cascadia.dashboard.prism"
)
$orphans = 0
foreach ($pat in $componentPatterns) {
    $orphans += Stop-ByCommandLine $pat
}
if ($orphans -gt 0) { Write-Host "  [ok] $orphans component process(es) stopped" -ForegroundColor Green }

# Stop tray icon
$n = Stop-ByCommandLine "cascadia.flint.tray"
if ($n -gt 0) { Write-Host "  [ok] Tray icon stopped" -ForegroundColor Green }

# Stop llama.cpp (anything on port 8080 or named llama-server)
$conn = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "  [ok] llama.cpp stopped" -ForegroundColor Green
}
Stop-ByCommandLine "llama-server" | Out-Null

Write-Host "Done." -ForegroundColor Green
