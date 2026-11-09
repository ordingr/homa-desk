"""Overlay test driver (plan section 8.3). Windows only.

    python tools/driver.py start --home <dir> [--port 17171] [--test-mode] [--console]
    python tools/driver.py stop
    python tools/driver.py post --title T --kind waiting [--detail D] [--id X] [--sticky]
    python tools/driver.py ui [--json]
    python tools/driver.py move X Y | clickxy X Y [--right] | drag X1 Y1 X2 Y2
    python tools/driver.py hover <card-id|icon> | click <card-id|icon> [<button>] [--right]
    python tools/driver.py hotkey | type "<text>" | key <name>
    python tools/driver.py screenshot --out <png>
    python tools/driver.py fullpage --query "<q>" --screenshot <png> [--via-api]

Coordinates are physical pixels, the same space as the rectangles in GET /v1/_debug/ui. Nothing here
writes inside the repository: the driver's own state (pid, port, home) lives in %TEMP%\\homa-driver.
Stdlib + ctypes; Pillow is imported only by ``screenshot`` / ``fullpage``.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = Path(tempfile.gettempdir()) / "homa-driver" / "state.json"
DEFAULT_PORT = 17171
STARTUP_TIMEOUT = 30.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

# ---- state and HTTP -------------------------------------------------------------------


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def require_state() -> dict:
    state = load_state()
    if not state.get("home"):
        sys.exit("driver: no running instance recorded; run `start` first")
    return state


def read_token(home: str) -> str:
    return (Path(home) / "token").read_text(encoding="ascii").strip()


def http(method: str, path: str, body=None, state: dict | None = None, timeout: float = 10.0):
    """Return (status, parsed_json_or_None). Uses the recorded port and token."""
    state = state or require_state()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{state['port']}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {read_token(state['home'])}", "Content-Type": "application/json"})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as err:
        raw, status = err.read(), err.code
    try:
        return status, (json.loads(raw) if raw else None)
    except ValueError:
        return status, None


def health_ok(state: dict) -> bool:
    try:
        return http("GET", "/v1/health", state=state, timeout=2)[0] == 200
    except (OSError, urllib.error.URLError, FileNotFoundError):
        return False


# ---- start / stop ---------------------------------------------------------------------


def _real_home_overlap(home: Path) -> str:
    """The real home folder (current or pre-rename) when `home` is one, is inside one, or contains one; else ""."""
    def norm(p: Path) -> str:
        return os.path.normcase(str(p.resolve())).rstrip("\\/")
    target = norm(home)
    for name in (".homa", ".assistant"):
        real = norm(Path.home() / name)
        if target == real or target.startswith(real + os.sep) or real.startswith(target + os.sep):
            return str(Path.home() / name)
    return ""


def cmd_start(args) -> int:
    existing = load_state()
    if existing.get("pid") and health_ok(existing):
        sys.exit(f"driver: an instance is already running (pid {existing['pid']}, home {existing['home']}); run `stop`")
    if args.port == DEFAULT_PORT and not getattr(args, "allow_live_port", False):
        sys.exit(f"driver: port {DEFAULT_PORT} is the live instance's port; pass a free --port, "
                 "or --allow-live-port if you really mean it")
    home = Path(args.home).resolve()
    real = _real_home_overlap(home)
    if real:
        sys.exit(f"driver: --home {home} overlaps the real {real}; use a scratch folder")
    home.mkdir(parents=True, exist_ok=True)
    settings = home / "settings.json"
    try:
        current = json.loads(settings.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        current = {"schema": 1}
    current["port"] = args.port
    settings.write_text(json.dumps(current), encoding="utf-8")
    fake_profile = home / "userprofile"
    fake_local = fake_profile / "AppData" / "Local"
    fake_roaming = fake_profile / "AppData" / "Roaming"
    fake_local.mkdir(parents=True, exist_ok=True)
    fake_roaming.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HOMA_HOME=str(home), USERPROFILE=str(fake_profile), HOME=str(fake_profile),
               LOCALAPPDATA=str(fake_local), APPDATA=str(fake_roaming))
    env.pop("HOMEDRIVE", None)
    env.pop("HOMEPATH", None)
    for name in [n for n in env if n.upper().startswith(("ASSISTANT_", "HOMA_")) and n.upper() != "HOMA_HOME"]:
        del env[name]
    if args.test_mode:
        env["HOMA_TEST_MODE"] = "1"
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    interpreter = exe if args.console or not pythonw.exists() else pythonw
    console = open(home / "console.txt", "wb")
    proc = subprocess.Popen([str(interpreter), "-m", "homa"], cwd=REPO_ROOT, env=env, stdout=console,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    state = {"pid": proc.pid, "port": args.port,
             "home": str(home), "test_mode": bool(args.test_mode)}
    save_state(state)
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"driver: app exited early with code {proc.returncode}:\n"
                     + (home / "console.txt").read_text(errors="replace"))
        if (home / "token").is_file() and health_ok(state):
            print(f"started pid {proc.pid} port {state['port']} home {home} test_mode={state['test_mode']}")
            return 0
        time.sleep(0.2)
    sys.exit(f"driver: no /v1/health answer within {STARTUP_TIMEOUT:.0f}s; see {home / 'console.txt'} and logs/app.log")


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def _child_pids(parent: int) -> list[int]:
    """Direct children of ``parent``: the venv launcher starts the real interpreter as a child."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    found = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == parent:
                found.append(entry.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return found


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def cmd_stop(args) -> int:
    state = load_state()
    if not state.get("home"):
        print("nothing recorded; nothing to stop")
        return 0
    pid = state.get("pid")
    try:
        status, _ = http("POST", "/v1/shutdown", {}, state=state, timeout=5)
        print(f"POST /v1/shutdown -> {status}")
    except (OSError, urllib.error.URLError, FileNotFoundError) as err:
        status = None
        print(f"POST /v1/shutdown failed ({err}); app probably not running")
    if pid and status not in (200, 202, 204):
        for target in _child_pids(pid) or [pid]:
            subprocess.run(["taskkill", "/PID", str(target)], capture_output=True)
        print("no shutdown route answered; asked the window to close (taskkill without /F)")
    if pid:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.2)
        if _alive(pid):
            print(f"pid {pid} still alive after 15 s; taskkill /T /F")
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
            return 1
    log = Path(state["home"]) / "logs" / "app.log"
    clean = log.is_file() and "shutting down" in log.read_text(encoding="utf-8", errors="replace")
    print(f"stopped; graceful-shutdown line in app.log: {clean}")
    STATE_FILE.unlink(missing_ok=True)
    return 0 if clean or status is None else 1


