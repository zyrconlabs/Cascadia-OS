# Cascadia OS — Windows Uninstaller
#Requires -Version 5.1
$ErrorActionPreference = "SilentlyContinue"

$INSTALL_DIR = "$env:USERPROFILE\cascadia-os"

Write-Host ""
Write-Host "  Cascadia OS — Uninstaller" -ForegroundColor Yellow
Write-Host ""
Write-Host "  This will:"
Write-Host "  * Stop all running Cascadia services"
Write-Host "  * Remove the Task Scheduler task"
Write-Host "  * Delete $INSTALL_DIR"
Write-Host ""
$confirm = Read-Host "  Continue? [y/N]"
if ($confirm -notmatch "^[Yy]$") { Write-Host "  Cancelled."; exit 0 }

# Stop services
if (Test-Path "$INSTALL_DIR\stop.ps1") {
    Write-Host "Stopping services..."
    & "$INSTALL_DIR\stop.ps1"
}

# Remove Task Scheduler task
schtasks /Delete /TN "CascadiaOS" /F 2>$null
if ($LASTEXITCODE -eq 0) { Write-Host "  [ok] Task Scheduler task removed." -ForegroundColor Green }

# Remove install directory
if (Test-Path $INSTALL_DIR) {
    Remove-Item $INSTALL_DIR -Recurse -Force
    Write-Host "  [ok] Removed $INSTALL_DIR" -ForegroundColor Green
}

Write-Host ""
Write-Host "  Cascadia OS has been uninstalled." -ForegroundColor Green
Write-Host "  You can delete this installer folder manually."
Write-Host ""
