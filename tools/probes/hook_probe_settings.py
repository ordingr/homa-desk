"""Print or merge the Claude Code hooks block used by probe P6."""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

EVENTS = [
    "PermissionRequest",
    "Notification",
    "Stop",
    "StopFailure",
    "UserPromptSubmit",
    "PostToolUse",
    "SessionStart",
    "SessionEnd",
]


def build_block(port):
    url = f"http://127.0.0.1:{port}/hook"
    handler = {"type": "http", "url": url, "onFailure": "continue"}
    return {"hooks": {ev: [{"hooks": [dict(handler)]}] for ev in EVENTS}}


def merge(existing, block):
    hooks = existing.setdefault("hooks", {})
    for ev, entries in block["hooks"].items():
        current = hooks.setdefault(ev, [])
        urls = {
            h.get("url")
            for entry in current
            for h in entry.get("hooks", [])
            if isinstance(h, dict)
        }
        for entry in entries:
            if entry["hooks"][0]["url"] not in urls:
                current.append(entry)
    return existing


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=17199)
    ap.add_argument("--print", dest="do_print", action="store_true", help="print the hooks block as JSON")
    ap.add_argument("--write", metavar="SETTINGS_JSON", help="merge into this settings file after writing a .bak copy")
    args = ap.parse_args()
    block = build_block(args.port)
    if not args.do_print and not args.write:
        ap.error("give --print and/or --write")
    if args.do_print:
        print(json.dumps(block, indent=2))
    if args.write:
        path = Path(args.write).expanduser()
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8") or "{}")
            bak = path.with_name(path.name + ".bak")
            if bak.exists():
                bak = path.with_name(f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
            shutil.copy2(path, bak)
            print(f"backup: {bak}", file=sys.stderr)
        else:
            existing = {}
        merged = merge(existing, block)
        path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        print(f"merged hooks into {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