# ---- cards and UI state ---------------------------------------------------------------


def cmd_post(args) -> int:
    body = {"title": args.title, "kind": args.kind}
    for name in ("detail", "id", "url"):
        if getattr(args, name):
            body[name] = getattr(args, name)
    if args.sticky:
        body["sticky"] = True
    status, payload = http("POST", "/v1/cards", body)
    print(status, json.dumps(payload))
    return 0 if status in (200, 201) else 1


def fetch_ui() -> dict:
    status, payload = http("GET", "/v1/_debug/ui")
    if status == 404:
        sys.exit("driver: /v1/_debug/ui is 404; the app must be started with `start --test-mode`")
    if status != 200 or not isinstance(payload, dict):
        sys.exit(f"driver: /v1/_debug/ui returned {status}")
    return payload


def card_windows(ui: dict) -> list[dict]:
    return list(ui.get("cards") or ui.get("windows") or [])


def cmd_ui(args) -> int:
    ui = fetch_ui()
    if args.json:
        print(json.dumps(ui, indent=2))
        return 0
    icon = ui.get("icon") or {}
    print(f"icon  hwnd={icon.get('hwnd')} rect={icon.get('rect')} count={icon.get('count')} "
          f"muted={icon.get('muted')} colour={icon.get('body_colour')} styles={icon.get('exstyle_flags')}")
    if ui.get("menu"):
        print("menu  " + " | ".join(ui["menu"]))
    rows = card_windows(ui)
    print(f"{len(rows)} card window(s)")
    if rows:
        print(f"{'card':<18}{'kind':<10}{'colour':<9}{'rect':<26}{'seen':<6}{'blink':<6}monitor")
        for w in rows:
            print(f"{str(w.get('id', w.get('card_id', '')))[:17]:<18}{str(w.get('kind', '')):<10}"
                  f"{str(w.get('colour', w.get('color', ''))):<9}{str(w.get('rect')):<26}"
                  f"{str(w.get('seen')):<6}{str(w.get('blinking')):<6}{w.get('monitor', '')}")
    return 0


# ---- pointer and keyboard via SendInput ----------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x1, 0x2, 0x4
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x8, 0x10
MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_ABSOLUTE = 0x4000, 0x8000
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x2, 0x4
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT

