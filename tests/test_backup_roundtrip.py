#!/usr/bin/env python3
"""Tempdir encrypt/decrypt/manifest roundtrip. No real chat content required."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENC = ROOT / "tools" / "encrypt_chat_backup.py"
RES = ROOT / "tools" / "restore_chat_backup.py"


class BackupRoundtrip(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "src"
            src.mkdir()
            (src / "offset.txt").write_text("42\n")
            (src / "stats.json").write_text('{"ok":true}\n')
            (src / "sample.jsonl").write_text('{"type":"message","text":"x"}\n')
            out = td / "chat_data.fernet"
            man = td / "manifest.json"
            key = td / "chat_backup.key"
            r = subprocess.run(
                [
                    sys.executable,
                    str(ENC),
                    "--src",
                    str(src),
                    "--out",
                    str(out),
                    "--manifest",
                    str(man),
                    "--key-file",
                    str(key),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("encrypted ok", r.stdout)
            meta = json.loads(man.read_text())
            self.assertEqual(meta["file_count"], 3)
            self.assertTrue(out.exists())
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)

            dest = td / "out"
            env = os.environ.copy()
            env["CHAT_BACKUP_KEY"] = key.read_text().strip()
            # ensure key not accidentally in command argv beyond env
            r2 = subprocess.run(
                [
                    sys.executable,
                    str(RES),
                    "--archive",
                    str(out),
                    "--manifest",
                    str(man),
                    "--dest",
                    str(dest),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("restored ok", r2.stdout)
            self.assertEqual((dest / "offset.txt").read_text(), "42\n")
            self.assertEqual((dest / "stats.json").read_text(), '{"ok":true}\n')
            self.assertTrue((dest / "sample.jsonl").exists())

    def test_syntax_modules(self):
        for name in (
            "logger.py",
            "common.py",
            "admin.py",
            "chatstats.py",
            "gemini_reply.py",
            "stats.py",
            "send_report.py",
            "sender_run.py",
            "list_day.py",
        ):
            path = ROOT / name
            src = path.read_text(encoding="utf-8")
            compile(src, str(path), "exec")


if __name__ == "__main__":
    unittest.main()
