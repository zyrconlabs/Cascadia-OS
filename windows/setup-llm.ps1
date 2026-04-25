# ─────────────────────────────────────────────────────────────────────────────
# Cascadia OS — Windows AI Model Setup
# Detects hardware, downloads llama.cpp + model, updates config.json
# Usage:  .\setup-llm.ps1 [3b|7b|14b]   (default: auto-recommend)
# ─────────────────────────────────────────────────────────────────────────────
#Requires -Version 5.1
param([string]$ModelArg = "")

$ErrorActionPreference = "Stop"

$INSTALL_DIR  = Split-Path -Parent $MyInvocation.MyCommand.Path
$MODELS_DIR   = "$INSTALL_DIR\models"
$CONFIG_PATH  = "$INSTALL_DIR\config.json"
$LLAMA_DIR    = "$INSTALL_DIR\llama.cpp"
$LLAMA_BIN    = "$LLAMA_DIR\llama-server.exe"
$NON_INTERACTIVE = ($ModelArg -ne "")

function Write-Info    { param($m) Write-Host "[cascadia] $m" -ForegroundColor Cyan   }
function Write-Success { param($m) Write-Host "[cascadia] $m" -ForegroundColor Green  }
function Write-Warn    { param($m) Write-Host "[cascadia] $m" -ForegroundColor Yellow }
function Write-Err     { param($m) Write-Host "[cascadia] ERROR: $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  +--------------------------------------------+"
Write-Host "  |     Cascadia OS — AI Setup (Windows)       |"
Write-Host "  +--------------------------------------------+"

# ── Step 1: Hardware detection ────────────────────────────────────────────────
Write-Host ""
Write-Info "Detecting your hardware..."

$ramBytes  = (Get-CimInstance Win32_PhysicalMemory -ErrorAction SilentlyContinue |
              Measure-Object Capacity -Sum).Sum
$RAM_GB    = $(if ($ramBytes) { [int]($ramBytes / 1GB) } else { 0 })

$GPU_TYPE  = "cpu_only"
$GPU_NAME  = "No GPU"
$VRAM_GB   = 0
$GPU_CAPABLE = $false

# NVIDIA check
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    try {
        $smiOut = (nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null) | Select-Object -First 1
        if ($smiOut) {
            $parts   = $smiOut.Split(",")
            $GPU_NAME = $parts[0].Trim()
            $VRAM_MB  = [int]($parts[1].Trim() -replace " MiB","")
            $VRAM_GB  = [int]($VRAM_MB / 1024)
            $GPU_TYPE    = "nvidia"
            $GPU_CAPABLE = $true
        }
    } catch {}
}

# AMD check (ROCm)
if ($GPU_TYPE -eq "cpu_only") {
    $rocm = Get-Command rocminfo -ErrorAction SilentlyContinue
    if ($rocm) { $GPU_TYPE = "amd"; $GPU_NAME = "AMD GPU (ROCm)"; $GPU_CAPABLE = $true }
}

# Fallback: any DirectX GPU
if ($GPU_TYPE -eq "cpu_only") {
    $dxGpu = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($dxGpu -and $dxGpu.Name -notmatch "Microsoft Basic") {
        $GPU_NAME = $dxGpu.Name
    }
}

# ── Recommendation ────────────────────────────────────────────────────────────
$RECOMMENDED_MODE  = "api"
$RECOMMENDED_MODEL = "3b"
if ($GPU_CAPABLE) {
    if ($RAM_GB -ge 8)  { $RECOMMENDED_MODEL = "7b";  $RECOMMENDED_MODE = "local" }
    elseif ($RAM_GB -ge 4) { $RECOMMENDED_MODEL = "3b"; $RECOMMENDED_MODE = "local" }
}
if ($RAM_GB -lt 4 -and $GPU_CAPABLE) { $RECOMMENDED_MODE = "api" }

# Hardware report
Write-Host ""
Write-Host "  +-----------------------------------------+"
Write-Host "  |  Hardware Report                        |"
Write-Host "  +-----------------------------------------+"
Write-Host ("  |  RAM:  {0,-33}|" -f "$RAM_GB GB")
if ($GPU_TYPE -eq "nvidia") {
    Write-Host ("  |  GPU:  {0,-33}|" -f "[ok] $GPU_NAME ($VRAM_GB GB VRAM)")
} elseif ($GPU_TYPE -eq "amd") {
    Write-Host ("  |  GPU:  {0,-33}|" -f "[ok] $GPU_NAME")
} else {
    Write-Host ("  |  GPU:  {0,-33}|" -f "$GPU_NAME")
}
Write-Host "  +-----------------------------------------+"
Write-Host ""

