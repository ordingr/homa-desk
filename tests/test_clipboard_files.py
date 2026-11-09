"""Copy / Cut of files to the Windows clipboard, in-process (plan 2026-11-09 popup-to-fullpage-and-file-clipboard,
sections 4.2 and 4.3; AC-C1, AC-C3).

Real ``homa.actions.shell.clipboard_files`` over a fixture tree and a fixture index built with ``walk_store`` (no
crawler). The CF_HDROP payload is checked byte for byte and through the Shell's own ``DragQueryFileW`` (no clipboard
needed for that). Round trips put files on the REAL clipboard and read them back with plain ctypes
(``tests/fixtures/clipboard.py``); the clipboard text present before each such test is put back afterwards (best effort).
When this session cannot open the clipboard (a sandboxed or locked desktop: OpenClipboard returns error 5), those tests
skip and say so; nothing is faked.

Nothing here moves, deletes or opens a fixture file: an audit hook records any open/remove/rename/startfile/subprocess
touching the tree while ``clipboard_files`` runs, and the files are checked after every Cut.
"""
import ctypes
import os
import struct
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

from conftest import walk_store
from tests.fixtures import clipboard as cb

shell = pytest.importorskip("homa.actions.shell", reason="homa/actions/shell.py not present")

HEADER = struct.pack("<IiiII", 20, 0, 0, 0, 1)


class FakeIndex:
    def __init__(self, root, store=True):
        self._store = walk_store(root) if store else None
        self._root = str(root)

    def current(self):
        return self._store, None

    def status(self):
        return {"roots": [{"path": self._root}]}


