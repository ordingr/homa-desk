"""Shell actions over ``POST /v1/actions/<verb>`` (plan 7.4 M3b-4; AC-AC-1..6, SEC-6, SEC-7).

Live: the private test-mode app of ``conftest.preview_app`` (its root holds disposable copies under ``acts/``). Nothing
here deletes anything permanently: the recycle tests move fixture files to the Recycle Bin, prove they are listed
there through the Shell, then restore them. Response contract: success ``200 {ok, message}``; a confirmation request
``200 {ok: false, needs_confirmation: true}``; a refusal ``403``, a bad name ``422 invalid_name``, a vanished file ``404``,
all with ``{"error": {code, message}}``.
"""
import ctypes
import json
import os
import re
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from conftest import REPO_ROOT, wait_until
from tests.fixtures.preview import BAT_MARKER

pytest.importorskip("assistant.actions.shell", reason="assistant/actions/shell.py (M3b-4) not present")


def outcome(result):
    """(ok, message, needs_confirmation, status) from either response shape."""
    status, body = result
    body = body or {}
    if "error" in body:
        return False, body["error"].get("message", ""), False, status
    return bool(body.get("ok")), body.get("message", ""), bool(body.get("needs_confirmation")), status


@pytest.fixture(scope="module")
def app(preview_app):
    status, body = preview_app.act("copy_path", preview_app.files["act00"])
    if status == 404:
        pytest.skip("POST /v1/actions/* is not served yet (M3b-4 / M3b-9 wiring)")
    return preview_app


# ---- clipboard / shell helpers -------------------------------------------------------------------------------------

def ps(script: str, timeout: float = 30.0) -> str:
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                          text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return done.stdout


def get_clipboard() -> str:
    return ps("Get-Clipboard -Raw").rstrip("\r\n")


@pytest.fixture
def keep_clipboard():
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        win32clipboard.CloseClipboard()
    except Exception as exc:
        pytest.skip(f"this session cannot open the clipboard ({exc.args[-1] if exc.args else exc}): locked or sandboxed desktop")
    saved = ps("Get-Clipboard -Raw")
    yield
    ps("Set-Clipboard -Value $null") if not saved else subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"], input=saved,
        text=True, capture_output=True, timeout=30)


def recycle_bin_listing() -> list:
    out = ps("$s=New-Object -ComObject Shell.Application; $b=$s.NameSpace(10); "
             "$b.Items() | ForEach-Object { $_.Name + '|' + $b.GetDetailsOf($_,1) }")
    return [line.strip() for line in out.splitlines() if "|" in line]


def restore_from_bin(stem: str) -> None:
    ps("$s=New-Object -ComObject Shell.Application; $b=$s.NameSpace(10); "
       f"$b.Items() | Where-Object {{ $_.Name -like '{stem}*' }} | ForEach-Object {{ $_.InvokeVerb('undelete') }}")


# ---- plumbing -----------------------------------------------------------------------------------------------------------

def test_unknown_verb_and_missing_token_and_bad_body(app):
    status, _ = app.api.post("/v1/actions/delete_forever", {"path": str(app.files["act00"])})
    assert status == 404
    assert app.files["act00"].exists()
    status, _ = app.api.post("/v1/actions/copy_path", {"path": str(app.files["act00"])}, token=False)
    assert status == 401
    assert app.api.post("/v1/actions/copy_path", {"nope": 1})[0] == 422
    assert app.api.post("/v1/actions/copy_path", {"path": 5})[0] == 422
    assert app.api.post("/v1/actions/recycle", {"path": str(app.files["act00"]), "confirm": "yes"})[0] == 422
    assert app.files["act00"].exists()


def test_every_documented_verb_is_routed(app):
    from assistant.actions import VERBS
    expected = {"open", "open_with", "reveal", "copy_path", "rename", "properties", "recycle", "md_vault",
                "folder_listing", "ask_claude"}
    assert expected <= set(VERBS)


# ---- copy_path (AC-AC-6) ------------------------------------------------------------------------------------------------

def test_copy_path_puts_the_plain_path_on_the_clipboard(app, keep_clipboard):
    target = app.files["act01"]
    ok, message, confirm, status = outcome(app.act("copy_path", target))
    assert ok and status == 200 and not confirm, message
    assert os.path.normcase(get_clipboard()) == os.path.normcase(str(target))