if ($GPU_TYPE -eq "nvidia")   { Write-Success "NVIDIA GPU detected — llama.cpp will use CUDA acceleration" }
elseif ($GPU_TYPE -eq "amd")  { Write-Success "AMD GPU detected — llama.cpp can use ROCm acceleration" }
else {
    Write-Warn "No GPU acceleration detected — llama.cpp will run CPU-only"
    Write-Host "  * 3B model: ~5-15 tokens/sec    * 7B+: very slow, not recommended"
    Write-Host "  Consider: use a Cloud API for better performance."
    Write-Host ""
}

# ── Step 2: Choose mode ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "  How should Cascadia run AI?"
Write-Host ""
if ($RECOMMENDED_MODE -eq "local") {
    Write-Host "  [1] Local  <- RECOMMENDED — private, free, fast on your hardware"
    Write-Host "  [2] API    — OpenAI / Anthropic / Groq (requires API key)"
    Write-Host "  [3] Ollama — use a model already running in Ollama"
    Write-Host "  [4] Skip   — configure later in PRISM Settings"
} else {
    Write-Host "  [1] Local  — runs on your hardware (may be slow without GPU)"
    Write-Host "  [2] API    <- RECOMMENDED — fast, works on any hardware"
    Write-Host "  [3] Ollama — use a model already running in Ollama"
    Write-Host "  [4] Skip   — configure later in PRISM Settings"
}
Write-Host ""

$defaultMode = $(if ($RECOMMENDED_MODE -eq "local") { "1" } else { "2" })
if ($NON_INTERACTIVE) { $modeChoice = "1" }
else { $modeChoice = Read-Host "  Choice [1-4, default: $defaultMode]" }
if ([string]::IsNullOrWhiteSpace($modeChoice)) { $modeChoice = $defaultMode }