VK = {"enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20, "up": 0x26,
      "down": 0x28, "left": 0x25, "right": 0x27, "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
      "f11": 0x7A, "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}


def _make_dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    if _user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
        raise OSError(f"SendInput failed: {ctypes.get_last_error()}")


def _mouse(flags: int, dx: int = 0, dy: int = 0) -> INPUT:
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
    return i


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return i


def move_to(x: int, y: int) -> None:
    _make_dpi_aware()
    left, top = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN), _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width, height = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    nx = round((x - left) * 65535 / max(width - 1, 1))
    ny = round((y - top) * 65535 / max(height - 1, 1))
    _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))
    time.sleep(0.05)


def pointer_position() -> tuple[int, int]:
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def click_at(x: int, y: int, right: bool = False) -> None:
    move_to(x, y)
    down, up = (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP) if right else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
    _send(_mouse(down))
    time.sleep(0.05)
    _send(_mouse(up))


def drag(x1: int, y1: int, x2: int, y2: int, steps: int = 20) -> None:
    move_to(x1, y1)
    _send(_mouse(MOUSEEVENTF_LEFTDOWN))
    for n in range(1, steps + 1):
        move_to(x1 + (x2 - x1) * n // steps, y1 + (y2 - y1) * n // steps)
        time.sleep(0.01)
    _send(_mouse(MOUSEEVENTF_LEFTUP))


def press_keys(names: list[str]) -> None:
    """Press the keys together (chord) then release in reverse order: ['ctrl', 'alt', 'space']."""
    codes = []
    for name in names:
        n = name.lower()
        if n in VK:
            codes.append(VK[n])
        elif len(n) == 1:
            codes.append(ord(n.upper()))
        else:
            sys.exit(f"driver: unknown key {name!r}; known: {', '.join(sorted(VK))} or a single character")
    _send(*[_key(c) for c in codes])
    time.sleep(0.05)
    _send(*[_key(c, flags=KEYEVENTF_KEYUP) for c in reversed(codes)])


def type_text(text: str) -> None:
    for ch in text:
        _send(_key(0, ord(ch), KEYEVENTF_UNICODE), _key(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        time.sleep(0.01)


def _centre(rect) -> tuple[int, int]:
    left, top, right, bottom = rect
    return (left + right) // 2, (top + bottom) // 2


def resolve_target(ui: dict, target: str, button: str | None = None) -> tuple[int, int]:
    if target == "icon":
        return _centre(ui["icon"]["rect"])
    for w in card_windows(ui):
        if str(w.get("id", w.get("card_id"))) == target:
            if button:
                buttons = w.get("buttons") or {}
                if button not in buttons:
                    sys.exit(f"driver: card {target} reports no button {button!r}; it reports {sorted(buttons)}")
                return _centre(buttons[button])
            return _centre(w["rect"])
    sys.exit(f"driver: no window for {target!r} in /v1/_debug/ui")


def cmd_hotkey(args) -> int:
    settings = Path(require_state()["home"]) / "settings.json"
    combo = "ctrl+alt+space"
    try:
        combo = json.loads(settings.read_text(encoding="utf-8")).get("hotkey", combo)
    except (OSError, ValueError):
        pass
    press_keys(combo.split("+"))
    print(f"pressed {combo}")
    return 0


# ---- screenshots and the full page ---------------------------------------------------


def grab(out: Path) -> None:
    try:
        from PIL import ImageGrab
    except ImportError:
        sys.exit("driver: Pillow is required for screenshots: pip install Pillow")
    _make_dpi_aware()
    out.parent.mkdir(parents=True, exist_ok=True)
    image = ImageGrab.grab(all_screens=True)
    image.save(out)
    print(f"saved {out} ({image.width}x{image.height})")


def cmd_screenshot(args) -> int:
    grab(Path(args.out))
    return 0


def _fullpage_via_api(args, state: dict) -> int:
    """Ask the running app to open the full page itself (it mints the launch nonce), screenshot, then close it."""
    status, body = http("POST", "/v1/fullpage/open", {"query": args.query}, state=state)
    if status in (404, 405):
        sys.exit("driver: this build has no POST /v1/fullpage/open (M3b-9); use F11 from the popup instead")
    if status >= 400:
        sys.exit(f"driver: /v1/fullpage/open answered {status}: {body}")
    time.sleep(args.wait)
    grab(Path(args.screenshot))
    http("POST", "/v1/fullpage/close", {}, state=state)
    return 0


def cmd_fullpage(args) -> int:
    state = require_state()
    if getattr(args, "via_api", False):
        return _fullpage_via_api(args, state)
    from urllib.parse import quote
    status, _ = http("GET", "/app/", state=state)
    if status == 404:
        sys.exit("driver: /app is not served by this build (full page lands in M3b)")
    edge = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                             r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if Path(p).exists()), None)
    if edge is None:
        sys.exit("driver: msedge.exe not found")
    url = f"http://127.0.0.1:{state['port']}/app/?q={quote(args.query)}"
    profile = Path(tempfile.gettempdir()) / "homa-driver" / "edge-profile"
    proc = subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run"])
    time.sleep(args.wait)
    grab(Path(args.screenshot))
    proc.terminate()
    return 0


# ---- CLI -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start", help="launch the app and wait for /v1/health")
    p.add_argument("--home", required=True, help="scratch HOMA_HOME (never ~/.homa)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--allow-live-port", action="store_true", help="permit the live instance's default port 17171")
    p.add_argument("--test-mode", action="store_true", help="set HOMA_TEST_MODE=1 (exposes /v1/_debug/ui)")
    p.add_argument("--console", action="store_true", help="use python.exe instead of pythonw.exe")
    p.set_defaults(fn=cmd_start)

    sub.add_parser("stop", help="POST /v1/shutdown and wait for exit").set_defaults(fn=cmd_stop)

    p = sub.add_parser("post", help="POST /v1/cards")
    p.add_argument("--title", required=True)
    p.add_argument("--kind", default="custom", choices=["waiting", "done", "meeting", "reminder", "custom"])
    p.add_argument("--detail")
    p.add_argument("--id")
    p.add_argument("--url")
    p.add_argument("--sticky", action="store_true")
    p.set_defaults(fn=cmd_post)

    p = sub.add_parser("ui", help="print GET /v1/_debug/ui")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_ui)

    p = sub.add_parser("move")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.set_defaults(fn=lambda a: (move_to(a.x, a.y), print(f"pointer at {pointer_position()}"))[1] or 0)

    p = sub.add_parser("clickxy")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--right", action="store_true")
    p.set_defaults(fn=lambda a: (click_at(a.x, a.y, a.right), print(f"clicked at {a.x},{a.y}"))[1] or 0)

    p = sub.add_parser("drag")
    for n in ("x1", "y1", "x2", "y2"):
        p.add_argument(n, type=int)
    p.set_defaults(fn=lambda a: (drag(a.x1, a.y1, a.x2, a.y2), print(f"dragged {a.x1},{a.y1} -> {a.x2},{a.y2}"))[1] or 0)

    p = sub.add_parser("hover", help="move the pointer onto a card (or 'icon')")
    p.add_argument("target")
    p.set_defaults(fn=lambda a: (move_to(*resolve_target(fetch_ui(), a.target)), print(f"hovering {a.target} at {pointer_position()}"))[1] or 0)

    p = sub.add_parser("click", help="click a card, a named card button, or the icon")
    p.add_argument("target")
    p.add_argument("button", nargs="?")
    p.add_argument("--right", action="store_true")

    def _click(a):
        x, y = resolve_target(fetch_ui(), a.target, a.button)
        click_at(x, y, a.right)
        print(f"clicked {a.target} {a.button or ''} at {x},{y}")
        return 0

    p.set_defaults(fn=_click)

    sub.add_parser("hotkey", help="press the configured global hotkey").set_defaults(fn=cmd_hotkey)

    p = sub.add_parser("type")
    p.add_argument("text")
    p.set_defaults(fn=lambda a: (type_text(a.text), print(f"typed {len(a.text)} chars"))[1] or 0)

    p = sub.add_parser("key", help="press a key or chord, e.g. key enter, key ctrl+f")
    p.add_argument("name")
    p.set_defaults(fn=lambda a: (press_keys(a.name.split("+")), print(f"pressed {a.name}"))[1] or 0)

    p = sub.add_parser("screenshot")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_screenshot)

    p = sub.add_parser("fullpage")
    p.add_argument("--query", required=True)
    p.add_argument("--screenshot", required=True)
    p.add_argument("--wait", type=float, default=4.0)
    p.add_argument("--via-api", action="store_true", help="let the app open the window (needs a launch nonce)")
    p.set_defaults(fn=cmd_fullpage)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
