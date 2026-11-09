import os, json, requests
from zoneinfo import ZoneInfo
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
TZ = ZoneInfo("Asia/Kuala_Lumpur")

_READ_ALLOW = {
    "getUpdates", "getMe", "getChat", "getChatAdministrators", "getChatMember",
    "getWebhookInfo", "getFile", "getUserProfilePhotos",
}

# Test hook: list of blocked method names (no secrets)
blocked_calls = []

def no_send_enabled() -> bool:
    return os.environ.get("NO_SEND", "").strip().lower() in {"1", "true", "yes", "on"}

def token():
    t = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not t:
        envp = os.path.join(BASE, ".env")
        if os.path.exists(envp):
            for l in open(envp):
                if l.startswith("TELEGRAM_BOT_TOKEN="):
                    t = l.split("=", 1)[1].strip()
                    break
    return t

def api(method, **params):
    if no_send_enabled() and method not in _READ_ALLOW:
        blocked_calls.append(method)
        raise RuntimeError(f"NO_SEND blocks Telegram method {method}")
    r = requests.post(
        f"https://api.telegram.org/bot{token()}/{method}",
        json=params,
        timeout=((10, 40) if method == "getUpdates" else (10, 30)),
    )
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"{method} failed: {j.get('description')}")
    return j["result"]
