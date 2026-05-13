"""Package canonicalization for mission package signing and verification.

Pure computation over bytes — no I/O. Callers handle reading files from
disk or zip archives. Both the signer (Zyrcon Labs tooling) and the
verifier (CREW at install time) must apply these rules identically.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath

_TEXT_EXTENSIONS = frozenset({
    '.json', '.yaml', '.yml', '.md', '.txt', '.csv',
    '.html', '.htm', '.css', '.js', '.mjs', '.py',
    '.sh', '.bash', '.toml', '.ini', '.cfg', '.sql', '.xml',
})


def normalize_path(path: str) -> str:
    """Return POSIX-normalized path.

    Raises ValueError if the path exceeds 8 directory levels (8 separators).
    Leading slashes and '.' / '..' segments are stripped (callers must reject
    packages containing '..' before calling this per the spec security rule).
    """
    p = PurePosixPath(path.replace('\\', '/'))
    # '/' is the root sentinel in PurePosixPath.parts — filter it alongside
    # empty strings, current-dir '.', and parent-dir '..' markers.
    parts = [part for part in p.parts if part not in ('', '.', '..', '/')]
    # Spec: "Maximum depth: 8 levels (8 `/` separators maximum)"
    # 8 separators = 9 parts total — 10+ parts exceeds the limit.
    if len(parts) > 9:
        raise ValueError(f"path exceeds max depth 8: {path!r}")
    if not parts:
        raise ValueError(f"empty path after normalization: {path!r}")
    return '/'.join(parts)


def is_text_file(path: str) -> bool:
    """Return True if path has a text extension (LF-normalize before hashing)."""
    suffix = PurePosixPath(path).suffix.lower()
    return suffix in _TEXT_EXTENSIONS


def normalize_line_endings(content: bytes) -> bytes:
    """Replace CRLF and bare CR with LF."""
    content = content.replace(b'\r\n', b'\n')
    content = content.replace(b'\r', b'\n')
    return content


def canonical_file_bytes(path: str, content: bytes) -> bytes:
    """Return canonical bytes for a file: LF-normalized for text, raw for binary."""
    if is_text_file(path):
        return normalize_line_endings(content)
    return content


def file_sha256(path: str, content: bytes) -> str:
    """Return lowercase hex SHA-256 of canonical bytes for a file."""
    return hashlib.sha256(canonical_file_bytes(path, content)).hexdigest()


def compute_package_digest(file_map: dict[str, bytes]) -> str:
    """Compute package digest over all files in lexicographic path order.

    file_map: {normalized_path: canonical_bytes}
    Returns 'sha256:<hex>'.

    mission.json is NOT included — it is protected separately by the
    Ed25519 signature. Including it would create a circular signing problem.
    """
    h = hashlib.sha256()
    for path in sorted(file_map.keys(), key=lambda p: p.encode('utf-8')):
        content = file_map[path]
        h.update(path.encode('utf-8') + b'\x00')
        h.update(len(content).to_bytes(8, 'big'))
        h.update(content)
    return 'sha256:' + h.hexdigest()


def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Produce canonical bytes for signing. Excludes the 'signature' field.

    All other fields (signed_by, signature_algorithm, key_id) remain in the
    signed content so a verifier can trust they were declared by the signer.
    """
    m = {k: v for k, v in manifest.items() if k != 'signature'}
    return json.dumps(m, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')
