#!/usr/bin/env python3
"""
download_model.py — background HuggingFace model downloader.

Spawned by POST /api/prism/setup/download-model.
Writes progress to data/runtime/model_download_progress.json.
Completion is signalled by the model file existing in models/.
"""
import sys
import json
import time
import urllib.request
from pathlib import Path

MODEL_CATALOG = {
    '3b': {
        'name': 'Qwen 2.5 3B',
        'file': 'qwen2.5-3b-instruct-q4_k_m.gguf',
        'huggingface_repo': 'Qwen/Qwen2.5-3B-Instruct-GGUF',
    },
    '7b': {
        'name': 'Qwen 2.5 7B',
        'file': 'qwen2.5-7b-instruct-q4_k_m.gguf',
        'huggingface_repo': 'Qwen/Qwen2.5-7B-Instruct-GGUF',
    },
    '14b': {
        'name': 'Qwen 2.5 14B',
        'file': 'qwen2.5-14b-instruct-q4_k_m.gguf',
        'huggingface_repo': 'Qwen/Qwen2.5-14B-Instruct-GGUF',
    },
}

if '--tier' not in sys.argv:
    print('Usage: download_model.py --tier <3b|7b|14b>', file=sys.stderr)
    sys.exit(1)

tier  = sys.argv[sys.argv.index('--tier') + 1]
model = MODEL_CATALOG[tier]

BASE_DIR      = Path(__file__).parent.parent
PROGRESS_FILE = BASE_DIR / 'data' / 'runtime' / 'model_download_progress.json'
MODELS_DIR    = BASE_DIR / 'models'
MODELS_DIR.mkdir(exist_ok=True)
PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)

dest = MODELS_DIR / model['file']
url  = (
    f"https://huggingface.co/{model['huggingface_repo']}"
    f"/resolve/main/{model['file']}"
)


def write_progress(status: str, percent: int = 0, speed: float = 0.0, eta: float = 0.0) -> None:
    PROGRESS_FILE.write_text(json.dumps({
        'status':     status,
        'percent':    percent,
        'speed_mbps': round(speed, 1),
        'eta_seconds': int(eta),
    }))


write_progress('downloading', 0)

downloaded = 0
start_time = time.time()


def reporthook(block_num: int, block_size: int, total_size: int) -> None:
    global downloaded
    downloaded = block_num * block_size
    if total_size > 0:
        percent   = min(int(downloaded / total_size * 100), 99)
        elapsed   = time.time() - start_time
        speed     = (downloaded / 1_048_576 / elapsed) if elapsed > 0 else 0.0
        remaining = max(total_size - downloaded, 0)
        eta       = (remaining / 1_048_576 / speed) if speed > 0 else 0.0
        write_progress('downloading', percent, speed, eta)


try:
    urllib.request.urlretrieve(url, dest, reporthook)
    write_progress('complete', 100)
except Exception:
    write_progress('error', 0)
finally:
    # Remove progress file only on success — model file existence signals completion.
    # On error the progress file is kept so the client can read the error status.
    if dest.exists():
        try:
            PROGRESS_FILE.unlink(missing_ok=True)
        except Exception:
            pass
