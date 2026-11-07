import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STARTUP_TIMEOUT = 30.0
SHUTDOWN_TIMEOUT = 15.0

_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
    ]


def _child_pids(parent: int):
    """Direct children of ``parent``; the venv launcher on Windows starts the real interpreter as a child."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    children = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == parent:
                children.append(entry.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return children


def _health_ok(port: int, token: str) -> bool:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/health",
                                     headers={"Authorization": f"Bearer {token}"})
    try:
        with _DIRECT.open(request, timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="session", autouse=True)
def isolated_home_env(tmp_path_factory):
    """Point this process's own ASSISTANT_HOME at a temp dir so in-process tests never touch ~/.assistant."""
    home = tmp_path_factory.mktemp("inproc_home")
    previous = os.environ.get("ASSISTANT_HOME")
    os.environ["ASSISTANT_HOME"] = str(home)
    yield home
    if previous is None:
        os.environ.pop("ASSISTANT_HOME", None)
    else:
        os.environ["ASSISTANT_HOME"] = previous


@pytest.fixture(scope="session")
def assistant_home(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("assistant_home")


@pytest.fixture(scope="session")
def assistant_port() -> int:
    return _free_port()


@pytest.fixture(scope="session")
def assistant_env(assistant_home) -> dict:
    return dict(os.environ, ASSISTANT_HOME=str(assistant_home))


@pytest.fixture(scope="session")
def running_app(assistant_home, assistant_port, assistant_env):
    """Start ``python -m assistant`` against an isolated home and yield (base_url, token)."""
    (assistant_home / "settings.json").write_text(
        json.dumps({"schema": 1, "port": assistant_port}), encoding="utf-8")
    console = open(assistant_home / "console.txt", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "assistant"], cwd=REPO_ROOT, env=assistant_env,
                            stdout=console, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    token_path = assistant_home / "token"
    token = ""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"assistant exited early with code {proc.returncode}:\n"
                                   + (assistant_home / "console.txt").read_text(errors="replace"))
            if not token and token_path.is_file():
                token = token_path.read_text(encoding="ascii").strip()
            if token and _health_ok(assistant_port, token):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("assistant did not answer /v1/health within "
                               f"{STARTUP_TIMEOUT:.0f}s:\n"
                               + (assistant_home / "console.txt").read_text(errors="replace"))
        yield f"http://127.0.0.1:{assistant_port}", token
    finally:
        _stop(proc, assistant_port, token)
        console.close()
        log_path = assistant_home / "logs" / "app.log"
        if log_path.is_file():
            assert "shutting down" in log_path.read_text(encoding="utf-8"), "no graceful-shutdown line in app.log"


def _wait_exit(proc: subprocess.Popen, timeout: float) -> bool:
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def _stop(proc: subprocess.Popen, port: int | None = None, token: str = "") -> None:
    """Graceful stop: POST /v1/shutdown, then taskkill without /F (retried; it can hit a window that is
    mid-destruction and print "Invalid window handle"), force-kill only if both fail."""
    if proc.poll() is None:
        exited = False
        if port and token:
            request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/shutdown", data=b"{}", method="POST",
                                             headers={"Authorization": f"Bearer {token}",
                                                      "Content-Type": "application/json"})
            try:
                _DIRECT.open(request, timeout=5).close()
            except (urllib.error.URLError, OSError):
                pass
            exited = _wait_exit(proc, 5.0)
        for _ in range(3):
            if exited:
                break
            for pid in _child_pids(proc.pid) or [proc.pid]:
                subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True, timeout=30)
            exited = _wait_exit(proc, 0.3)
        try:
            proc.wait(timeout=SHUTDOWN_TIMEOUT if not exited else 1)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
            proc.wait(timeout=10)
            pytest.fail("assistant did not shut down gracefully and was force-killed")