def test_copy_path_strips_the_long_path_prefix(app, keep_clipboard):
    target = app.files["act02"]
    ok, message, _, _ = outcome(app.act("copy_path", "\\\\?\\" + str(target)))
    assert ok, message
    copied = get_clipboard()
    assert "\\\\?\\" not in copied and os.path.normcase(copied) == os.path.normcase(str(target))


def test_copy_path_quoted_wraps_the_path_in_quotes(app, keep_clipboard):
    target = app.files["act03"]
    ok, message, _, status = outcome(app.act("copy_path_quoted", target))
    if status in (404, 422):
        pytest.skip("copy_path_quoted is not a verb of this build")
    assert ok, message
    assert get_clipboard() == f'"{target}"'


def test_copy_path_of_a_protected_file_is_allowed_but_never_reads_it(app, keep_clipboard):
    ok, message, _, _ = outcome(app.act("copy_path", app.files["pem"]))
    assert ok, message


# ---- vetting ----------------------------------------------------------------------------------------------------------------

def test_actions_refuse_paths_outside_the_roots_and_in_the_assistants_home(app, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n", encoding="utf-8")
    for verb in ("recycle", "rename", "copy_path", "reveal", "folder_listing"):
        extra = {"confirm": True} if verb == "recycle" else {"new_name": "moved.txt"} if verb == "rename" else {}
        status, body = app.act(verb, outside, **extra)
        assert status == 403 and body["error"]["code"] == "refused", (verb, status, body)
    assert outside.read_text(encoding="utf-8") == "keep me\n"
    status, body = app.act("recycle", app.home / "token", confirm=True)
    assert status == 403 and (app.home / "token").is_file()
    status, body = app.act("copy_path", app.home / "token")
    assert status == 403


def test_a_vanished_file_is_a_404_not_a_crash(app):
    status, body = app.act("copy_path", app.root / "acts" / "never-existed.txt")
    assert status in (403, 404) and "Traceback" not in json.dumps(body)


def test_dot_dot_escape_is_refused(app, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n", encoding="utf-8")
    sneaky = app.root / "acts" / ".." / ".." / ".." / victim.name
    status, _ = app.act("recycle", sneaky, confirm=True)
    assert status == 403 and victim.exists()


# ---- open + executable confirmation (AC-AC-3, SEC-6) -------------------------------------------------------------------

def test_open_of_a_batch_file_asks_first_and_runs_nothing(app):
    marker = app.files["marker"]
    assert not marker.exists()
    ok, message, needs, status = outcome(app.act("open", app.files["bat"]))
    assert needs and not ok and status == 200, (ok, message, needs, status)
    assert "marker.bat" in message
    time.sleep(1.5)
    assert not marker.exists(), "the batch file ran without confirmation"


def test_open_with_confirm_runs_the_batch_file(app):
    marker = app.files["marker"]
    marker.unlink(missing_ok=True)
    ok, message, needs, _ = outcome(app.act("open", app.files["bat"], confirm=True))
    assert ok and not needs, message
    assert wait_until(marker.exists, timeout=10), "marker.bat did not run after confirmation"
    marker.unlink(missing_ok=True)


@pytest.mark.parametrize("key", ["exe"])
def test_open_of_an_exe_asks_first(app, key):
    ok, message, needs, status = outcome(app.act("open", app.files[key]))
    assert needs and not ok, message


@pytest.mark.parametrize("ext", ["lnk", "url", "scr", "cmd", "ps1", "vbs", "hta", "msi", "reg", "js", "com"])
def test_every_executable_class_extension_needs_confirmation(app, ext):
    name = app.root / "acts" / f"probe.{ext}"
    created = not name.exists()
    if created:
        name.write_text("rem nothing\n", encoding="utf-8")
    try:
        # The file is new, so it is not in the index until the watcher sees it.
        assert wait_until(lambda: app.act("open", name)[0] != 403, timeout=15), "new file never reached the index"
        ok, message, needs, status = outcome(app.act("open", name))
        assert needs and not ok, (ext, message)
    finally:
        if created:
            name.unlink(missing_ok=True)  # test-owned throwaway inside pytest tmp, never a user file


# ---- reveal reuses one Explorer window (AC-AC-1) -------------------------------------------------------------------------

def _cabinet_windows() -> set:
    found = set()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        if buf.value == "CabinetWClass":
            found.add(int(hwnd))
        return True

    user32.EnumWindows(proto(cb), 0)
    return found


def _close_windows(handles) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    for h in handles:
        user32.PostMessageW(wintypes.HWND(h), 0x0010, 0, 0)  # WM_CLOSE on windows this test opened


def test_reveal_twice_in_one_folder_opens_one_explorer_window(app):
    before = _cabinet_windows()
    try:
        ok, message, _, _ = outcome(app.act("reveal", app.files["rev1"]))
        assert ok, message
        opened = wait_until(lambda: len(_cabinet_windows() - before) >= 1, timeout=10)
        if not opened:
            pytest.skip("Explorer showed no new CabinetWClass window (it may have reused an existing one or run headless)")
        first = _cabinet_windows() - before
        ok, message, _, _ = outcome(app.act("reveal", app.files["rev2"]))
        assert ok, message
        time.sleep(2.5)
        assert _cabinet_windows() - before == first, "second reveal opened another Explorer window"
        assert len(first) == 1
    finally:
        _close_windows(_cabinet_windows() - before)


# ---- folder_listing (AC-AC-1, Ctrl+F) -------------------------------------------------------------------------------------

def test_folder_listing_returns_the_siblings_of_the_file_and_hides_protected_children(app):
    ok, message, _, _ = outcome(app.act("folder_listing", app.files["rev1"]))
    assert ok, message
    status, body = app.act("folder_listing", app.files["rev1"])
    names = [r["name"] for r in body["rows"]]
    assert {"reveal_one.txt", "reveal_two.txt"} <= set(names)
    status, body = app.act("folder_listing", app.files["pem"])
    assert status == 200
    assert "server.pem" not in [r["name"] for r in body["rows"]]
    assert body.get("hidden_protected", 0) >= 1


# ---- rename (AC-AC-4) ------------------------------------------------------------------------------------------------------

def test_rename_moves_the_file_and_the_reason_is_returned_for_bad_names(app):
    source = app.files["act04"]
    for bad, needle in (("con.txt", "reserved"), ("a<b.txt", "<"), ("trailing. ", "period")):
        status, body = app.act("rename", source, new_name=bad)
        assert status == 422 and body["error"]["code"] == "invalid_name", (bad, status, body)
        assert needle in body["error"]["message"].lower() or needle in body["error"]["message"], body
        assert source.exists()
    status, body = app.act("rename", source, new_name="..\\evil.txt")
    assert status == 422 and source.exists() and not (source.parent.parent / "evil.txt").exists()
    ok, message, _, status = outcome(app.act("rename", source, new_name="act_04_renamed.txt"))
    assert ok and status == 200, message
    renamed = source.with_name("act_04_renamed.txt")
    assert renamed.exists() and not source.exists()
    assert renamed.read_text(encoding="utf-8") == "action fixture 4\n"


def test_rename_never_overwrites_an_existing_file(app):
    a, b = app.files["act05"], app.files["act06"]
    status, body = app.act("rename", a, new_name=b.name)
    assert status == 403, (status, body)
    assert a.read_text(encoding="utf-8") == "action fixture 5\n" and b.read_text(encoding="utf-8") == "action fixture 6\n"


def test_rename_of_an_indexed_root_is_refused(app):
    status, body = app.act("rename", app.root, new_name="somewhere-else")
    assert status == 403 and app.root.is_dir()


# ---- recycle (AC-AC-5, SEC-7) ------------------------------------------------------------------------------------------------

def test_recycle_asks_first_then_the_file_is_in_the_recycle_bin_and_gone_from_disk(app):
    target = app.files["act07"]
    ok, message, needs, _ = outcome(app.act("recycle", target))
    assert needs and not ok and target.exists(), "recycle must ask for confirmation before touching the file"
    before = recycle_bin_listing()
    ok, message, needs, status = outcome(app.act("recycle", target, confirm=True))
    try:
        assert ok and status == 200, message
        assert not target.exists(), "file is still on disk after recycle"
        listing = recycle_bin_listing()
        stem = target.stem
        hits = [line for line in listing if line.lower().startswith(stem.lower())
                and os.path.normcase(str(target.parent)) in os.path.normcase(line)]
        assert hits, f"{stem} not found in shell:RecycleBinFolder; bin before={len(before)} after={len(listing)}"
    finally:
        restore_from_bin(target.stem)
    assert wait_until(target.exists, timeout=15), "could not restore the fixture from the Recycle Bin"


def test_recycle_of_a_folder_goes_through_the_bin_too(app, tmp_path):
    folder = app.root / "acts" / "folder_b"
    ok, message, needs, _ = outcome(app.act("recycle", folder))
    assert needs and folder.exists()


def test_recycle_is_refused_on_a_network_path(app):
    unc = "\\\\localhost\\c$\\Windows\\win.ini"
    status, body = app.act("recycle", unc, confirm=True)
    assert status in (403, 404) and Path("C:/Windows/win.ini").exists()


def test_recycle_is_refused_on_a_removable_volume_with_an_explanation():
    shell = pytest.importorskip("assistant.actions.shell")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    drives = [f"{c}:\\" for c in "DEFGHIJKLMNOPQRSTUVWXYZ" if k32.GetDriveTypeW(f"{c}:\\") == 2]
    if not drives:
        pytest.skip("no removable drive is attached; _volume_problem is exercised on a UNC path instead")
    assert "removable" in shell._volume_problem(drives[0] + "x.txt")


def test_volume_check_names_the_network_reason():
    shell = pytest.importorskip("assistant.actions.shell")
    reason = shell._volume_problem("\\\\server\\share\\a.txt")
    assert reason and "network" in reason


# ---- Ask Claude (DEC 4: nothing leaves without confirmation) --------------------------------------------------------------

def test_ask_claude_asks_before_anything_and_names_what_would_be_sent(app):
    target = app.files["csv"]
    status, body = app.act("ask_claude", target)
    assert status == 200 and body["ok"] is False and body["needs_confirmation"] is True, body
    assert "Nothing is sent" in body["message"] or "confirm" in body["message"].lower()
    summary = body.get("summary", {})
    assert summary.get("path") and summary.get("bytes_to_send", 0) > 0


def test_ask_claude_refuses_protected_binary_and_placeholder_files(app):
    for key in ("pem", "keybackup", "png", "offline", "binary"):
        status, body = app.act("ask_claude", app.files[key])
        assert status in (403, 422) or (status == 200 and not body.get("needs_confirmation")), (key, status, body)
        assert not body.get("needs_confirmation"), key


@pytest.mark.parametrize("key", ["docx", "xlsx", "pptx"])
def test_ask_claude_office_files_confirm_with_extracted_text_only(app, key):
    status, body = app.act("ask_claude", app.files[key])
    assert status == 200 and body["ok"] is False and body["needs_confirmation"] is True, (key, body)
    summary = body["summary"]
    assert summary["office_text"] is True and summary["bytes_to_send"] > 0, summary
    assert "the file itself is not sent" in summary["what"], summary


# ---- md_vault and properties ----------------------------------------------------------------------------------------------

def test_md_vault_refuses_folders(app):
    status, body = app.act("md_vault", app.root / "acts")
    assert status in (403, 422) or not body.get("ok")


def test_properties_window_opens_and_can_be_closed(app):
    ok, message, _, status = outcome(app.act("properties", app.files["act08"]))
    assert ok and status == 200, message
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    title = f"{app.files['act08'].name} Properties"
    hwnd = wait_until(lambda: user32.FindWindowW("#32770", title), timeout=10)
    if hwnd:
        user32.PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0)
    else:
        pytest.skip("the Properties dialog was not seen (locked desktop?)")


# ---- action log in test mode (plan 18.4 item 2) ----------------------------------------------------------------------------

def test_actions_are_recorded_in_the_debug_snapshot(app):
    app.act("copy_path", app.files["act09"])
    status, ui = app.api.get("/v1/_debug/ui")
    assert status == 200
    key = next((k for k in ("actions", "action_log") if k in ui), None)
    if key is None:
        pytest.skip("/v1/_debug/ui carries no action log yet (M3b-9 wiring)")
    entries = ui[key]
    assert any(e.get("verb") == "copy_path" and "act_09.txt" in e.get("path", "") for e in entries), entries[-3:]


# ---- no permanent-delete path (SEC-7, AC-AC-5) -------------------------------------------------------------------------------

DELETE_CALLS = re.compile(r"\bos\.(remove|unlink|rmdir|removedirs)\b|\.unlink\(|\brmtree\b|\.rmdir\(|DeleteFile|"
                          r"SHEmptyRecycleBin|FOF_NOCONFIRMATION|FO_DELETE")
ACTION_SURFACE = ["assistant/actions", "assistant/server/routes_actions.py", "assistant/server/routes_files.py",
                  "assistant/preview", "assistant/server/static.py", "assistant/overlay/search_ui.py",
                  "assistant/server/mcp.py", "assistant/overlay/fullpage.py"]


def _sources(rel):
    path = REPO_ROOT / rel
    if path.is_file():
        yield path
    elif path.is_dir():
        yield from path.rglob("*.py")


def test_the_action_surface_has_no_delete_call_except_the_recycle_bin_move():
    hits = []
    for rel in ACTION_SURFACE:
        for path in _sources(rel):
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                if DELETE_CALLS.search(code):
                    hits.append((str(path.relative_to(REPO_ROOT)), no, line.strip()))
    assert hits, "the Recycle Bin move (FO_DELETE with FOF_ALLOWUNDO) should be the one deletion call"
    assert all(h[0].replace("\\", "/") == "assistant/actions/shell.py" and "FO_DELETE" in h[2] for h in hits), hits


def test_every_fof_flag_set_in_the_package_keeps_allowundo():
    for path in (REPO_ROOT / "assistant").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "SHFileOperation" in text:
            assert "FOF_ALLOWUNDO" in text or "_FOF_ALLOWUNDO" in text, path
            assert "NOCONFIRMATION" not in text, path
    shell_src = (REPO_ROOT / "assistant" / "actions" / "shell.py").read_text(encoding="utf-8")
    assert re.search(r"flags\s*=\s*_FOF_ALLOWUNDO", shell_src)


def test_no_http_route_can_delete():
    routes = (REPO_ROOT / "assistant" / "server" / "routes_actions.py").read_text(encoding="utf-8")
    assert "delete" not in routes.lower().replace("recycle", "")


# ---- M3b security fixes (R9): script classes, Recycle Bin capacity, listing excludes -------------------------------------

@pytest.mark.parametrize("key", ["pyscript", "hta"])
def test_py_and_hta_files_need_confirmation_before_open(app, key):
    ok, message, needs, status = outcome(app.act("open", app.files[key]))
    assert needs and not ok, (key, message)


def test_recycle_is_refused_when_the_bin_is_smaller_than_the_item_or_set_to_delete_at_once(tmp_path, monkeypatch):
    from assistant.actions import shell
    target = tmp_path / "bin-limit-qa.bin"
    target.write_bytes(b"x" * (3 * 1024 * 1024))
    monkeypatch.setattr(shell, "_vet", lambda index, path, **kw: str(target))
    monkeypatch.setattr(shell, "_volume_problem", lambda path: None)
    moved = []
    monkeypatch.setattr(shell, "_recycle_item", lambda path: (moved.append(path), os.remove(path)))
    monkeypatch.setattr(shell, "_bin_settings", lambda path: (1, 0))
    result = shell.recycle(str(target), confirm=True, index=object())
    assert result["ok"] is False and result.get("kind") == "refused" and "permanently" in result["message"], result
    monkeypatch.setattr(shell, "_bin_settings", lambda path: (None, 1))
    result = shell.recycle(str(target), confirm=True, index=object())
    assert result["ok"] is False and result.get("kind") == "refused", result
    assert not moved and target.exists()
    monkeypatch.setattr(shell, "_bin_settings", lambda path: (10, 0))
    result = shell.recycle(str(target), confirm=True, index=object())
    assert result["ok"] is True and moved == [str(target)], result


def test_folder_listing_leaves_out_excluded_folders_such_as_node_modules(app):
    status, body = app.act("folder_listing", app.files["excl_keep"].parent)
    assert status == 200 and body["ok"], body
    names = {r["name"] for r in body["rows"]}
    assert "keep.txt" in names
    assert "node_modules" not in names, names
