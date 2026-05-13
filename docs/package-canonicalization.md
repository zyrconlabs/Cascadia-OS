# Package Canonicalization Rules

**Status:** RFC — awaiting Andy review before Sprint 2B implementation
**Version:** 1.0-draft
**Date:** 2026-05-12
**Author:** Zyrcon Labs

---

## Purpose

Without canonicalization, the same logical package can produce different
byte sequences depending on operating system, compression tool, YAML/JSON
serializer version, or file system. Different byte sequences produce
different digests, which breaks signature verification.

These rules define the exact canonical form used when computing:

- Per-file SHA-256 hashes (stored in `files[]`)
- The package digest (`package_digest`)
- The signed manifest bytes (input to Ed25519 signing)

Both the signer (Zyrcon Labs tooling) and the verifier (CREW at install
time) must apply these rules identically.

---

## Rule 1 — File Order

When iterating package files to compute the package digest, files are
processed in **lexicographic order by POSIX-normalized path**.

Lexicographic comparison is:
- Case-sensitive (uppercase before lowercase in ASCII order)
- Byte-by-byte comparison of UTF-8 encoded path strings
- Directory separators (`/`) sort before any other character (ASCII 47,
  before all alphanumeric characters)

This means `a/b.json` sorts before `ab.json`.

**Python reference implementation:**

```python
sorted_paths = sorted(all_paths, key=lambda p: p.encode('utf-8'))
```

---

## Rule 2 — Path Normalization

All file paths within a package must be POSIX-normalized before use in
any digest computation or manifest field.

### Rules

1. Use `/` as the separator. Replace `\` with `/`.
2. No leading slash. `mission.json` is valid; `/mission.json` is invalid.
3. No `..` path segments. Any package containing `..` is rejected before
   canonicalization begins (security rule, not canonicalization).
4. No `.` path segments (current directory). `./workflows/main.json` is
   normalized to `workflows/main.json`.
5. Collapse multiple consecutive separators: `workflows//main.json` →
   `workflows/main.json`.
6. Strip trailing separator: `workflows/` → `workflows` (but packages
   should not list directories in `files[]`).
7. Maximum depth: 8 levels (8 `/` separators maximum).
8. Maximum filename length: 255 bytes (UTF-8 encoded component, not the
   full path).
9. Paths are case-sensitive. `Templates/Quote.md` and `templates/quote.md`
   are different files.

**Python reference implementation:**

```python
from pathlib import PurePosixPath

def normalize_path(path: str) -> str:
    p = PurePosixPath(path.replace('\\', '/'))
    parts = [part for part in p.parts if part not in ('', '.', '..')]
    if len(parts) > 8:
        raise ValueError(f"path exceeds max depth 8: {path!r}")
    normalized = '/'.join(parts)
    if not normalized:
        raise ValueError(f"empty path after normalization: {path!r}")
    return normalized
```

---

## Rule 3 — Line Endings

Line endings affect SHA-256 hashes. To make hashes platform-independent,
text files are normalized to LF (`\n`, 0x0A) before hashing. Binary files
are hashed byte-for-byte.

### Text vs Binary Classification

**Always text (LF normalize before hashing):**

| Extension | Notes |
|-----------|-------|
| `.json` | Mission manifests, workflow files |
| `.yaml`, `.yml` | If YAML format is adopted (see spec J1) |
| `.md` | Documentation, templates |
| `.txt` | Plain text |
| `.csv` | Data files |
| `.html`, `.htm` | HTML templates |
| `.css` | Stylesheets |
| `.js`, `.mjs` | JavaScript |
| `.py` | Python source |
| `.sh`, `.bash` | Shell scripts |
| `.toml`, `.ini`, `.cfg` | Config formats |
| `.sql` | SQL scripts |
| `.xml` | XML data |

**Always binary (hash byte-for-byte, no modification):**

