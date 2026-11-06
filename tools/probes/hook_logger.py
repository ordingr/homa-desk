"""Append one JSON line per POST body received on 127.0.0.1 (probe P6)."""
import argparse
import json
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_handler(out_path, lock):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            text = raw.decode("utf-8", errors="replace")
            try:
                body = json.loads(text)
            except ValueError:
                body = {"_unparsed": text}
            record = {
                "received_at": datetime.now(timezone.utc).isoformat(),
                "path": self.path,
                "body": body,
            }
            line = json.dumps(record, ensure_ascii=False)
            with lock:
                with open(out_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                print(line, flush=True)
            payload = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=17199)
    ap.add_argument("--out", required=True, help="JSONL file to append to")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.out, threading.Lock()))
    print(f"listening on http://127.0.0.1:{args.port}/ , appending to {args.out} (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