class Api:
    """Tiny JSON client for the running app. ``token=False`` sends no Authorization header."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token
        self.host, _, self.port = base_url.removeprefix("http://").partition(":")

    def request(self, method: str, path: str, body=None, *, token=True, raw: bytes | None = None, headers=None):
        import http.client
        hdrs = dict(headers or {})
        if token is True:
            hdrs["Authorization"] = f"Bearer {self.token}"
        elif isinstance(token, str):
            hdrs["Authorization"] = f"Bearer {token}"
        payload = raw
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection(self.host, int(self.port), timeout=10)
        try:
            conn.request(method, path, body=payload, headers=hdrs)
            response = conn.getresponse()
            data = response.read()
        finally:
            conn.close()
        try:
            parsed = json.loads(data) if data else None
        except ValueError:
            parsed = None
        return response.status, parsed

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)


@pytest.fixture(scope="session")
def api(running_app) -> Api:
    return Api(*running_app)


def wait_until(predicate, timeout: float = 2.0, step: float = 0.05):
    """Poll ``predicate`` until it returns a truthy value (returned) or the timeout passes (returns the last value)."""
    deadline = time.monotonic() + timeout
    value = predicate()
    while not value and time.monotonic() < deadline:
        time.sleep(step)
        value = predicate()
    return value


# ---- M2 helpers: extra app instances, a local ICS feed server, MCP clients ----------------------------------

import contextlib
import datetime as _dt
import http.server
import threading


@contextlib.contextmanager
def launch_app(home: Path, port: int, settings: dict | None = None, test_mode: bool = False):
    """Start a second, private ``python -m assistant`` and yield (base_url, token); stops it gracefully."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(json.dumps({"schema": 1, "port": port, **(settings or {})}), encoding="utf-8")
    env = dict(os.environ, ASSISTANT_HOME=str(home))
    env.pop("ASSISTANT_TEST_MODE", None)
    if test_mode:
        env["ASSISTANT_TEST_MODE"] = "1"
    console = open(home / "console.txt", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "assistant"], cwd=REPO_ROOT, env=env, stdout=console,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    token = ""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"assistant exited early with code {proc.returncode}:\n"
                                   + (home / "console.txt").read_text(errors="replace"))
            if not token and (home / "token").is_file():
                token = (home / "token").read_text(encoding="ascii").strip()
            if token and _health_ok(port, token):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(f"assistant did not answer /v1/health within {STARTUP_TIMEOUT:.0f}s:\n"
                               + (home / "console.txt").read_text(errors="replace"))
        yield f"http://127.0.0.1:{port}", token
    finally:
        token_path = home / "token"
        current = token_path.read_text(encoding="ascii").strip() if token_path.is_file() else token
        _stop(proc, port, current)
        console.close()


@pytest.fixture
def app_factory(tmp_path):
    """``app_factory(settings=None, test_mode=False)`` -> context manager yielding (base_url, token) for a private app.
    The private home is ``tmp_path / "private_home"`` (read it back as ``tmp_path / "private_home"``)."""
    def make(settings: dict | None = None, test_mode: bool = False, home: Path | None = None):
        return launch_app(home or (tmp_path / "private_home"), _free_port(), settings, test_mode)
    return make