| Extension | Notes |
|-----------|-------|
| `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg` | Images (SVG is XML but treat as binary) |
| `.pdf` | Documents |
| `.zip`, `.tar`, `.gz`, `.bz2`, `.xz` | Archives |
| `.whl`, `.egg` | Python packages |
| `.db`, `.sqlite` | Databases |
| `.bin`, `.dat` | Binary data |
| `.wasm` | WebAssembly |
| `.dylib`, `.so`, `.dll` | Native libraries |
| `.ttf`, `.otf`, `.woff`, `.woff2` | Fonts |
| `.ico` | Icons |

**Ambiguous or unlisted extensions:** Treat as binary. When in doubt,
hash byte-for-byte. The signer's tooling must use the same classification
as CREW's verifier; the classification tables above are the canonical
reference.

### LF Normalization Algorithm

For text files only:

```python
def normalize_line_endings(content: bytes) -> bytes:
    """Replace CRLF and bare CR with LF."""
    content = content.replace(b'\r\n', b'\n')
    content = content.replace(b'\r', b'\n')
    return content
```

Apply this transformation before computing SHA-256. The transformed bytes
are never written to disk — only the hash is stored.

---

## Rule 4 — Compression Behavior

The package digest is computed over **uncompressed file contents**, not
over the zip file bytes.

The zip archive is a transport wrapper only. Compression level, compression
method (Deflate, BZIP2, LZMA, Store), and zip metadata do not affect any
digest or signature.

This means:
- A package re-zipped with different compression produces the same digests.
- Signers and verifiers both decompress before hashing.
- The zip file itself is never signed or hashed.

---

## Rule 5 — Excluded Metadata

The following are excluded from all digest computations and must not
appear in `files[]`:

### Zip-Internal Metadata

- File timestamps (`mtime`, `ctime`, `atime`) — not part of canonical content
- Unix mode bits / file permissions — not part of canonical content
- Zip comment field — ignored
- Extra fields in local/central directory headers — ignored

### macOS Artifacts

- `.DS_Store` files (any directory)
- `__MACOSX/` directory and all contents
- `._*` AppleDouble resource fork files

### Python Artifacts

- `*.pyc` files
- `__pycache__/` directories and contents

### Editor Backups

- `*~` (Emacs/vi backup)
- `*.swp`, `*.swo` (vim swap files)
- `*.orig` (merge conflict originals)
- `.#*` (Emacs lock files)

### VCS Metadata

- `.git/` and all contents
- `.svn/` and all contents
- `.hg/` and all contents

**If a submitted zip contains any excluded paths**, the installer strips
them before canonicalization. They do not cause install rejection (they
are silently ignored). The `files[]` manifest must not list them.

---

## Rule 6 — Manifest Canonicalization

The signature covers the canonical manifest — the `mission.json` content
serialized as a deterministic byte sequence with the `signature` field
removed.

### Steps

1. **Parse** `mission.json` as UTF-8 JSON into a Python dict.

2. **Remove the `signature` field.** If the field is absent (computing
   the signature for the first time), skip this step.

3. **Sort keys recursively.** At every nesting level, sort dict keys
   alphabetically. Lists are NOT sorted — list order is preserved because
   it is semantically significant (step order in workflows, etc.).

4. **Serialize to JSON** using the following exact settings:
   - `separators=(',', ':')` — no spaces after separators
   - `sort_keys=True` — belt-and-suspenders (step 3 already sorted)
   - `ensure_ascii=False` — preserve non-ASCII characters as UTF-8
   - No trailing newline

5. **Encode to bytes** using UTF-8. No BOM.

The resulting byte sequence is the input to Ed25519 signing and verification.

**Python reference implementation:**

```python
import json

def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Produce canonical bytes for signing. manifest must already be parsed."""
    m = {k: v for k, v in manifest.items() if k != 'signature'}
    return json.dumps(m, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')
```

### What Is NOT Stripped

