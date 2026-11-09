#!/usr/bin/env python3
"""Create an authenticated Fernet archive of a data directory. Key never printed."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_tar(src: Path, tar_path: Path, include_globs: list[str]) -> list[dict]:
    files: list[Path] = []
    for pattern in include_globs:
        # recursive for simple extensions; honor explicit relative globs
        if "/" not in pattern and pattern.startswith("*."):
            files.extend(sorted(src.rglob(pattern)))
        else:
            files.extend(sorted(src.glob(pattern)))
    # de-dupe, files only, under src
    seen = set()
    unique: list[Path] = []
    for p in files:
        if not p.is_file():
            continue
        rp = p.resolve()
        if src.resolve() not in rp.parents and rp != src.resolve():
            continue
        if rp in seen:
            continue
        seen.add(rp)
        unique.append(p)

    manifest = []
    with tarfile.open(tar_path, "w:gz") as tar:
        for p in unique:
            arcname = p.relative_to(src).as_posix()
            tar.add(p, arcname=arcname)
            st = p.stat()
            manifest.append(
                {
                    "path": arcname,
                    "size": st.st_size,
                    "sha256": _sha256(p),
                }
            )
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--key-file", required=True, type=Path)
    ap.add_argument(
        "--pattern",
        action="append",
        dest="patterns",
        default=None,
        help="glob relative to src; repeatable",
    )
    args = ap.parse_args()
    src = args.src.resolve()
    if not src.is_dir():
        raise SystemExit(f"src not a directory: {src}")

    patterns = args.patterns or [
        "*.json",
        "*.jsonl",
        "*.txt",
        "pending_*.json",
    ]

    key = Fernet.generate_key()
    args.key_file.parent.mkdir(parents=True, exist_ok=True)
    # write key with 0600
    fd = os.open(args.key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    os.chmod(args.key_file, 0o600)

    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / "data.tar.gz"
        manifest_files = build_tar(src, tar_path, patterns)
        plaintext = tar_path.read_bytes()
        token = Fernet(key).encrypt(plaintext)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(token)
        os.chmod(args.out, 0o644)

    manifest = {
        "format": "fernet-tar-gz-v1",
        "source_name": src.name,
        "archive": args.out.name,
        "file_count": len(manifest_files),
        "plaintext_tar_sha256": hashlib.sha256(plaintext).hexdigest(),
        "ciphertext_bytes": args.out.stat().st_size,
        "files": manifest_files,
    }
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    # summary only — no paths that look like secrets, no message bodies
    print(
        f"encrypted ok files={manifest['file_count']} "
        f"cipher_bytes={manifest['ciphertext_bytes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
