#!/usr/bin/env python3
"""Restore Fernet chat/data archive. Key from env CHAT_BACKUP_KEY or --key-file. Never prints key/token."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tarfile
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def _load_key(key_file: Path | None) -> bytes:
    env = os.environ.get("CHAT_BACKUP_KEY", "").strip()
    if env:
        return env.encode() if not env.startswith("gAAAA") else env.encode()
    # Fernet keys are urlsafe base64; accept raw file bytes
    if key_file is None:
        raise SystemExit("set CHAT_BACKUP_KEY or pass --key-file")
    raw = key_file.read_bytes().strip()
    return raw


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> list[str]:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for m in tar.getmembers():
        name = m.name
        if name.startswith("/") or ".." in Path(name).parts:
            raise SystemExit(f"unsafe path in archive: {name!r}")
        target = (dest / name).resolve()
        if dest not in target.parents and target != dest:
            raise SystemExit(f"path escapes dest: {name!r}")
        if m.issym() or m.islnk():
            raise SystemExit(f"refusing link member: {name!r}")
        tar.extract(m, path=dest, set_attrs=False)
        written.append(name)
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True, type=Path)
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--manifest", type=Path, default=None)
    ap.add_argument("--key-file", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = _load_key(args.key_file)
    # If env held the key string from file contents
    if os.environ.get("CHAT_BACKUP_KEY") and args.key_file is None:
        key = os.environ["CHAT_BACKUP_KEY"].strip().encode()

    try:
        plain = Fernet(key).decrypt(args.archive.read_bytes())
    except InvalidToken:
        raise SystemExit("decrypt failed (bad key or corrupt archive)")
    except Exception:
        raise SystemExit("decrypt failed")

    if args.manifest and args.manifest.exists():
        meta = json.loads(args.manifest.read_text())
        digest = hashlib.sha256(plain).hexdigest()
        expected = meta.get("plaintext_tar_sha256")
        if expected and digest != expected:
            raise SystemExit("manifest plaintext hash mismatch")

    if args.dry_run:
        with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tar:
            names = [m.name for m in tar.getmembers() if m.isfile()]
        print(f"dry-run ok members={len(names)} bytes={len(plain)}")
        return 0

    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / "data.tar.gz"
        tar_path.write_bytes(plain)
        with tarfile.open(tar_path, "r:gz") as tar:
            written = _safe_extract(tar, args.dest)
    print(f"restored ok files={len(written)} dest={args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