Only `signature` is stripped. All other fields — including `signed_by`,
`signature_algorithm`, and `key_id` — remain in the signed content. This
means a verifier can trust that `key_id` was declared by the signer and
has not been swapped.

---

## Rule 7 — Package Digest Computation

The package digest is a SHA-256 over all canonical file contents,
concatenated in lexicographic path order.

### Algorithm

```
package_digest = SHA-256(concat(
    for each path in sorted(all_paths):
        encode_utf8(path) + b'\x00'    # path, null-terminated
      + len(canonical_bytes).to_bytes(8, 'big')  # 8-byte big-endian length
      + canonical_bytes                # file content after line ending norm
))
```

Where:
- `all_paths` is the set of paths in `files[]` (not including `mission.json`)
- `canonical_bytes` is the file content after line ending normalization
  (text files) or raw bytes (binary files)
- The length prefix prevents length-extension ambiguities when file
  content is concatenated
- The null terminator on the path prevents path/content boundary ambiguity

**Python reference implementation:**

```python
import hashlib

def compute_package_digest(file_map: dict[str, bytes]) -> str:
    """
    file_map: {normalized_path: canonical_bytes}
    Returns 'sha256:<hex>'
    """
    h = hashlib.sha256()
    for path in sorted(file_map.keys(), key=lambda p: p.encode('utf-8')):
        content = file_map[path]
        h.update(path.encode('utf-8') + b'\x00')
        h.update(len(content).to_bytes(8, 'big'))
        h.update(content)
    return 'sha256:' + h.hexdigest()
```

---

## Rule 8 — Per-File SHA-256

Each file's `sha256` entry in `files[]` is computed independently:

```
file_sha256 = SHA-256(canonical_bytes)
```

Where `canonical_bytes` is the file content after line ending normalization
(for text files) or raw bytes (for binary files).

The stored value is the lowercase hex digest (64 hex characters).

**Python reference implementation:**

```python
import hashlib

def file_sha256(content: bytes, is_text: bool) -> str:
    if is_text:
        content = normalize_line_endings(content)
    return hashlib.sha256(content).hexdigest()
```

---

## Algorithm References

| Operation | Algorithm | Specification |
|-----------|-----------|---------------|
| File hash | SHA-256 | NIST FIPS 180-4 |
| Package digest | SHA-256 | NIST FIPS 180-4 |
| Package signing | Ed25519 | RFC 8032 |
| JSON serialization | RFC 8259 | Python `json` module, `separators=(',',':')` |
| String encoding | UTF-8 | RFC 3629 |

**Library references (Python):**

| Purpose | Module | Notes |
|---------|--------|-------|
| SHA-256 | `hashlib` (stdlib) | `hashlib.sha256()` |
| Ed25519 signing | `cryptography.hazmat.primitives.asymmetric.ed25519` | Already a project dependency |
| Ed25519 verification | Same | `Ed25519PublicKey.verify()` raises `InvalidSignature` on failure |
| JSON | `json` (stdlib) | `json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=False)` |

---

## Test Vectors

### Vector 1 — Single text file

**Input file** `templates/quote.md` (text, LF normalize):

```
Hello, {name}!\r\n
```

(16 bytes: 14 text chars + \r + \n)

**Canonical bytes** (after CRLF → LF):

```
Hello, {name}!\n
```

(15 bytes: 14 text chars + \n)

**SHA-256 of canonical bytes:**

```
1e86d5b60d7dc623fef1b8cf2b847c6e4c9b47fbcd00a3b0eadcce51c54df492
```

### Vector 2 — Package digest with two files

**Files (after canonicalization):**

| Path | Canonical bytes | Length | SHA-256 |
|------|----------------|--------|---------|
| `templates/quote.md` | `Hello, {name}!\n` | 15 bytes | `1e86d5b60d7dc623fef1b8cf2b847c6e4c9b47fbcd00a3b0eadcce51c54df492` |
| `workflows/main.json` | `{"steps":[]}\n` | 13 bytes | `4dfdf4c2e8f3f3c86a3ca3c75648d3ce52c2f43b9cda2a72ff2fc563c3825886` |

