"""Runtime identity from env (never hardcode chat/owner ids in source)."""
from __future__ import annotations

import os

# Populated by refresh(); import sites should use identity.GROUP etc.
GROUP: int = 0
OWNER: str = ""
OWNER_ID: int = 0
OWNER_CHAT: int = 0


def refresh() -> None:
    """Load GROUP_CHAT_ID, OWNER_ID, OWNER_USERNAME from environment."""
    global GROUP, OWNER, OWNER_ID, OWNER_CHAT
    g = os.environ.get("GROUP_CHAT_ID", "").strip()
    o = os.environ.get("OWNER_USERNAME", "").strip().lstrip("@")
    oid = os.environ.get("OWNER_ID", "").strip()
    if not (g and o and oid):
        if os.environ.get("KAKIREPORTBOT_TEST_IDENTITY", "").strip().lower() in {"1", "true", "yes", "on"}:
            GROUP = -1009999999999
            OWNER = "test_ketua"
            OWNER_ID = 100000001
            OWNER_CHAT = OWNER_ID
            return
        raise RuntimeError(
            "fail-closed: set GROUP_CHAT_ID, OWNER_ID, OWNER_USERNAME (or KAKIREPORTBOT_TEST_IDENTITY=1 for tests)"
        )
    GROUP = int(g)
    OWNER = o.lower()
    OWNER_ID = int(oid)
    OWNER_CHAT = OWNER_ID


refresh()