# ── Step 3: Route to chosen path ──────────────────────────────────────────────
switch ($modeChoice) {

    "1" {
        # ── LOCAL MODE ────────────────────────────────────────────────────────
        Write-Host ""
        Write-Host "  Choose model size:"
        Write-Host ""
        $defSize = $(if ($RAM_GB -ge 8) { "2" } else { "1" })
        Write-Host "  [1] 3B  — 2.0 GB  Fast          (your ${RAM_GB}GB RAM: $(if($RAM_GB -ge 4){'ok'}else{'tight'}))"
        Write-Host "  [2] 7B  — 4.7 GB  Balanced      (your ${RAM_GB}GB RAM: $(if($RAM_GB -ge 8){'ok'}elseif($RAM_GB -ge 6){'tight'}else{'not recommended'}))"
        Write-Host "  [3] 14B — 8.9 GB  Best quality  (your ${RAM_GB}GB RAM: $(if($RAM_GB -ge 16){'ok'}elseif($RAM_GB -ge 12){'tight'}else{'not recommended'}))"
        Write-Host ""

        if ($ModelArg) { $sizeChoice = $ModelArg }
        else { $sizeChoice = Read-Host "  Size [1-3, default $defSize]" }
        if ([string]::IsNullOrWhiteSpace($sizeChoice)) { $sizeChoice = $defSize }

        switch ($sizeChoice) {
            { $_ -eq "2" -or $_ -eq "7b" } {
                $MODEL_SIZE = "7b"
                $MODEL_FILE = "qwen2.5-7b-instruct-q4_k_m.gguf"
                $MODEL_URL  = "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m.gguf"
            }
            { $_ -eq "3" -or $_ -eq "14b" } {
                $MODEL_SIZE = "14b"
                $MODEL_FILE = "Qwen2.5-14B-Instruct-Q4_K_M.gguf"
                $MODEL_URL  = "https://huggingface.co/Qwen/Qwen2.5-14B-Instruct-GGUF/resolve/main/Qwen2.5-14B-Instruct-Q4_K_M.gguf"
            }
            default {
                $MODEL_SIZE = "3b"
                $MODEL_FILE = "qwen2.5-3b-instruct-q4_k_m.gguf"
                $MODEL_URL  = "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
            }
        }

        # RAM safety check
        if ($MODEL_SIZE -eq "7b" -and $RAM_GB -lt 6) {
            Write-Warn "7B needs 8GB RAM — you have ${RAM_GB}GB. Switching to 3B."
            $MODEL_SIZE = "3b"; $MODEL_FILE = "qwen2.5-3b-instruct-q4_k_m.gguf"
            $MODEL_URL  = "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
        } elseif ($MODEL_SIZE -eq "14b" -and $RAM_GB -lt 12) {
            Write-Warn "14B needs 16GB RAM — you have ${RAM_GB}GB. Switching to 7B."
            $MODEL_SIZE = "7b"; $MODEL_FILE = "qwen2.5-7b-instruct-q4_k_m.gguf"
            $MODEL_URL  = "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m.gguf"
        }

        $MODEL_PATH = "$MODELS_DIR\$MODEL_FILE"

        # ── Install llama.cpp for Windows ─────────────────────────────────────
        Write-Host ""
        if (Test-Path $LLAMA_BIN) {
            Write-Success "llama.cpp found: $LLAMA_BIN"
        } else {
            Write-Info "Downloading llama.cpp for Windows..."
            New-Item $LLAMA_DIR -ItemType Directory -Force | Out-Null

            # Get latest release tag from GitHub API
            $releaseApi = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
            $release    = Invoke-RestMethod $releaseApi -Headers @{ "User-Agent" = "cascadia-installer" }
            $tag        = $release.tag_name   # e.g. b5123

            # Pick the right build variant — broader patterns to handle version suffixes
            if ($GPU_TYPE -eq "nvidia") {
                $asset = $release.assets | Where-Object { $_.name -match "bin-win.*cuda.*x64\.zip$" } | Select-Object -First 1
                $variantLabel = "CUDA"
            } else {
                $asset = $release.assets | Where-Object { $_.name -match "bin-win.*avx2.*x64\.zip$" } | Select-Object -First 1
                $variantLabel = "CPU/AVX2"
            }

            # Fallback to generic CPU build if variant not found
            if (-not $asset) {
                $asset = $release.assets | Where-Object { $_.name -match "bin-win.*x64\.zip$" -and $_.name -notmatch "cuda|vulkan" } | Select-Object -First 1
                $variantLabel = "CPU"
            }
            if (-not $asset) { Write-Err "Could not find a Windows llama.cpp build in the latest release ($tag). Download manually from https://github.com/ggml-org/llama.cpp/releases" }

            Write-Info "Downloading $($asset.name) ($variantLabel build, $('{0:N0}' -f ($asset.size/1MB)) MB)..."
            $zipPath = "$env:TEMP\llama-windows.zip"
            Invoke-WebRequest $asset.browser_download_url -OutFile $zipPath -UseBasicParsing

            Write-Info "Extracting..."
            Expand-Archive $zipPath -DestinationPath $LLAMA_DIR -Force
            Remove-Item $zipPath

            # llama-server.exe may be in a sub-folder — find it
            $exePath = Get-ChildItem $LLAMA_DIR -Recurse -Filter "llama-server.exe" | Select-Object -First 1
            if ($exePath -and $exePath.FullName -ne $LLAMA_BIN) {
                Copy-Item $exePath.FullName $LLAMA_BIN -Force
            }

            if (Test-Path $LLAMA_BIN) { Write-Success "llama.cpp installed: $LLAMA_BIN" }
            else { Write-Err "llama-server.exe not found after extraction. Check $LLAMA_DIR" }
        }

        # ── Download model ────────────────────────────────────────────────────
        New-Item $MODELS_DIR -ItemType Directory -Force | Out-Null
        if (Test-Path $MODEL_PATH) {
            Write-Success "Model already present: $MODEL_PATH"
        } else {
            Write-Info "Downloading $MODEL_FILE (~$(if($MODEL_SIZE -eq '3b'){'2'}elseif($MODEL_SIZE -eq '7b'){'4.7'}else{'8.9'}) GB) ..."
            Write-Warn "This may take a while. Do not close this window."
            $ProgressPreference = "SilentlyContinue"  # speed up Invoke-WebRequest
            Invoke-WebRequest $MODEL_URL -OutFile $MODEL_PATH -UseBasicParsing
            $ProgressPreference = "Continue"
            if (Test-Path $MODEL_PATH) { Write-Success "Model downloaded: $MODEL_PATH" }
            else { Write-Err "Model download failed." }
        }

        # ── Update config.json ────────────────────────────────────────────────
        $cfg = Get-Content $CONFIG_PATH -Raw | ConvertFrom-Json
        if (-not $cfg.llm) { $cfg | Add-Member -NotePropertyName llm -NotePropertyValue ([PSCustomObject]@{}) }
        $cfg.llm | Add-Member -NotePropertyName provider    -NotePropertyValue "llamacpp"     -Force
        $cfg.llm | Add-Member -NotePropertyName base_url    -NotePropertyValue "http://127.0.0.1:8080" -Force
        $cfg.llm | Add-Member -NotePropertyName models_dir  -NotePropertyValue $MODELS_DIR    -Force
        $cfg.llm | Add-Member -NotePropertyName model       -NotePropertyValue $MODEL_FILE    -Force
        $cfg.llm | Add-Member -NotePropertyName llama_bin   -NotePropertyValue $LLAMA_BIN     -Force
        $nGpuLayersVal = $(if ($GPU_CAPABLE) { 99 } else { 0 })
        $cfg.llm | Add-Member -NotePropertyName n_gpu_layers -NotePropertyValue $nGpuLayersVal -Force
        $cfg | ConvertTo-Json -Depth 10 | Set-Content $CONFIG_PATH -Encoding UTF8
        Write-Success "config.json updated for local llama.cpp mode."

        Write-Host ""
        Write-Host "  Local AI setup complete." -ForegroundColor Green
        Write-Host "  Run  .\start.ps1  to start the full stack."
    }

    "2" {
        # ── API MODE ──────────────────────────────────────────────────────────
        Write-Host ""
        Write-Host "  Choose your API provider:"
        Write-Host "  [1] OpenAI (GPT-4o, etc.)"
        Write-Host "  [2] Anthropic (Claude)"
        Write-Host "  [3] Groq"
        Write-Host "  [4] Other (custom base_url)"
        Write-Host ""
        $provChoice = Read-Host "  Provider [1-4]"
        switch ($provChoice) {
            "1" { $provider = "openai";    $baseUrl = "https://api.openai.com/v1";       $model = "gpt-4o" }
            "2" { $provider = "anthropic"; $baseUrl = "https://api.anthropic.com/v1";    $model = "claude-sonnet-4-6" }
            "3" { $provider = "groq";      $baseUrl = "https://api.groq.com/openai/v1";  $model = "llama-3.3-70b-versatile" }
            default { $provider = "openai"; $baseUrl = Read-Host "  Base URL"; $model = Read-Host "  Model name" }
        }
        $apiKey = Read-Host "  API key (will be stored in config.json)"

        $cfg = Get-Content $CONFIG_PATH -Raw | ConvertFrom-Json
        if (-not $cfg.llm) { $cfg | Add-Member -NotePropertyName llm -NotePropertyValue ([PSCustomObject]@{}) }
        $cfg.llm | Add-Member -NotePropertyName provider -NotePropertyValue $provider -Force
        $cfg.llm | Add-Member -NotePropertyName base_url -NotePropertyValue $baseUrl  -Force
        $cfg.llm | Add-Member -NotePropertyName model    -NotePropertyValue $model    -Force
        $cfg.llm | Add-Member -NotePropertyName api_key  -NotePropertyValue $apiKey   -Force
        $cfg | ConvertTo-Json -Depth 10 | Set-Content $CONFIG_PATH -Encoding UTF8
        Write-Success "config.json updated for $provider API mode."
    }

    "3" {
        # ── OLLAMA MODE ───────────────────────────────────────────────────────
        $ollamaUrl = "http://127.0.0.1:11434/v1"
        $ollamaModel = Read-Host "  Ollama model name (e.g. llama3.2, qwen2.5:3b)"
        $cfg = Get-Content $CONFIG_PATH -Raw | ConvertFrom-Json
        if (-not $cfg.llm) { $cfg | Add-Member -NotePropertyName llm -NotePropertyValue ([PSCustomObject]@{}) }
        $cfg.llm | Add-Member -NotePropertyName provider -NotePropertyValue "ollama"   -Force
        $cfg.llm | Add-Member -NotePropertyName base_url -NotePropertyValue $ollamaUrl -Force
        $cfg.llm | Add-Member -NotePropertyName model    -NotePropertyValue $ollamaModel -Force
        $cfg | ConvertTo-Json -Depth 10 | Set-Content $CONFIG_PATH -Encoding UTF8
        Write-Success "config.json updated for Ollama mode."
    }

    default { Write-Warn "Skipped. Configure AI mode later in PRISM -> Settings." }
}

Write-Host ""