**Sorted paths** (lexicographic): `templates/quote.md`, `workflows/main.json`

**Package digest computation:**

```
h = SHA-256()
# First file: templates/quote.md
h.update(b'templates/quote.md\x00')       # path + null
h.update((15).to_bytes(8, 'big'))         # 8-byte length (15 bytes)
h.update(b'Hello, {name}!\n')             # canonical content

# Second file: workflows/main.json
h.update(b'workflows/main.json\x00')      # path + null
h.update((13).to_bytes(8, 'big'))         # 8-byte length (13 bytes)
h.update(b'{"steps":[]}\n')              # canonical content

package_digest = 'sha256:' + h.hexdigest()
# Result: sha256:7084c017325aee7e0ef448a47413a9f2066795ef6955fab1c97f4c6b21aa6924
```

### Vector 3 — Canonical manifest bytes

**Input manifest (before canonicalization):**

```json
{
  "version": "1.0.0",
  "id": "example_mission",
  "type": "mission",
  "signature": "AAAA...",
  "name": "Example"
}
```

**After removing `signature` and sorting keys:**

```json
{"id":"example_mission","name":"Example","type":"mission","version":"1.0.0"}
```

**UTF-8 bytes** (no trailing newline):

```
7b226964223a226578616d706c655f6d697373696f6e222c226e616d65223a224578616d706c65222c2274797065223a226d697373696f6e222c2276657273696f6e223a22312e302e30227d
```

*(Hex representation of the UTF-8 string above.)*

---

## Worked Example — Complete Package

### Package Contents

A minimal mission package with 3 files:

```
lead_qualification_1.0.0.zip
├── mission.json          (manifest)
├── workflows/
│   └── main.json         (workflow definition)
└── templates/
    └── quote.md          (email template)
```

### File: `workflows/main.json`

```json
{
  "steps": [
    {"id": "step_scout", "type": "operator", "operator": "scout"}
  ]
}
```

**Is text:** Yes (`.json`)
**Raw bytes:** `{\n  "steps": [\n    {"id": "step_scout", "type": "operator", "operator": "scout"}\n  ]\n}\n`
**After LF normalization:** same (already LF)
**Byte count:** 87
**SHA-256:** `a0dbc5f4c21d1c208946c7dca485de5cff8ec1bcc8e2ce377b92e5f4816d7908`

### File: `templates/quote.md`

```
Dear {{contact_name}},\r\n
\r\n
Please find the proposal attached.\r\n
```

**Is text:** Yes (`.md`)
**After CRLF → LF:**

```
Dear {{contact_name}},\n
\n
Please find the proposal attached.\n
```

**Byte count:** 59
**SHA-256:** `2e7bd21b199f1c1c852eb1b041a2b7cd4f22bfa702810c6a7cbfc3537fb30872`

### `files[]` Block

```json
{
  "files": [
    {
      "path": "templates/quote.md",
      "sha256": "2e7bd21b199f1c1c852eb1b041a2b7cd4f22bfa702810c6a7cbfc3537fb30872",
      "size_bytes": 59
    },
    {
      "path": "workflows/main.json",
      "sha256": "a0dbc5f4c21d1c208946c7dca485de5cff8ec1bcc8e2ce377b92e5f4816d7908",
      "size_bytes": 87
    }
  ]
}
```

Note: `mission.json` itself is NOT listed in `files[]`.

### Package Digest

Sorted paths: `templates/quote.md`, `workflows/main.json`

```python
package_digest = compute_package_digest({
    "templates/quote.md": b'Dear {{contact_name}},\n\nPlease find the proposal attached.\n',
    "workflows/main.json": b'{\n  "steps": [\n    {"id": "step_scout", "type": "operator", "operator": "scout"}\n  ]\n}\n',
})
# Result: "sha256:48144dde1a45c9bd553bdb855185608c263918f314913f0b070583ef7f7f3305"
```

