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
        with urllib.request.urlopen(request, timeout=2) as response:
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
        _stop(proc)
        console.close()
        log_path = assistant_home / "logs" / "app.log"
        if log_path.is_file():
            assert "shutting down" in log_path.read_text(encoding="utf-8"), "no graceful-shutdown line in app.log"


def _stop(proc: subprocess.Popen) -> None:
    """Ask the app to close its window (taskkill without /F), force-kill only if that fails."""
    if proc.poll() is None:
        for pid in _child_pids(proc.pid) or [proc.pid]:
            subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True, timeout=30)
        try:
            proc.wait(timeout=SHUTDOWN_TIMEOUT)
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
