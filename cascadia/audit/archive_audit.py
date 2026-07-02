"""
archive_audit.py — Monthly audit-record archiver for Cascadia OS.

NIST 800-171 AU-family: the live audit trail (data/audit.log) is a TIER-2
audit record — never rotated or truncated by newsyslog. This archiver is the
ONLY sanctioned path for managing its size:

  1. gzip-copy data/audit.log → data/audit-archive/audit-YYYY-MM.log.gz
  2. verify the archive is readable and its sha256 matches the source bytes
  3. append a MANIFEST.txt line (filename, sha256, byte count, date range)
  4. ONLY THEN reset the live log — preserving any events appended during
     archiving (tail-preserving, so no audit record is ever lost)

Retention: archives are NEVER deleted by default (AUDIT_RETENTION_DAYS unset
or 0 = keep forever). If AUDIT_RETENTION_DAYS is set >0, archives are pruned
only when older than max(365, AUDIT_RETENTION_DAYS) days — a hard 365-day
floor that a smaller configured value can never undercut.

Runs monthly via the ai.zyrcon.audit-archive LaunchAgent (day=1, hour=3).
Machine-local deployment — the plist is not committed.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# __file__-relative — never hardcode /Users/zyrcon. parents[2] = repo root
# (cascadia-os), matching the path the enterprise audit writer resolves to.
_ROOT = Path(__file__).resolve().parents[2]
AUDIT_LOG = _ROOT / "data" / "audit.log"
ARCHIVE_DIR = _ROOT / "data" / "audit-archive"
MANIFEST = ARCHIVE_DIR / "MANIFEST.txt"

RETENTION_FLOOR_DAYS = 365  # hard minimum — never retain audit archives for less


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _date_range(data: bytes) -> str:
    """First and last event timestamp in the archived JSONL, best-effort."""
    lines = [l for l in data.decode("utf-8", "replace").splitlines() if l.strip()]
    first = last = "unknown"
    for l in lines:
        try:
            first = json.loads(l).get("timestamp", "unknown")
            break
        except Exception:
            continue
    for l in reversed(lines):
        try:
            last = json.loads(l).get("timestamp", "unknown")
            break
        except Exception:
            continue
    return f"{first} .. {last}"


def _unique_archive_path(stamp: str) -> Path:
    """audit-YYYY-MM.log.gz, suffixed if that name already exists (never clobber)."""
    p = ARCHIVE_DIR / f"audit-{stamp}.log.gz"
    if not p.exists():
        return p
    return ARCHIVE_DIR / f"audit-{stamp}-{datetime.now().strftime('%d%H%M%S')}.log.gz"


def archive() -> dict:
    """Archive the current audit.log. Returns a summary dict."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    if not AUDIT_LOG.exists():
        return {"status": "skipped", "reason": "no audit.log"}

    # Snapshot the current content and its exact length. Anything appended
    # after this point is preserved on reset (tail-preserving).
    data = AUDIT_LOG.read_bytes()
    archived_len = len(data)
    if archived_len == 0:
        return {"status": "skipped", "reason": "audit.log empty"}

    src_sha = _sha256_bytes(data)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m")
    out = _unique_archive_path(stamp)

    # 1. gzip-write
    with gzip.open(out, "wb") as gz:
        gz.write(data)

    # 2. verify readable + sha256 matches the source bytes BEFORE any reset
    with gzip.open(out, "rb") as gz:
        roundtrip = gz.read()
    if _sha256_bytes(roundtrip) != src_sha:
        out.unlink(missing_ok=True)
        raise RuntimeError("archive verification failed — sha256 mismatch; live log NOT reset")

    # 3. manifest line
    line = (f"{out.name}\tsha256={src_sha}\tbytes={archived_len}"
            f"\trange={_date_range(data)}\tarchived_at={datetime.now(timezone.utc).isoformat()}\n")
    with open(MANIFEST, "a") as f:
        f.write(line)

    # 4. reset live log, PRESERVING anything appended during archiving
    current = AUDIT_LOG.read_bytes()
    tail = current[archived_len:] if len(current) >= archived_len else b""
    AUDIT_LOG.write_bytes(tail)

    pruned = _prune_old_archives()
    return {
        "status": "ok",
        "archive": out.name,
        "sha256": src_sha,
        "bytes": archived_len,
        "preserved_tail_bytes": len(tail),
        "pruned": pruned,
    }


def _prune_old_archives() -> list[str]:
    """Delete archives older than max(365, AUDIT_RETENTION_DAYS). Default: keep all."""
    configured = int(os.environ.get("AUDIT_RETENTION_DAYS", "0") or "0")
    if configured <= 0:
        return []  # keep forever
    effective = max(RETENTION_FLOOR_DAYS, configured)
    cutoff = time.time() - effective * 86400
    pruned = []
    for p in ARCHIVE_DIR.glob("audit-*.log.gz"):
        if p.stat().st_mtime < cutoff:
            p.unlink()
            pruned.append(p.name)
    return pruned


def main() -> None:
    result = archive()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