### Canonical Manifest for Signing

`mission.json` (before signature is added):

```json
{
  "type": "mission",
  "id": "lead_qualification",
  "version": "1.0.0",
  "name": "Lead Qualification Pipeline",
  "description": "Qualifies inbound leads.",
  "tier_required": "pro",
  "runtime": "server",
  "author": "zyrcon-labs",
  "signed_by": "zyrcon-labs",
  "signature_algorithm": "Ed25519",
  "key_id": "zyrcon-2026-q2",
  "package_digest": "sha256:48144dde1a45c9bd553bdb855185608c263918f314913f0b070583ef7f7f3305",
  "files": [
    {"path": "templates/quote.md", "sha256": "2e7bd21b199f1c1c852eb1b041a2b7cd4f22bfa702810c6a7cbfc3537fb30872", "size_bytes": 59},
    {"path": "workflows/main.json", "sha256": "a0dbc5f4c21d1c208946c7dca485de5cff8ec1bcc8e2ce377b92e5f4816d7908", "size_bytes": 87}
  ],
  "capabilities": ["crm.read", "email.send"],
  "requires_approval": ["email.send"],
  "risk_level": "medium",
  "operators": {"required": ["scout"], "optional": []},
  "connectors": {"required": [], "optional": []},
  "workflows": {"main": "workflows/main.json"}
}
```

**Canonical bytes for signing** (sorted keys, no spaces, UTF-8, no `signature` field):

```
{"author":"zyrcon-labs","capabilities":["crm.read","email.send"],"connectors":{"optional":[],"required":[]},"description":"Qualifies inbound leads.","files":[{"path":"templates/quote.md","sha256":"2e7bd21b199f1c1c852eb1b041a2b7cd4f22bfa702810c6a7cbfc3537fb30872","size_bytes":59},{"path":"workflows/main.json","sha256":"a0dbc5f4c21d1c208946c7dca485de5cff8ec1bcc8e2ce377b92e5f4816d7908","size_bytes":87}],"id":"lead_qualification","key_id":"zyrcon-2026-q2","name":"Lead Qualification Pipeline","operators":{"optional":[],"required":["scout"]},"package_digest":"sha256:48144dde1a45c9bd553bdb855185608c263918f314913f0b070583ef7f7f3305","requires_approval":["email.send"],"risk_level":"medium","runtime":"server","signature_algorithm":"Ed25519","signed_by":"zyrcon-labs","tier_required":"pro","type":"mission","version":"1.0.0","workflows":{"main":"workflows/main.json"}}
```

**SHA-256 of canonical manifest bytes:** `f754c2961ecff1388143bbbad941bdf398ea7985d7dae92d003b6d7ebaef3039`

**Note:** The `files[]` list preserves its original order (not sorted) because
list order is semantically preserved. Only dict keys are sorted.

### Signing

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import base64, json

private_key = Ed25519PrivateKey.generate()  # or load from file
canonical = canonical_manifest_bytes(manifest_without_signature)
signature_bytes = private_key.sign(canonical)
signature_b64 = base64.urlsafe_b64encode(signature_bytes).rstrip(b'=').decode()
```

### Verification

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
import base64

def verify(manifest: dict, public_key: Ed25519PublicKey) -> bool:
    sig_b64 = manifest.get('signature', '')
    # Add padding back for standard base64url decode
    padding = 4 - len(sig_b64) % 4
    sig_bytes = base64.urlsafe_b64decode(sig_b64 + '=' * (padding % 4))
    canonical = canonical_manifest_bytes(manifest)
    try:
        public_key.verify(sig_bytes, canonical)
        return True
    except InvalidSignature:
        return False
```

### End-to-End Verification Snippet

The following script reproduces every value in this worked example from
first principles and asserts correctness. Run it with `python3` to confirm
your canonicalization implementation matches the spec.

