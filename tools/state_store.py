#!/usr/bin/env python3
"""Encrypted durable runtime state (offset/chats/stats/...). Fail closed on missing/invalid."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

REQUIRED_MEMBERS = ("offset.txt", "stats.json", "chats.json")


def _key_from_env_or_file(key_file: Path | None) -> bytes:
    env = os.environ.get("CHAT_BACKUP_KEY", "").strip()
    if env:
        return env.encode()
    if key_file is None:
        raise SystemExit("fail-closed: CHAT_BACKUP_KEY missing and no --key-file")
    if not key_file.exists():
        raise SystemExit("fail-closed: key file missing")
    return key_file.read_bytes().strip()


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def pack_state(data_dir: Path, patterns: list[str] | None = None) -> tuple[bytes, dict]:
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        raise SystemExit("fail-closed: data dir missing")
    patterns = patterns or [
        "offset.txt",
        "stats.json",
        "chats.json",
        "settings.json",
        "nicknames.json",
        "name_prefs.json",
        "gemini_usage.json",
        "winners.json",
        "private_users.json",
        "instant_acks.json",
        "report_requests.json",
        "batch_seen_ids.json",
        "batch_outbound_marks.json",
        "last_replied.txt",
        "*.json",
        "*.jsonl",
        "*.txt",
    ]
    files: list[Path] = []
    for pat in patterns:
        if "/" in pat:
            files.extend(data_dir.glob(pat))
        elif pat.startswith("*"):
            files.extend(data_dir.rglob(pat))
        else:
            p = data_dir / pat
            if p.exists():
                files.append(p)
    # unique files only under data_dir
    uniq = []
    seen = set()
    for p in files:
        if not p.is_file():
            continue
        rp = p.resolve()
        if data_dir not in rp.parents and rp != data_dir:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append(p)
    names = {p.relative_to(data_dir).as_posix() for p in uniq}
    missing = [m for m in REQUIRED_MEMBERS if m not in names]
    if missing:
        raise SystemExit(f"fail-closed: required state missing: {missing}")

    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / "state.tar.gz"
        meta_files = []
        with tarfile.open(tar_path, "w:gz") as tar:
            for p in sorted(uniq, key=lambda x: x.as_posix()):
                arc = p.relative_to(data_dir).as_posix()
                if arc.startswith("/") or ".." in Path(arc).parts:
                    raise SystemExit(f"fail-closed: unsafe path {arc}")
                tar.add(p, arcname=arc)
                b = p.read_bytes()
                meta_files.append({"path": arc, "size": len(b), "sha256": _sha256_bytes(b)})
        plain = tar_path.read_bytes()
    # Detailed file list stays INSIDE the encrypted tar only (public meta is opaque).
    inner_manifest = {
        "format": "fernet-state-tar-gz-v2-inner",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "file_count": len(meta_files),
        "plaintext_tar_sha256": None,  # filled after inner tar rebuilt with manifest
        "files": meta_files,
        "required": list(REQUIRED_MEMBERS),
    }
    # Rebuild tar including _inner_manifest.json
    with tempfile.TemporaryDirectory() as td2:
        tar_path2 = Path(td2) / "state.tar.gz"
        man_path = Path(td2) / "_inner_manifest.json"
        # first pass already in `plain` without inner manifest; rebuild from uniq
        with tarfile.open(tar_path2, "w:gz") as tar:
            for p in sorted(uniq, key=lambda x: x.as_posix()):
                arc = p.relative_to(data_dir).as_posix()
                tar.add(p, arcname=arc)
            man_path.write_text(json.dumps(inner_manifest, indent=2) + "\n")
            tar.add(man_path, arcname="_inner_manifest.json")
        plain = tar_path2.read_bytes()
    public_meta = {
        "format": "fernet-state-tar-gz-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "file_count": len(meta_files),
        "plaintext_tar_sha256": _sha256_bytes(plain),
        # No path names / per-file hashes in public meta
    }
    return plain, public_meta


def encrypt_state(plain: bytes, key: bytes) -> bytes:
    return Fernet(key).encrypt(plain)


def decrypt_state(blob: bytes, key: bytes, expected_sha: str | None = None) -> bytes:
    try:
        plain = Fernet(key).decrypt(blob)
    except InvalidToken:
        raise SystemExit("fail-closed: decrypt failed (bad key or corrupt state)")
    except Exception:
        raise SystemExit("fail-closed: decrypt failed")
    if expected_sha and _sha256_bytes(plain) != expected_sha:
        raise SystemExit("fail-closed: state plaintext hash mismatch")
    return plain


def safe_extract(plain: bytes, dest: Path) -> list[str]:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    import io

    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tar:
        for m in tar.getmembers():
            name = m.name
            if name.startswith("/") or ".." in Path(name).parts:
                raise SystemExit(f"fail-closed: unsafe archive member {name!r}")
            target = (dest / name).resolve()
            if dest not in target.parents and target != dest:
                raise SystemExit(f"fail-closed: path escapes dest {name!r}")
            if m.issym() or m.islnk():
                raise SystemExit(f"fail-closed: refusing link {name!r}")
            tar.extract(m, path=dest, set_attrs=False)
            if m.isfile():
                written.append(name)
    names = set(written)
    missing = [r for r in REQUIRED_MEMBERS if r not in names]
    if missing:
        raise SystemExit(f"fail-closed: restored state missing required: {missing}")
    return written


def cmd_save(args: argparse.Namespace) -> int:
    key = _key_from_env_or_file(args.key_file)
    plain, manifest = pack_state(args.data_dir)
    blob = encrypt_state(plain, key)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(blob)
    manifest["ciphertext_bytes"] = args.out.stat().st_size
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "saved": True,
                "file_count": manifest["file_count"],
                "ciphertext_bytes": manifest["ciphertext_bytes"],
            }
        )
    )
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    if not args.archive.exists():
        raise SystemExit("fail-closed: state archive missing")
    if not args.manifest.exists():
        raise SystemExit("fail-closed: state manifest missing")
    key = _key_from_env_or_file(args.key_file)
    meta = json.loads(args.manifest.read_text())
    fmt = meta.get("format")
    if fmt not in {"fernet-state-tar-gz-v1", "fernet-state-tar-gz-v2"}:
        raise SystemExit("fail-closed: unsupported state format")
    plain = decrypt_state(args.archive.read_bytes(), key, meta.get("plaintext_tar_sha256"))
    written = safe_extract(plain, args.dest)
    # Drop inner manifest from dest if present (not runtime data)
    inner = args.dest / "_inner_manifest.json"
    if inner.exists():
        inner.unlink()
        written = [w for w in written if w != "_inner_manifest.json"]
    print(json.dumps({"loaded": True, "files": len(written)}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("save")
    s.add_argument("--data-dir", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--manifest", type=Path, required=True)
    s.add_argument("--key-file", type=Path, default=None)
    l = sub.add_parser("load")
    l.add_argument("--archive", type=Path, required=True)
    l.add_argument("--manifest", type=Path, required=True)
    l.add_argument("--dest", type=Path, required=True)
    l.add_argument("--key-file", type=Path, default=None)
    args = ap.parse_args()
    if args.cmd == "save":
        return cmd_save(args)
    return cmd_load(args)


if __name__ == "__main__":
    raise SystemExit(main())