def _junction(link, target) -> bool:
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    return done.returncode == 0


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = Path(os.path.realpath(tmp_path)) / "root"
    (root / "docs").mkdir(parents=True)
    (root / "inner").mkdir()
    (root / "own").mkdir()
    for name in ("a.txt", "b.txt", "\u00fcml\u00e4ut n\u00e4me.txt"):
        (root / "docs" / name).write_text(name, encoding="utf-8")
    (root / "docs" / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (root / "inner" / "f.txt").write_text("inner", encoding="utf-8")
    (root / "own" / "token").write_text("x", encoding="utf-8")
    (root / "docs" / "gone.txt").write_text("soon gone", encoding="utf-8")
    linked = _junction(root / "link", root / "inner")
    monkeypatch.setenv("HOMA_HOME", str(root / "own"))
    index = FakeIndex(root)
    (root / "docs" / "gone.txt").unlink()
    (root / "docs" / "late.txt").write_text("not indexed", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    files = {"a": str(root / "docs" / "a.txt"), "b": str(root / "docs" / "b.txt"),
             "u": str(root / "docs" / "\u00fcml\u00e4ut n\u00e4me.txt"), "env": str(root / "docs" / ".env"),
             "own": str(root / "own" / "token"), "gone": str(root / "docs" / "gone.txt"),
             "late": str(root / "docs" / "late.txt"), "outside": str(outside), "root": str(root),
             "inner": str(root / "inner"), "link": str(root / "link") if linked else None}
    return root, index, files


@pytest.fixture
def clip():
    reason = cb.probe()
    if reason:
        pytest.skip(f"clipboard read-back skipped: this session cannot open the clipboard ({reason})")
    saved = cb.save_text()
    yield cb
    try:
        cb.set_text(saved)
    except cb.ClipboardDenied:
        pass


_EVENTS = {"open", "os.remove", "os.rename", "os.rmdir", "os.startfile", "os.truncate", "shutil.move",
           "shutil.rmtree", "shutil.copyfile", "subprocess.Popen", "os.system"}
_TOUCHED: list = []
_WATCH_ROOTS: list = []


def _audit(event, args):
    if not _WATCH_ROOTS or event not in _EVENTS:
        return
    if event in ("subprocess.Popen", "os.system"):
        _TOUCHED.append((event, str(args[:2])[:200]))
        return
    target = args[0] if args else None
    if isinstance(target, (str, bytes, os.PathLike)):
        name = os.path.normcase(os.fsdecode(target))
        if any(name.startswith(r) for r in _WATCH_ROOTS):
            _TOUCHED.append((event, name))


sys.addaudithook(_audit)


def run(index, paths, op, root):
    _TOUCHED.clear()
    _WATCH_ROOTS[:] = [os.path.normcase(str(root))]
    try:
        return shell.clipboard_files(paths, op, index=index)
    finally:
        _WATCH_ROOTS.clear()


def same(a, b) -> bool:
    return [os.path.normcase(x) for x in a] == [os.path.normcase(x) for x in b]


# ---- DROPFILES payload (pure) -----------------------------------------------------------------------------------------

def test_dropfiles_bytes_is_the_explorer_layout_byte_for_byte():
    paths = [r"C:\one\a.txt", "C:\\\u00fc\\b c.txt"]
    got = shell.dropfiles_bytes(paths)
    assert got[:20] == HEADER
    assert struct.unpack("<IiiII", got[:20]) == (20, 0, 0, 0, 1), "pFiles=20, pt=(0,0), fNC=0, fWide=1"
    assert got[20:] == "C:\\one\\a.txt\0C:\\\u00fc\\b c.txt\0\0".encode("utf-16-le")
    assert got.endswith(b"\0\0\0\0"), "the list ends with an empty string (double NUL, UTF-16)"
    assert len(got) == 20 + 2 * (len(paths[0]) + 1 + len(paths[1]) + 1 + 1)


def test_dropfiles_bytes_is_read_back_by_the_shell_itself():
    """DragQueryFileW (what Explorer's Paste uses) parses the payload in a plain HGLOBAL; no clipboard involved."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sh32 = ctypes.WinDLL("shell32")
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    sh32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    sh32.DragQueryFileW.restype = wintypes.UINT
    paths = [r"C:\x\first.txt", "D:\\\u0645\u062a\u0646\\second file.pdf", r"\\server\share\third"]
    data = shell.dropfiles_bytes(paths)
    h = k32.GlobalAlloc(0x0002, len(data))
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    try:
        assert sh32.DragQueryFileW(h, 0xFFFFFFFF, None, 0) == 3
        got = []
        for i in range(3):
            size = sh32.DragQueryFileW(h, i, None, 0) + 1
            buf = ctypes.create_unicode_buffer(size)
            sh32.DragQueryFileW(h, i, buf, size)
            got.append(buf.value)
        assert got == paths
    finally:
        k32.GlobalFree(h)


def test_constants():
    assert shell.MAX_CLIPBOARD_PATHS == 1000
    assert shell.CLIPBOARD_OPS == {"copy": 1, "cut": 2}


# ---- refusals (none of these reaches the clipboard) ------------------------------------------------------------------

def _refusal_cases(files):
    a = files["a"]
    return {
        "long-path prefix": ("\\\\?\\" + a, "prefixes are not accepted"),
        "device prefix": ("\\\\.\\" + a, "prefixes are not accepted"),
        "NT object prefix": ("\\??\\" + a, "prefixes are not accepted"),
        "long-path prefix, forward slashes": ("//?/" + a.replace("\\", "/"), "prefixes are not accepted"),
        "device prefix, forward slashes": ("//./" + a.replace("\\", "/"), "prefixes are not accepted"),
        "device prefix, mixed slashes": ("\\/.\\" + a, "prefixes are not accepted"),
        "alternate data stream": (a + ":hidden", "outside the indexed roots"),
        "deny-listed .env": (files["env"], "protected file (secrets deny-list)"),
        "Homa's own home": (files["own"], "Homa's own data"),
        "outside the roots": (files["outside"], "outside the indexed roots"),
        "UNC with no UNC root": ("\\\\qa-no-such-host\\share\\a.txt", "outside the indexed roots"),
        "missing file": (files["gone"], "no longer exists"),
        "not indexed": (files["late"], "not in the index"),
        "relative": ("docs\\a.txt", "absolute path"),
        "empty": ("", "not a usable path"),
        "NUL": (a + "\x00.txt", "not a usable path"),
        "over 32767 characters": ("C:\\" + "x" * 32767, "not a usable path"),
        "not a string": (5, "not a usable path"),
    }


CASES = ["long-path prefix", "device prefix", "NT object prefix", "long-path prefix, forward slashes",
         "device prefix, forward slashes", "device prefix, mixed slashes", "alternate data stream", "deny-listed .env",
         "Homa's own home", "outside the roots", "UNC with no UNC root", "missing file", "not indexed", "relative",
         "empty", "NUL", "over 32767 characters", "not a string"]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("op", ["copy", "cut"])
def test_each_refusal_is_reported_and_nothing_is_placed(tree, case, op):
    root, index, files = tree
    path, reason = _refusal_cases(files)[case]
    result = run(index, [path], op, root)
    assert result["ok"] is False and result["kind"] == "refused", result
    assert result["count"] == 0 and result["op"] == op
    assert len(result["skipped"]) == 1 and reason in result["skipped"][0]["reason"], result["skipped"]
    assert _TOUCHED == [], _TOUCHED


def test_cut_of_an_indexed_root_is_refused(tree):
    root, index, files = tree
    result = run(index, [files["root"]], "cut", root)
    assert result["kind"] == "refused" and "indexed root folder" in result["skipped"][0]["reason"], result


@pytest.mark.parametrize("paths, op, field", [
    (["C:\\a.txt"], "move", "op"),
    (["C:\\a.txt"], "", "op"),
    (["C:\\a.txt"], None, "op"),
    ([], "copy", "paths"),
    ("C:\\a.txt", "copy", "paths"),
    (None, "copy", "paths"),
    (["C:\\a.txt"] * 1001, "copy", "paths"),
])
def test_shape_errors_are_invalid_with_the_field(tree, paths, op, field):
    root, index, _ = tree
    result = run(index, paths, op, root)
    assert result["ok"] is False and result["kind"] == "invalid" and result["field"] == field, result


def test_a_thousand_paths_is_the_limit_not_an_error(tree):
    root, index, files = tree
    result = run(index, [files["env"]] * 1000, "copy", root)
    assert result["kind"] == "refused" and len(result["skipped"]) == 1000


def test_an_index_that_is_not_ready_is_unavailable(tree):
    root, _, files = tree
    result = run(FakeIndex(root, store=False), [files["a"]], "copy", root)
    assert result["ok"] is False and result["kind"] == "unavailable", result


def test_all_refused_leaves_the_clipboard_alone(tree, clip):
    root, index, files = tree
    clip.set_text("qa sentinel 7f3e")
    result = run(index, [files["env"], files["outside"], "\\\\?\\" + files["a"]], "cut", root)
    assert result["kind"] == "refused" and len(result["skipped"]) == 3
    got = clip.read()
    assert got["text"] == "qa sentinel 7f3e" and got["files"] == [] and got["effect"] is None


def test_shape_errors_leave_the_clipboard_alone(tree, clip):
    root, index, files = tree
    clip.set_text("qa sentinel 9a1c")
    run(index, [files["a"]], "move", root)
    run(index, [files["a"]] * 1001, "copy", root)
    assert clip.read()["text"] == "qa sentinel 9a1c"


# ---- the link rule for Cut (section 4.3) --------------------------------------------------------------------------------

def test_cut_puts_the_junction_itself_copy_puts_its_target(tree):
    root, index, files = tree
    if files["link"] is None:
        pytest.skip("cannot create a junction here")
    assert os.path.normcase(shell._clipboard_path(index, files["link"], "cut")) == os.path.normcase(files["link"])
    assert os.path.normcase(shell._clipboard_path(index, files["link"], "copy")) == os.path.normcase(files["inner"])


# ---- real clipboard round trips ----------------------------------------------------------------------------------------

def test_copy_round_trip_cf_hdrop_and_drop_effect_1(tree, clip):
    root, index, files = tree
    result = run(index, [files["a"], files["u"]], "copy", root)
    assert result == {"ok": True, "message": "Copied 2 files \u2014 paste in File Explorer", "op": "copy",
                      "count": 2, "skipped": []}, result
    got = clip.read()
    assert same(got["files"], [files["a"], files["u"]]), got["files"]
    assert got["effect"] == 1
    assert got["hdrop_raw"][:20] == HEADER
    assert _TOUCHED == [], _TOUCHED


def test_cut_round_trip_drop_effect_2_and_the_files_stay_put(tree, clip):
    root, index, files = tree
    result = run(index, [files["a"], files["b"]], "cut", root)
    assert result["ok"] and result["count"] == 2 and result["op"] == "cut"
    assert result["message"] == "Cut 2 files \u2014 paste in File Explorer to move them"
    got = clip.read()
    assert same(got["files"], [files["a"], files["b"]]) and got["effect"] == 2, got
    assert os.path.isfile(files["a"]) and os.path.isfile(files["b"]), "Homa never moves a file itself"
    assert _TOUCHED == [], _TOUCHED


def test_one_file_message_is_singular(tree, clip):
    root, index, files = tree
    assert run(index, [files["b"]], "copy", root)["message"] == "Copied 1 file \u2014 paste in File Explorer"


def test_mixed_list_places_the_good_ones_and_reports_the_rest(tree, clip):
    root, index, files = tree
    dup = files["a"].upper()
    result = run(index, [files["a"], files["env"], files["outside"], dup, files["b"]], "copy", root)
    assert result["ok"] is True and result["count"] == 2, result
    assert result["message"] == "Copied 2 files \u2014 paste in File Explorer; 2 skipped"
    assert [s["path"] for s in result["skipped"]] == [files["env"], files["outside"]]
    assert "protected file" in result["skipped"][0]["reason"]
    assert "outside the indexed roots" in result["skipped"][1]["reason"]
    got = clip.read()
    assert same(got["files"], [files["a"], files["b"]]) and got["effect"] == 1, "duplicates (any case) are dropped"


def test_copy_of_an_indexed_root_folder_is_allowed(tree, clip):
    root, index, files = tree
    result = run(index, [files["root"]], "copy", root)
    assert result["ok"] and result["count"] == 1
    assert same(clip.read()["files"], [files["root"]])


def test_cut_round_trip_of_a_junction_places_the_link(tree, clip):
    root, index, files = tree
    if files["link"] is None:
        pytest.skip("cannot create a junction here")
    assert run(index, [files["link"]], "cut", root)["ok"]
    got = clip.read()
    assert same(got["files"], [files["link"]]) and got["effect"] == 2
    assert os.path.isfile(os.path.join(files["inner"], "f.txt"))


def test_action_log_has_one_line_per_call_with_a_count_and_no_paths(tree, clip):
    root, index, files = tree
    before = len(shell.action_log())
    run(index, [files["a"], files["b"]], "copy", root)
    run(index, [files["env"]], "cut", root)
    lines = shell.action_log()[before:]
    assert [(l["verb"], l["path"]) for l in lines] == [("copy_files", "2 paths"), ("cut_files", "1 paths")]
    assert lines[0]["result"] == "ok" and lines[1]["result"].startswith("refused")
    blob = repr(lines)
    assert "a.txt" not in blob and ".env" not in blob and str(root) not in blob


# ---- R15C security notes N1-N6 (docs/security/2026-11-09-clipboard-handoff-review.md) -------------------------------------
# These stub only the final clipboard write (``_set_clipboard_files``) and record what would have been placed; every
# other step is the real code over a real fixture tree and index.

@pytest.fixture
def placed(monkeypatch):
    calls = []
    monkeypatch.setattr(shell, "_set_clipboard_files", lambda paths, effect: calls.append((list(paths), effect)))
    return calls


@pytest.mark.parametrize("bad_op", [["copy"], {}, {"op": "copy"}, 1, 1.5, True, b"copy", ("cut",)])
def test_n1_an_op_that_is_not_a_string_is_invalid_with_field_op(tree, placed, bad_op):
    root, index, files = tree
    result = run(index, [files["a"]], bad_op, root)
    assert result["ok"] is False and result["kind"] == "invalid" and result["field"] == "op", result
    assert placed == []


def test_n2_a_lone_surrogate_path_alone_is_refused_and_json_encodable(tree, placed):
    import json
    root, index, files = tree
    result = run(index, ["\ud800"], "copy", root)
    assert result["kind"] == "refused" and result["count"] == 0, result
    assert "not a usable path" in result["skipped"][0]["reason"]
    assert result["skipped"][0]["path"] == "\\ud800", "echoed as a literal backslash-u escape"
    json.dumps(result).encode("utf-8")
    assert placed == []


def test_n2_surrogate_is_refused_before_vet(tree, placed, monkeypatch):
    root, index, files = tree

    def boom(*a, **k):
        raise AssertionError("_vet reached with an unencodable path")
    monkeypatch.setattr(shell, "_vet", boom)
    result = run(index, [files["a"] + "\udfff"], "cut", root)
    assert result["kind"] == "refused" and "not a usable path" in result["skipped"][0]["reason"], result
    assert placed == []


@pytest.mark.parametrize("op", ["copy", "cut"])
def test_n2_mixed_good_and_surrogate_places_only_the_good_path(tree, placed, op):
    import json
    root, index, files = tree
    result = run(index, [files["a"], "\ud800x", files["b"]], op, root)
    assert result["ok"] is True and result["count"] == 2, result
    assert [s["path"] for s in result["skipped"]] == ["\\ud800x"]
    json.dumps(result).encode("utf-8")
    assert len(placed) == 1 and same(placed[0][0], [files["a"], files["b"]]), placed
    assert placed[0][1] == shell.CLIPBOARD_OPS[op]


@pytest.mark.parametrize("path", ["\\/qa-no-such-host/share/a.txt", "/\\qa-no-such-host\\share\\a.txt",
                                  "//qa-no-such-host/share/a.txt", "\\\\qa-no-such-host\\share\\a.txt"])
@pytest.mark.parametrize("op", ["copy", "cut"])
def test_n3_mixed_separator_unc_never_reaches_realpath(tree, placed, monkeypatch, path, op):
    root, index, files = tree
    real = os.path.realpath
    seen = []

    def spy(p, *a, **k):
        text = os.fsdecode(p)
        if text.replace("/", "\\").startswith("\\\\"):
            seen.append(text)
            raise AssertionError(f"realpath on a UNC-looking path: {text}")
        return real(p, *a, **k)
    monkeypatch.setattr(os.path, "realpath", spy)
    result = run(index, [path], op, root)
    assert result["kind"] == "refused" and "outside the indexed roots" in result["skipped"][0]["reason"], result
    assert seen == [] and placed == []


@pytest.fixture
def ssh_junction(tmp_path, monkeypatch):
    root = Path(os.path.realpath(tmp_path)) / "root"
    (root / "plain").mkdir(parents=True)
    (root / "plain" / "id.txt").write_text("not a secret by name", encoding="utf-8")
    if not _junction(root / ".ssh", root / "plain"):
        pytest.skip("cannot create a junction here")
    (root / "own").mkdir()
    monkeypatch.setenv("HOMA_HOME", str(root / "own"))
    return root, FakeIndex(root)


@pytest.mark.parametrize("suffix", [".", "  ", ". . ", " "])
@pytest.mark.parametrize("op", ["copy", "cut"])
def test_n4_trailing_dot_or_space_spellings_of_a_secret_named_junction_are_refused(ssh_junction, placed, op, suffix):
    root, index = ssh_junction
    spelled = str(root / ".ssh") + suffix
    result = run(index, [spelled], op, root)
    assert result["kind"] == "refused" and result["count"] == 0, (spelled, result)
    assert "protected file (secrets deny-list)" in result["skipped"][0]["reason"], result
    assert placed == []


@pytest.mark.parametrize("op", ["copy", "cut"])
def test_n4_the_exact_name_is_refused_too(ssh_junction, placed, op):
    root, index = ssh_junction
    result = run(index, [str(root / ".ssh")], op, root)
    assert result["kind"] == "refused" and "deny-list" in result["skipped"][0]["reason"], result


def test_n4_win_trim_keeps_dot_components():
    assert shell._win_trim("C:\\a\\.ssh. .\\b ") == "C:\\a\\.ssh\\b"
    assert shell._win_trim("C:\\a\\..\\.\\b") == "C:\\a\\..\\.\\b"


def test_n4_link_or_target_does_not_return_a_trimmed_secret_link(ssh_junction):
    root, index = ssh_junction
    given = str(root / ".ssh") + "."
    resolved = os.path.realpath(given)
    assert shell._link_or_target(index, given, resolved) == resolved, "the link is never placed, only the target"


@pytest.fixture
def holder_tree(tmp_path, monkeypatch):
    root = Path(os.path.realpath(tmp_path)) / "root"
    home = root / "holder" / "homa-home"
    home.mkdir(parents=True)
    (home / "token").write_text("x", encoding="utf-8")
    (root / "holder" / "other").mkdir()
    (root / "sibling").mkdir()
    (root / "sibling" / "f.txt").write_text("f", encoding="utf-8")
    monkeypatch.setenv("HOMA_HOME", str(home))
    return root, FakeIndex(root), {"holder": str(root / "holder"), "sibling": str(root / "sibling"),
                                   "other": str(root / "holder" / "other")}


def test_n5_cut_of_a_folder_holding_homa_home_is_refused(holder_tree, placed):
    root, index, f = holder_tree
    result = run(index, [f["holder"]], "cut", root)
    assert result["kind"] == "refused", result
    assert "holds Homa's own data" in result["skipped"][0]["reason"]
    assert placed == []


def test_n5_copy_of_that_folder_is_allowed_and_unrelated_cuts_are_too(holder_tree, placed):
    root, index, f = holder_tree
    assert run(index, [f["holder"]], "copy", root)["ok"] is True
    assert run(index, [f["sibling"]], "cut", root)["ok"] is True
    assert run(index, [f["other"]], "cut", root)["ok"] is True
    assert [(len(p), e) for p, e in placed] == [(1, 1), (1, 2), (1, 2)]
