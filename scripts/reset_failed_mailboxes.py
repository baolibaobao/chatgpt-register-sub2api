import json
import shutil
from datetime import datetime
from pathlib import Path

p = Path("data/outlook_token_state.json")
if not p.exists():
    print("[INFO] No state file found. Nothing to reset.")
    raise SystemExit(0)

backup = p.with_name(p.name + ".bak-reset-failed-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
shutil.copy2(p, backup)
state = json.loads(p.read_text(encoding="utf-8"))
removed = []
for email, item in list(state.items()):
    if isinstance(item, dict) and str(item.get("state") or "").lower() == "failed":
        removed.append(email)
        state.pop(email, None)

if state:
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
else:
    p.unlink()

print(f"[INFO] Backup: {backup}")
print(f"[INFO] Reset failed count: {len(removed)}")
for email in removed:
    print(f"  reset: {email}")
if not removed:
    print("[INFO] No failed mailboxes. Used mailboxes were kept unchanged.")