def _ics_stamp(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(events: list) -> str:
    """Minimal VCALENDAR. Event keys: uid, subject, start, end (epoch seconds), location, description, declined."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Assistant QA//EN"]
    for e in events:
        lines += ["BEGIN:VEVENT", f"UID:{e['uid']}", f"DTSTAMP:{_ics_stamp(time.time())}",
                  f"DTSTART:{_ics_stamp(e['start'])}", f"DTEND:{_ics_stamp(e['end'])}", f"SUMMARY:{e['subject']}"]
        if e.get("location"):
            lines.append(f"LOCATION:{e['location']}")
        if e.get("description"):
            lines.append(f"DESCRIPTION:{e['description']}")
        if e.get("declined"):
            lines.append("ATTENDEE;PARTSTAT=DECLINED:mailto:user@example.test")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


class IcsServer:
    """Local HTTP server for calendar feeds: ``serve(path, body, status=200)``; ``hits`` lists requested paths."""

    def __init__(self) -> None:
        self.routes: dict = {}
        self.hits: list = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                status, body = outer.routes.get(self.path.split("?")[0], (404, b"not found"))
                self.send_response(status)
                self.send_header("Content-Type", "text/calendar; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def serve(self, path: str, body, status: int = 200) -> str:
        self.routes[path] = (status, body.encode("utf-8") if isinstance(body, str) else body)
        return self.url(path)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def ics_server():
    server = IcsServer()
    yield server
    server.close()


class McpResult:
    def __init__(self, is_error: bool, text: str, structured):
        self.is_error, self.text, self.structured = is_error, text, structured

    def card_id(self) -> str:
        """The card id from a send_card result, whether returned as structured content, JSON text or bare text."""
        data = self.structured
        if isinstance(data, dict):
            data = data.get("result", data)
        if isinstance(data, dict) and data.get("id"):
            return str(data["id"])
        if isinstance(data, str) and data.strip():
            return data.strip()
        try:
            parsed = json.loads(self.text)
            if isinstance(parsed, dict) and parsed.get("id"):
                return str(parsed["id"])
        except ValueError:
            pass
        return self.text.strip()


class McpHttp:
    """Sync wrapper over the ``mcp`` package's Streamable HTTP client; one short session per call."""

    def __init__(self, base_url: str, token: str, headers: dict | None = None):
        self.url = base_url.rstrip("/") + "/mcp"
        self.headers = dict({"Authorization": f"Bearer {token}"} if token else {}, **(headers or {}))

    def _run(self, fn):
        import asyncio

        async def go():
            import httpx2
            from mcp import Client
            from mcp.client.streamable_http import streamable_http_client
            async with httpx2.AsyncClient(headers=self.headers, trust_env=False, timeout=30) as http_client:
                async with Client(streamable_http_client(self.url, http_client=http_client)) as client:
                    return await fn(client)
        return asyncio.run(asyncio.wait_for(go(), 60))

    def list_tools(self):
        async def fn(client):
            return (await client.list_tools()).tools
        return self._run(fn)

    def call(self, name: str, arguments: dict | None = None) -> McpResult:
        async def fn(client):
            try:
                result = await client.call_tool(name, arguments or {})
            except Exception as exc:
                return McpResult(True, f"{type(exc).__name__}: {exc}", None)
            text = "\n".join(getattr(block, "text", "") or "" for block in result.content)
            return McpResult(bool(result.is_error), text, result.structured_content)
        return self._run(fn)


def run_stdio_shim(home: Path, lines: list, timeout: float = 20.0, expect: int | None = None):
    """Run ``python -m assistant.mcp_stdio`` against ``home``, feed JSON-RPC ``lines``; return (stdout lines, stderr)."""
    env = dict(os.environ, ASSISTANT_HOME=str(home))
    proc = subprocess.Popen([sys.executable, "-m", "assistant.mcp_stdio"], cwd=REPO_ROOT, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out: list = []
    want = expect if expect is not None else sum(1 for line in lines if '"id"' in line)
    done = threading.Event()

    def read():
        for raw in proc.stdout:
            out.append(raw.decode("utf-8", "replace").rstrip("\r\n"))
            if len(out) >= want:
                break
        done.set()

    threading.Thread(target=read, daemon=True).start()
    try:
        proc.stdin.write(("\n".join(lines) + "\n").encode("utf-8"))
        proc.stdin.flush()
        done.wait(timeout)
    finally:
        with contextlib.suppress(Exception):
            proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    return out, proc.stderr.read().decode("utf-8", "replace")


class MiniLive:
    """In-process Assistant server (real create_app + real uvicorn thread) with the MCP app mounted, on a private home."""

    def __init__(self, home, port, store, reminders, server, mcp_server):
        self.home, self.port, self.store, self.reminders = home, port, store, reminders
        self.server, self.mcp_server = server, mcp_server
        self.base_url = f"http://127.0.0.1:{port}"

    @property
    def token(self) -> str:
        return (self.home / "token").read_text(encoding="ascii").strip()

    def api(self) -> "Api":
        return Api(self.base_url, self.token)

    def mcp(self, token: str | None = None, headers: dict | None = None) -> "McpHttp":
        return McpHttp(self.base_url, self.token if token is None else token, headers)


@pytest.fixture(scope="module")
def mcp_live(tmp_path_factory):
    """Skips until assistant/server/mcp.py exists. Home, store, scheduler and reminders are private to the module."""
    build = pytest.importorskip("assistant.server.mcp", reason="assistant/server/mcp.py (M2-2) not present").build_mcp
    from assistant import paths
    from assistant.cards.store import CardStore
    from assistant.reminders.store import ReminderStore
    from assistant.scheduler import Scheduler
    from assistant.security import token as token_mod
    from assistant.server.app import ServerThread, create_app

    home = tmp_path_factory.mktemp("mcp_live_home")
    port = _free_port()
    mp = pytest.MonkeyPatch()
    mp.setenv("ASSISTANT_HOME", str(home))
    paths.ensure_layout()
    (home / "settings.json").write_text(json.dumps({"schema": 1, "port": port}), encoding="utf-8")
    token_mod.ensure()
    store = CardStore(path=home / "state.json")
    scheduler = Scheduler()
    scheduler.start()
    reminders = ReminderStore(scheduler, fire=lambda r: None, path=home / "reminders.json")
    mcp_server, asgi = build(store, reminders)
    app = create_app(token_mod.read, store, reminders, scheduler, mcp_app=asgi)
    server = ServerThread(app, port)
    assert server.start(), "in-process server did not start"
    live = MiniLive(home, port, store, reminders, server, mcp_server)
    try:
        yield live
    finally:
        server.stop()
        scheduler.stop()
        mp.undo()
