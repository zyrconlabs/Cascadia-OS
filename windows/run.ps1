# Cascadia OS — Task Scheduler entrypoint
# This script is called by Windows Task Scheduler at every login.
#Requires -Version 5.1
$ErrorActionPreference = "SilentlyContinue"

$INSTALL_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $INSTALL_DIR

# Small delay so the desktop is fully loaded before starting services
Start-Sleep 5

& "$INSTALL_DIR\start.ps1"