```python
import hashlib, json

def normalize_line_endings(content: bytes) -> bytes:
    content = content.replace(b'\r\n', b'\n')
    content = content.replace(b'\r', b'\n')
    return content

def compute_package_digest(file_map: dict) -> str:
    h = hashlib.sha256()
    for path in sorted(file_map.keys(), key=lambda p: p.encode('utf-8')):
        content = file_map[path]
        h.update(path.encode('utf-8') + b'\x00')
        h.update(len(content).to_bytes(8, 'big'))
        h.update(content)
    return 'sha256:' + h.hexdigest()

def canonical_manifest_bytes(manifest: dict) -> bytes:
    m = {k: v for k, v in manifest.items() if k != 'signature'}
    return json.dumps(m, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')

# File contents
quote_raw = b'Dear {{contact_name}},\r\n\r\nPlease find the proposal attached.\r\n'
quote_canonical = normalize_line_endings(quote_raw)
main_canonical = b'{\n  "steps": [\n    {"id": "step_scout", "type": "operator", "operator": "scout"}\n  ]\n}\n'

assert len(quote_canonical) == 59
assert len(main_canonical) == 87
assert hashlib.sha256(quote_canonical).hexdigest() == \
    '2e7bd21b199f1c1c852eb1b041a2b7cd4f22bfa702810c6a7cbfc3537fb30872'
assert hashlib.sha256(main_canonical).hexdigest() == \
    'a0dbc5f4c21d1c208946c7dca485de5cff8ec1bcc8e2ce377b92e5f4816d7908'

file_map = {"templates/quote.md": quote_canonical, "workflows/main.json": main_canonical}
pkg_digest = compute_package_digest(file_map)
assert pkg_digest == \
    'sha256:48144dde1a45c9bd553bdb855185608c263918f314913f0b070583ef7f7f3305'

manifest = {
    "type": "mission", "id": "lead_qualification", "version": "1.0.0",
    "name": "Lead Qualification Pipeline", "description": "Qualifies inbound leads.",
    "tier_required": "pro", "runtime": "server", "author": "zyrcon-labs",
    "signed_by": "zyrcon-labs", "signature_algorithm": "Ed25519",
    "key_id": "zyrcon-2026-q2", "package_digest": pkg_digest,
    "files": [
        {"path": "templates/quote.md", "sha256": hashlib.sha256(quote_canonical).hexdigest(), "size_bytes": 59},
        {"path": "workflows/main.json", "sha256": hashlib.sha256(main_canonical).hexdigest(), "size_bytes": 87},
    ],
    "capabilities": ["crm.read", "email.send"], "requires_approval": ["email.send"],
    "risk_level": "medium", "operators": {"required": ["scout"], "optional": []},
    "connectors": {"required": [], "optional": []},
    "workflows": {"main": "workflows/main.json"},
}
canon = canonical_manifest_bytes(manifest)
assert hashlib.sha256(canon).hexdigest() == \
    'f754c2961ecff1388143bbbad941bdf398ea7985d7dae92d003b6d7ebaef3039'

print("All assertions passed.")
```

---

## Implementation Notes for Sprint 2B

1. **Module location:** `cascadia/depot/canonicalization.py`
2. **Public functions to expose:**
   - `normalize_path(path: str) -> str`
   - `is_text_file(path: str) -> bool`
   - `normalize_line_endings(content: bytes) -> bytes`
   - `canonical_file_bytes(path: str, content: bytes) -> bytes`
   - `file_sha256(path: str, content: bytes) -> str`
   - `compute_package_digest(file_map: dict[str, bytes]) -> str`
   - `canonical_manifest_bytes(manifest: dict) -> bytes`
3. **Test vectors:** Every function in this module must have a test that
   reproduces the worked example above exactly. Tests live in
   `cascadia/tests/test_canonicalization.py`.
4. **No I/O in this module.** `canonicalization.py` is pure computation
   over bytes. Callers handle reading files from disk or zip archives.
