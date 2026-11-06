"""Print every hook event captured by hook_logger.py, grouped by session_id (probe P6)."""
import argparse
import json
import sys


def load(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                print(f"line {n}: not JSON, skipped", file=sys.stderr)
                continue
            body = rec.get("body")
            rows.append((rec.get("received_at", ""), body if isinstance(body, dict) else {}))
    return rows


def first(body, *keys):
    for k in keys:
        if body.get(k) not in (None, ""):
            return str(body[k])
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("jsonl")
    args = ap.parse_args()
    rows = load(args.jsonl)
    if not rows:
        print("no events")
        return
    sessions = {}
    for ts, body in rows:
        sessions.setdefault(first(body, "session_id") or "(none)", []).append((ts, body))
    cols = ("time", "hook_event_name", "matcher/notification_type", "cwd", "session_title")
    for sid, events in sessions.items():
        print(f"\n=== session_id {sid}  ({len(events)} events)")
        table = []
        for ts, b in events:
            table.append((
                ts[11:23],
                first(b, "hook_event_name"),
                first(b, "notification_type", "matcher", "tool_name"),
                first(b, "cwd"),
                first(b, "session_title"),
            ))
        widths = [max(len(c), *(len(r[i]) for r in table)) for i, c in enumerate(cols)]
        print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
        for r in table:
            print("  ".join(v.ljust(w) for v, w in zip(r, widths)))
        names = sorted({r[1] for r in table if r[1]})
        print("distinct hook_event_name: " + ", ".join(names))


if __name__ == "__main__":
    main()
