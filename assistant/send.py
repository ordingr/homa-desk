import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional

from . import netinfo, paths, settings
from .security import token

KINDS = ("custom", "waiting", "done", "meeting", "reminder")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


class ListenerMismatch(Exception):
    pass


def _base_url() -> str:
    return f"http://127.0.0.1:{settings.peek_port()}"


def _read_token():
    try:
        return token.read()
    except (OSError, ValueError):
        return None


def _recorded_pid() -> Optional[int]:
    try:
        return int(paths.home().joinpath("assistant.pid").read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _check_listener(port: int) -> None:
    recorded = _recorded_pid()
    owner = netinfo.listener_pid(port)
    if owner is None:
        return
    if recorded is not None:
        if recorded != owner:
            raise ListenerMismatch(
                f"Port {port} is held by process {owner}, not the Assistant (pid {recorded}); nothing was sent.")
        return
    image = netinfo.process_image(owner)
    if image is None or not os.path.basename(image).lower() in ("python.exe", "pythonw.exe"):
        held = os.path.basename(image) if image else "an unidentified process"
        raise ListenerMismatch(
            f"No assistant.pid, and port {port} is held by {held} (pid {owner}); nothing was sent.")


def post(route: str, payload: dict, timeout: float = 5.0):
    """POST JSON to the local app. Returns (status, body); raises ConnectionError, or ListenerMismatch
    when another process owns the port."""
    tok = _read_token()
    if tok is None:
        raise ConnectionError(f"no usable token at {paths.token_file()}; run: python -m assistant --init")
    _check_listener(settings.peek_port())
    request = urllib.request.Request(
        _base_url() + route,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
    )
    try:
        with _opener.open(request, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError):
        raise ConnectionError(f"Assistant is not running on 127.0.0.1:{settings.peek_port()}") from None


def _error_text(body: bytes) -> str:
    try:
        err = json.loads(body).get("error")
        if isinstance(err, dict):
            return str(err.get("message", err))
        return str(err)
    except (ValueError, AttributeError):
        return body[:200].decode("utf-8", "replace")


def run_send(args) -> int:
    payload = {"title": args.title, "kind": args.kind}
    if getattr(args, "id", None) is not None:
        payload["id"] = args.id
    if args.detail is not None:
        payload["detail"] = args.detail
    if args.priority is not None:
        payload["priority"] = args.priority
    if args.sticky:
        payload["sticky"] = True
    if args.url is not None:
        payload["url"] = args.url
    try:
        status, body = post("/v1/cards", payload)
    except (ConnectionError, ListenerMismatch) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if status in (200, 201):
        try:
            print(json.loads(body).get("id", "ok"))
        except ValueError:
            print("ok")
        return 0
    if status == 404:
        print("Assistant answered 404: this build has no /v1/cards route yet", file=sys.stderr)
    else:
        print(f"Assistant answered {status}: {_error_text(body)}", file=sys.stderr)
    return 1


def run_shutdown() -> int:
    try:
        status, body = post("/v1/shutdown", {})
    except ListenerMismatch as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ConnectionError as exc:
        print(str(exc))
        return 0
    if status in (200, 202, 204):
        print("shutdown requested")
        return 0
    if status == 404:
        print("this build has no /v1/shutdown route yet", file=sys.stderr)
        return 2
    print(f"Assistant answered {status}: {_error_text(body)}", file=sys.stderr)
    return 1
