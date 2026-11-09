"""Stdio MCP shim for Claude Desktop: forwards each JSON-RPC line to the running app's /mcp endpoint.

Stdlib only. The HTTP server is stateless and answers in JSON, so each line is one POST and each
non-empty response body is one line on stdout. Tool definitions live only in the server.
"""
import json
import sys
import urllib.error
import urllib.request

from . import send

_ACCEPT = "application/json, text/event-stream"
_MAX_RESPONSE = 4 * 1024 * 1024


def _error_line(request_id, message: str) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": request_id,
                       "error": {"code": -32000, "message": message}}).encode("utf-8")


def _json_lines(body: bytes, content_type: str) -> list:
    if "text/event-stream" not in content_type:
        return [body] if body.strip() else []
    out = []
    for line in body.decode("utf-8", "replace").splitlines():
        if line.startswith("data:") and line[5:].strip():
            out.append(line[5:].strip().encode("utf-8"))
    return out


def forward(line: bytes) -> list:
    """POST one JSON-RPC message to /mcp/; returns the response lines (none for a notification)."""
    try:
        message = json.loads(line)
    except ValueError:
        return [_error_line(None, "Parse error: the line is not valid JSON.")]
    request_id = message.get("id") if isinstance(message, dict) else None
    wants_reply = isinstance(message, list) or (isinstance(message, dict) and "id" in message)

    def fail(text: str) -> list:
        return [_error_line(request_id, text)] if wants_reply else []

    tok = send._read_token()
    if tok is None:
        return fail(f"No usable token at {send.paths.token_file()}; run: python -m homa --init")
    try:
        send._check_listener(send.settings.peek_port())
    except send.ListenerMismatch as exc:
        return fail(str(exc))
    request = urllib.request.Request(
        send._base_url() + "/mcp/", data=line, method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept": _ACCEPT})
    try:
        with send._opener.open(request, timeout=30) as resp:
            body = resp.read(_MAX_RESPONSE)
            return _json_lines(body, resp.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as exc:
        return fail(f"Homa answered HTTP {exc.code} on /mcp.")
    except (urllib.error.URLError, OSError):
        return fail(f"Homa is not running on 127.0.0.1:{send.settings.peek_port()}.")


def main() -> int:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        for out in forward(raw):
            stdout.write(out + b"\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
