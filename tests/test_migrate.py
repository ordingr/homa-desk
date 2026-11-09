"""One-time move of %USERPROFILE%\\.assistant to .homa (rename plan 4.5), in-process on a fake profile.

Everything is redirected: USERPROFILE/HOME, LOCALAPPDATA and the Desktop/Startup folders (HOMA_MIGRATE_SHORTCUT_DIRS), so
no test can reach the real home folders, Edge profile or shortcuts. Shortcut tests write real .lnk files through
WScript.Shell into temp folders (no window is shown)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from homa import migrate, netinfo, paths, update

from conftest import REPO_ROOT, _free_port

INSTALL_ROOT = str(update.install_root())


class Env:
    def __init__(self, base: Path):
        self.user = base / "user"
        self.local = base / "local"
        self.desktop = base / "Desktop"
        self.startup = base / "Startup"
        for folder in (self.user, self.local, self.desktop, self.startup):
            folder.mkdir()
        self.old = self.user / ".assistant"
        self.new = self.user / ".homa"
        self.old_edge = self.local / "Assistant"
        self.new_edge = self.local / "Homa"

    def make_old(self, pid_line: str = "999999\n17171\n"):
        self.old.mkdir()
        (self.old / "token").write_text("tok", encoding="ascii")
        (self.old / "settings.json").write_text(json.dumps({"schema": 1, "port": 17171}), encoding="utf-8")
        (self.old / "assistant.pid").write_text(pid_line, encoding="ascii")
        (self.old / "hooks-install.json").write_text("{}", encoding="utf-8")
        (self.old / "index").mkdir()
        (self.old / "index" / "files.idx").write_bytes(b"\x00" * 64)
        (self.old / "logs").mkdir()
        (self.old / "logs" / "app.log").write_text("log", encoding="utf-8")

    def make_edge(self):
        (self.old_edge / "edge-profile").mkdir(parents=True)
        (self.old_edge / "edge-profile" / "x").write_text("profile", encoding="utf-8")

    def listing(self, root: Path):
        return sorted(str(p.relative_to(root)) for p in root.rglob("*"))

    def child_env(self) -> dict:
        drive, rest = os.path.splitdrive(str(self.user))
        child = dict(os.environ, USERPROFILE=str(self.user), HOME=str(self.user), LOCALAPPDATA=str(self.local),
                     HOMEDRIVE=drive, HOMEPATH=rest)
        child[migrate.SHORTCUT_DIRS_ENV] = os.pathsep.join((str(self.desktop), str(self.startup)))
        child.pop("HOMA_HOME", None)
        child.pop("ASSISTANT_HOME", None)
        return child


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path)
    drive, rest = os.path.splitdrive(str(e.user))
    monkeypatch.setenv("USERPROFILE", str(e.user))
    monkeypatch.setenv("HOME", str(e.user))
    monkeypatch.setenv("HOMEDRIVE", drive)
    monkeypatch.setenv("HOMEPATH", rest)
    monkeypatch.setenv("LOCALAPPDATA", str(e.local))
    monkeypatch.setenv(migrate.SHORTCUT_DIRS_ENV, os.pathsep.join((str(e.desktop), str(e.startup))))
    monkeypatch.delenv("HOMA_HOME", raising=False)
    monkeypatch.delenv("ASSISTANT_HOME", raising=False)
    assert Path.home() == e.user and migrate.old_home() == e.old
    return e


def make_lnk(path: Path, args: str, workdir: str):
    import win32com.client
    link = win32com.client.Dispatch("WScript.Shell").CreateShortCut(str(path))
    link.TargetPath = sys.executable
    link.Arguments = args
    link.WorkingDirectory = workdir
    link.IconLocation = str(Path(workdir) / "assistant" / "assets" / "assistant.ico") + ",0"
    link.Description = "Assistant"
    link.Save()


def read_lnk(path: Path):
    import win32com.client
    link = win32com.client.Dispatch("WScript.Shell").CreateShortCut(str(path))
    return link.TargetPath, link.Arguments, link.WorkingDirectory, link.IconLocation, link.Description


def test_happy_path_moves_everything(env):
    env.make_old()
    env.make_edge()
    make_lnk(env.desktop / "Assistant.lnk", "-m assistant", INSTALL_ROOT)
    make_lnk(env.startup / "Assistant.lnk", "-m assistant", INSTALL_ROOT)
    before = {p for p in env.listing(env.old) if p != "assistant.pid"}

    assert migrate.run_once() is None

    assert not env.old.exists()
    after = set(env.listing(env.new))
    assert before <= after
    assert "assistant.pid" not in after
    assert after - before == {"migrated-from.json", "migration-notice.json"}
    assert (env.new / "token").read_text() == "tok"
    assert (env.new_edge / "edge-profile" / "x").read_text() == "profile" and not env.old_edge.exists()
    for folder in (env.desktop, env.startup):
        assert not (folder / "Assistant.lnk").exists()
        target, args, workdir, icon, desc = read_lnk(folder / "Homa.lnk")
        assert args == "-m homa" and os.path.normcase(workdir) == os.path.normcase(INSTALL_ROOT)
        assert icon.lower().endswith(os.path.join("homa", "assets", "homa.ico,0").lower()) and desc == "Homa"
        assert os.path.normcase(target) == os.path.normcase(sys.executable)
    marker = json.loads((env.new / "migrated-from.json").read_text(encoding="utf-8"))
    assert marker["from"] == str(env.old) and marker["edge_profile"] == "moved" and len(marker["shortcuts"]) == 2
    assert marker["homa_version"] and marker["when"]
    assert paths.home() == env.new


def test_migration_notice_is_a_one_time_card(env):
    env.make_old()
    assert migrate.run_once() is None
    first = migrate.consume_notice()
    assert first["title"] == "Assistant is now Homa" and "Shortcuts: 0 updated" in first["detail"]
    assert migrate.consume_notice() is None


def test_idempotent_second_run_changes_nothing(env):
    env.make_old()
    env.make_edge()
    assert migrate.run_once() is None
    snapshot = env.listing(env.user) + env.listing(env.local)
    marker_bytes = (env.new / "migrated-from.json").read_bytes()
    assert migrate.run_once() is None
    assert env.listing(env.user) + env.listing(env.local) == snapshot
    assert (env.new / "migrated-from.json").read_bytes() == marker_bytes


def test_both_folders_without_marker_moves_nothing_and_says_so(env):
    env.make_old()
    env.new.mkdir()
    (env.new / "settings.json").write_text("{}", encoding="utf-8")
    old_before, new_before = env.listing(env.old), env.listing(env.new)

    assert migrate.run_once() is None

    assert env.listing(env.old) == old_before
    assert env.listing(env.new) == sorted(new_before + ["migration-notice.json"])
    assert not (env.new / "migrated-from.json").exists()
    line, old_line = migrate.status()
    assert line == "not done: both folders exist" and "were not merged" in old_line
    notice = migrate.consume_notice()
    assert notice["title"] == "Assistant data folder was not merged" and "Homa uses .homa" in notice["detail"]


def test_both_folders_with_marker_reports_the_old_folder_size(env):
    env.make_old()
    env.new.mkdir()
    (env.new / "migrated-from.json").write_text("{}", encoding="utf-8")
    assert migrate.run_once() is None
    assert env.old.exists()
    line, old_line = migrate.status()
    assert line == "done" and "still present" in old_line and "Homa does not read it" in old_line
    assert not (env.new / "migration-notice.json").exists()


def test_status_none_pending_and_done(env):
    assert migrate.status() == ("none needed", None)
    env.make_old()
    assert migrate.status()[0].startswith("pending")
    migrate.run_once()
    line, extra = migrate.status()
    assert line.startswith("done: moved from") and extra is None


@pytest.mark.parametrize("var", ["HOMA_HOME", "ASSISTANT_HOME"])
def test_explicit_home_never_migrates(env, monkeypatch, tmp_path, var):
    env.make_old()
    make_lnk(env.desktop / "Assistant.lnk", "-m assistant", INSTALL_ROOT)
    monkeypatch.setenv(var, str(tmp_path / "explicit"))
    before = env.listing(env.user) + env.listing(env.desktop)
    assert migrate.run_once() is None
    assert env.listing(env.user) + env.listing(env.desktop) == before
    assert migrate.status()[0].startswith("not applicable")


def test_rename_permission_error_moves_nothing_and_writes_no_marker(env, monkeypatch, capsys):
    env.make_old()
    env.make_edge()
    make_lnk(env.desktop / "Assistant.lnk", "-m assistant", INSTALL_ROOT)
    before = env.listing(env.old)
    real_rename = os.rename

    def refuse(src, dst):
        if Path(src) == env.old:
            raise PermissionError(5, "Access is denied")
        return real_rename(src, dst)

    monkeypatch.setattr(migrate.os, "rename", refuse)
    assert migrate.run_once() == 1
    err = capsys.readouterr().err
    assert "could not move" in err and "PermissionError" in err and "Nothing was changed" in err and str(env.old) in err
    assert env.listing(env.old) == before and not env.new.exists()
    assert env.old_edge.exists() and not env.new_edge.exists()
    assert (env.desktop / "Assistant.lnk").is_file() and not (env.desktop / "Homa.lnk").exists()
    monkeypatch.setattr(migrate.os, "rename", real_rename)
    assert migrate.run_once() is None and env.new.is_dir() and not env.old.exists()


def test_shortcut_for_a_different_install_is_left_alone(env, tmp_path):
    env.make_old()
    other = tmp_path / "other-install"
    other.mkdir()
    make_lnk(env.desktop / "Assistant.lnk", "-m assistant", str(other))
    make_lnk(env.startup / "Assistant.lnk", "-m somethingelse", INSTALL_ROOT)
    assert migrate.run_once() is None
    for folder in (env.desktop, env.startup):
        assert (folder / "Assistant.lnk").is_file() and not (folder / "Homa.lnk").exists()
    assert json.loads((env.new / "migrated-from.json").read_text())["shortcuts"] == []


def test_edge_profile_is_kept_when_the_destination_exists(env):
    env.make_old()
    env.make_edge()
    (env.new_edge / "edge-profile").mkdir(parents=True)
    assert migrate.run_once() is None
    marker = json.loads((env.new / "migrated-from.json").read_text())
    assert marker["edge_profile"].startswith("skipped") and (env.old_edge / "edge-profile" / "x").is_file()
    assert "kept at old location" in migrate.consume_notice()["detail"]


def test_edge_rename_failure_is_recorded_not_fatal(env, monkeypatch):
    env.make_old()
    env.make_edge()
    real_rename = os.rename

    def flaky(src, dst):
        if Path(src) == env.old_edge / "edge-profile":
            raise PermissionError(5, "locked")
        return real_rename(src, dst)

    monkeypatch.setattr(migrate.os, "rename", flaky)
    assert migrate.run_once() is None
    assert json.loads((env.new / "migrated-from.json").read_text())["edge_profile"] == "skipped: PermissionError"
    assert (env.old_edge / "edge-profile" / "x").is_file() and env.new.is_dir()


# ---- a running old instance --------------------------------------------------------------------------------------

def test_stuck_old_instance_stops_the_migration(env, monkeypatch, capsys):
    env.make_old("4242\n17999\n")
    monkeypatch.setattr(migrate, "STOP_TIMEOUT_S", 0.6)
    monkeypatch.setattr(netinfo, "process_image", lambda pid: r"C:\venv\Scripts\pythonw.exe")
    monkeypatch.setattr(netinfo, "listener_pid", lambda port: 4242)
    posted = []
    monkeypatch.setattr(migrate, "_request_shutdown", lambda old, port: posted.append(port))
    before = env.listing(env.old)
    t0 = time.monotonic()

    assert migrate.run_once() == 1

    assert time.monotonic() - t0 >= 0.5, "it must wait out the timeout"
    assert posted == [17999]
    err = capsys.readouterr().err
    assert "could not stop the running Assistant (pid 4242)" in err
    assert env.listing(env.old) == before and not env.new.exists()


def test_old_instance_that_stops_lets_the_migration_proceed(env, monkeypatch):
    env.make_old("4242\n17999\n")
    state = {"alive": True}
    monkeypatch.setattr(netinfo, "process_image", lambda pid: r"C:\v\pythonw.exe" if state["alive"] else None)
    monkeypatch.setattr(netinfo, "listener_pid", lambda port: 4242 if state["alive"] else None)
    monkeypatch.setattr(migrate, "_request_shutdown", lambda old, port: state.update(alive=False))
    assert migrate.run_once() is None
    assert env.new.is_dir() and not env.old.exists()


def test_non_python_listener_on_the_recorded_port_is_not_touched(env, monkeypatch):
    env.make_old("4242\n17999\n")
    monkeypatch.setattr(netinfo, "process_image", lambda pid: r"C:\Windows\notepad.exe")
    monkeypatch.setattr(migrate, "_request_shutdown", lambda *a: pytest.fail("must not post"))
    assert migrate.run_once() is None and env.new.is_dir()


def test_migrate_builds_its_own_opener_and_never_the_default_urlopen():
    src = (REPO_ROOT / "homa" / "migrate.py").read_text(encoding="utf-8")
    assert "urlopen" not in src and "ProxyHandler({})" in src
    assert "_NoRedirect" in src.split("_opener =")[1].splitlines()[0]
    send_src = (REPO_ROOT / "homa" / "send.py").read_text(encoding="utf-8")
    assert "urlopen" not in send_src


def test_real_old_instance_on_a_scratch_port_is_shut_down_then_migrated(env, tmp_path):
    """A real scratch app (not port 17171) plays the old instance; its pid file is copied to assistant.pid, as the
    pre-rename version wrote it. Its console goes outside the folder: an open handle inside would block the rename."""
    port = _free_port()
    assert port != 17171
    env.old.mkdir()
    (env.old / "settings.json").write_text(json.dumps({"schema": 1, "port": port}), encoding="utf-8")
    child = env.child_env()
    child["HOMA_HOME"] = str(env.old)
    console = open(tmp_path / "old-console.txt", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "homa"], cwd=REPO_ROOT, env=child, stdout=console,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (env.old / "homa.pid").is_file():
            assert proc.poll() is None, (tmp_path / "old-console.txt").read_text(errors="replace")
            time.sleep(0.2)
        pid_text = (env.old / "homa.pid").read_text(encoding="ascii")
        pid = int(pid_text.split()[0])
        (env.old / "assistant.pid").write_text(pid_text, encoding="ascii")
        deadline = time.monotonic() + 30
        while netinfo.listener_pid(port) != pid and time.monotonic() < deadline:
            time.sleep(0.2)
        assert netinfo.listener_pid(port) == pid
        token = (env.old / "token").read_text(encoding="ascii").strip()
        env.make_edge()
        assert migrate.run_once() is None
        assert netinfo.listener_pid(port) is None, "the old instance must be gone"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        console.close()
    assert env.new.is_dir() and not env.old.exists()
    assert (env.new / "token").read_text(encoding="ascii").strip() == token
    assert not (env.new / "assistant.pid").exists()
    assert json.loads((env.new / "settings.json").read_text())["port"] == port


def test_cli_init_migrates_in_a_subprocess_and_a_second_run_is_a_noop(env):
    env.make_old()
    env.make_edge()
    make_lnk(env.desktop / "Assistant.lnk", "-m assistant", INSTALL_ROOT)
    child = env.child_env()
    cmd = [sys.executable, "-m", "homa", "--init"]
    first = subprocess.run(cmd, cwd=REPO_ROOT, env=child, capture_output=True, text=True, timeout=60)
    assert first.returncode == 0, first.stderr
    assert (env.new / "migrated-from.json").is_file() and not env.old.exists()
    assert (env.desktop / "Homa.lnk").is_file() and (env.new_edge / "edge-profile" / "x").is_file()
    snapshot = env.listing(env.user) + env.listing(env.local) + env.listing(env.desktop)
    second = subprocess.run(cmd, cwd=REPO_ROOT, env=child, capture_output=True, text=True, timeout=60)
    assert second.returncode == 0, second.stderr
    assert env.listing(env.user) + env.listing(env.local) + env.listing(env.desktop) == snapshot
    doctor = subprocess.run([sys.executable, "-m", "homa", "--doctor"], cwd=REPO_ROOT, env=child,
                            capture_output=True, text=True, timeout=120)
    assert doctor.returncode == 0 and "done: moved from" in doctor.stdout


# ---- review 14 regressions (R14-N1, N2, N3, N5, N6) ---------------------------------------------------------------

import http.server
import threading


class _Sink(http.server.BaseHTTPRequestHandler):
    """Records every request; with ``redirect_to`` set it answers a POST with a 302 to that port."""

    def do_POST(self):
        self.server.hits.append(("POST", self.path, self.headers.get("Authorization")))
        if self.server.redirect_to:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.redirect_to}/stolen")
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.server.hits.append(("GET", self.path, self.headers.get("Authorization")))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def sinks():
    made = []

    def make(redirect_to=None):
        server = http.server.HTTPServer(("127.0.0.1", 0), _Sink)
        server.hits, server.redirect_to = [], redirect_to
        threading.Thread(target=server.serve_forever, daemon=True).start()
        made.append(server)
        return server

    yield make
    for server in made:
        server.shutdown()
        server.server_close()


def test_shutdown_request_does_not_follow_a_redirect_or_forward_the_bearer(env, sinks):
    other = sinks()
    first = sinks(redirect_to=other.server_port)
    env.old.mkdir()
    (env.old / "token").write_text("SECRETTOKEN", encoding="ascii")
    migrate._request_shutdown(env.old, first.server_port)
    assert first.hits == [("POST", "/v1/shutdown", "Bearer SECRETTOKEN")]
    assert other.hits == [], "the bearer must never leave the port it was aimed at"


def test_shutdown_request_with_an_undecodable_token_file_neither_raises_nor_sends(env, sinks):
    sink = sinks()
    env.old.mkdir()
    (env.old / "token").write_bytes(b"\xff\xfe\x00")
    migrate._request_shutdown(env.old, sink.server_port)
    assert sink.hits == []


def test_shutdown_request_with_a_crlf_token_is_refused_not_injected(env, sinks):
    sink = sinks()
    env.old.mkdir()
    (env.old / "token").write_bytes(b"abc\r\nX-Injected: 1")
    migrate._request_shutdown(env.old, sink.server_port)
    assert all("X-Injected" not in str(hit) for hit in sink.hits)


@pytest.mark.parametrize("text", ["[1]", '"x"', "null", "5"])
def test_status_survives_a_marker_that_is_not_an_object(env, text):
    env.new.mkdir()
    (env.new / migrate.MARKER_NAME).write_text(text, encoding="utf-8")
    assert migrate.status() == ("done (marker unreadable)", None)


def test_status_does_not_interpolate_non_text_marker_fields(env):
    env.new.mkdir()
    (env.new / migrate.MARKER_NAME).write_text(json.dumps({"from": {"a": 1}, "when": [1]}), encoding="utf-8")
    line = migrate.status()[0]
    assert line == "done: moved from the old folder on an unknown date", line


def test_foreign_dot_assistant_is_not_moved_stopped_or_noticed(env, monkeypatch):
    env.old.mkdir()
    (env.old / "foreign-tool.cfg").write_text("x", encoding="utf-8")
    monkeypatch.setattr(migrate, "_stop_old_instance", lambda old: pytest.fail("must not look for an instance"))
    assert migrate.run_once() is None
    assert (env.old / "foreign-tool.cfg").is_file() and not env.new.exists()
    assert migrate.status()[0].startswith("not applicable")


def test_foreign_dot_assistant_next_to_dot_homa_writes_no_notice(env):
    env.old.mkdir()
    (env.old / "foreign-tool.cfg").write_text("x", encoding="utf-8")
    env.new.mkdir()
    assert migrate.run_once() is None
    assert not (env.new / migrate.NOTICE_NAME).exists() and migrate.consume_notice() is None


def test_edge_move_takes_only_the_edge_profile_and_leaves_the_rest_of_assistant(env):
    (env.old_edge / "other-app").mkdir(parents=True)
    (env.old_edge / "other-app" / "data.bin").write_text("x", encoding="utf-8")
    env.make_edge()
    assert migrate._move_edge_profile() == "moved"
    assert (env.new_edge / "edge-profile" / "x").is_file()
    assert (env.old_edge / "other-app" / "data.bin").is_file() and not (env.old_edge / "edge-profile").exists()
    assert not (env.new_edge / "other-app").exists()


def test_edge_move_removes_the_old_folder_only_when_it_is_empty(env):
    env.make_edge()
    assert migrate._move_edge_profile() == "moved"
    assert env.new_edge.is_dir() and not env.old_edge.exists()


def test_edge_move_ignores_a_foreign_assistant_folder_without_a_profile(env):
    (env.old_edge / "other-app").mkdir(parents=True)
    (env.old_edge / "other-app" / "data.bin").write_text("x", encoding="utf-8")
    before = env.listing(env.local)
    assert migrate._move_edge_profile() == "absent"
    assert env.listing(env.local) == before and not env.new_edge.exists()


@pytest.mark.parametrize("args, rewritten", [
    ("-m assistant.mcp_stdio", None),
    ("-m assistantx", None),
    ("-m assistant", "-m homa"),
    ("-m assistant --some-flag", "-m homa --some-flag"),
])
def test_shortcut_rewrite_matches_the_module_exactly_and_keeps_extra_arguments(env, args, rewritten):
    make_lnk(env.desktop / "Assistant.lnk", args, INSTALL_ROOT)
    done = migrate._rewrite_shortcuts()
    if rewritten is None:
        assert done == [] and (env.desktop / "Assistant.lnk").is_file() and not (env.desktop / "Homa.lnk").exists()
    else:
        assert len(done) == 1 and not (env.desktop / "Assistant.lnk").exists()
        assert read_lnk(env.desktop / "Homa.lnk")[1] == rewritten
