# Cascadia OS — Windows Installation

## Requirements
- Windows 10 or 11
- PowerShell 5.1+ (built into Windows)
- Python 3.11+ https://python.org — tick Add to PATH during install
- Git for Windows https://git-scm.com

## One-Click Install

Open PowerShell and run:

    git clone https://github.com/zyrconlabs/cascadia-os.git
    powershell -ExecutionPolicy Bypass -File cascadia-os\windows\install.ps1

The installer sets up everything and opens PRISM at http://localhost:6300

## Manual Controls

    .\start.ps1       # Start the full stack
    .\stop.ps1        # Stop everything
    .\setup-llm.ps1   # Switch AI model
    .\uninstall.ps1   # Remove Cascadia OS

## AI Modes

| Mode | Best for |
|------|----------|
| Local llama.cpp | Privacy, no API costs, needs 8GB+ RAM |
| Cloud API | Any hardware, OpenAI Anthropic or Groq |
| Ollama | Already running Ollama locally |

## Logs

    %USERPROFILE%\cascadia-os\data\logs\

## Troubleshooting
- Python not found: reinstall Python tick Add to PATH restart PowerShell
- ExecutionPolicy error: run Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
- Port in use: run .\stop.ps1 then .\start.ps1
