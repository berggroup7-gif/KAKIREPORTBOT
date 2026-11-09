import sys
from common import api
chat, path = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read().strip()
parts, cur = [], ""
for line in text.split("\n"):
    while len(line) > 4000: parts.append(line[:4000]); line = line[4000:]
    if len(cur) + len(line) + 1 > 4000: parts.append(cur); cur = ""
    cur += line + "\n"
if cur.strip(): parts.append(cur)
for p in parts: api("sendMessage", chat_id=int(chat), text=p, disable_web_page_preview=True)  # plain text, no parse_mode
print(f"sent {len(parts)} message(s)")
