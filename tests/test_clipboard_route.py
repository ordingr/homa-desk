"""POST /v1/files/clipboard over HTTP (plan 2026-11-09 popup-to-fullpage-and-file-clipboard, section 4.2; AC-C1,
AC-C3): auth, Host/Origin, body and count limits, shape errors, prefix refusals, deny-list skip, Cut of a root, the
response shape, and a read-back of what the server put on the real clipboard.

A private test-mode app (``launch_app``: scratch HOMA_HOME, USERPROFILE, LOCALAPPDATA and APPDATA, a free port, never
17171) indexes a fixture root. Read-backs skip with the reason when this session cannot open the clipboard (the server
then answers 500 "Windows would not let Homa use the clipboard"); nothing is faked. The clipboard text is restored
after the module (best effort).
"""
import os
from pathlib import Path

import pytest

from conftest import Api, _free_port, launch_app, wait_index
from tests.fixtures import clipboard as cb

BUSY = "Windows would not let Homa use the clipboard."


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    base = Path(os.path.realpath(tmp_path_factory.mktemp("cliproute")))
    root = base / "root"
    (root / "docs" / "sub").mkdir(parents=True)
    files = {}
    for rel in ("docs/a.txt", "docs/b.txt", "docs/sub/c.txt"):
        (root / rel).write_text(rel, encoding="utf-8")
        files[Path(rel).stem] = str(root / rel)
    (root / "docs" / ".env").write_text("SECRET=1\n", encoding="utf-8")
    files["env"] = str(root / "docs" / ".env")
    files["root"] = str(root)
    roaming = base / "appdata_roaming"
    roaming.mkdir()
    previous = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(roaming)
    saved = cb.save_text() if not cb.probe() else None
    try:
        with launch_app(base / "home", _free_port(), {"index": {"roots": [str(root)]}}, test_mode=True) \
                as (url, token):
            assert not url.endswith(":17171")
            index = wait_index(url, token, minimum=5)
            if not (isinstance(index, dict) and index.get("entries", 0) >= 5):
                pytest.skip(f"the index never reached 5 entries: {index!r}")
            yield Api(url, token), files
    finally:
        if previous is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = previous
        if not cb.probe():
            try:
                cb.set_text(saved)
            except cb.ClipboardDenied:
                pass


def post(api, body, **kw):
    return api.post("/v1/files/clipboard", body, **kw)


def readback_or_skip(status, body):
    """The server's own clipboard write and this process's read must both work, else skip with the reason."""
    reason = cb.probe()
    if status == 500 and (body or {}).get("error", {}).get("message") == BUSY:
        pytest.skip(f"clipboard read-back skipped: the server could not open the clipboard ({reason or 'error 500'})")
    if reason:
        pytest.skip(f"clipboard read-back skipped: this session cannot open the clipboard ({reason})")
    return cb.read()


def same(a, b) -> bool:
    return [os.path.normcase(x) for x in a] == [os.path.normcase(x) for x in b]


# ---- auth, Host, Origin --------------------------------------------------------------------------------------------------

def test_no_bearer_or_a_wrong_one_is_401(served):
    api, files = served
    assert post(api, {"paths": [files["a"]], "op": "copy"}, token=False)[0] == 401
    assert post(api, {"paths": [files["a"]], "op": "copy"}, token="not-the-token")[0] == 401


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1.evil.example", "null",
                                    "http://192.168.1.5:3000"])
def test_a_foreign_origin_is_403_even_with_the_bearer(served, origin):
    api, files = served
    status, body = post(api, {"paths": [files["a"]], "op": "copy"}, headers={"Origin": origin})
    assert status == 403 and body["error"]["code"] == "forbidden_origin", (status, body)


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "192.168.1.5"])
def test_a_foreign_host_is_403(served, host):
    api, files = served
    status, body = post(api, {"paths": [files["a"]], "op": "copy"}, headers={"Host": host})
    assert status == 403 and body["error"]["code"] == "forbidden_host", (status, body)


def test_a_localhost_origin_passes_the_middleware(served):
    api, files = served
    status, body = post(api, {"paths": [files["a"]], "op": "move"}, headers={"Origin": "http://localhost:5173"})
    assert status == 422 and body["error"]["field"] == "op", "reached the route (shape error, not 403)"


def test_get_is_405(served):
    api, _ = served
    assert api.get("/v1/files/clipboard")[0] == 405


# ---- limits and shapes ---------------------------------------------------------------------------------------------------

def test_a_body_over_256_kib_is_413(served):
    # Headers only: the route refuses on the declared Content-Length and closes. Pushing the 256 KiB body first made
    # Windows reset the socket (WinError 10054) before the 413 could be read, about one run in three.
    import http.client
    import json
    api, files = served
    payload = json.dumps({"paths": [files["a"], "x" * 262_144], "op": "copy"}).encode("utf-8")
    conn = http.client.HTTPConnection(api.host, int(api.port), timeout=10)
    try:
        conn.putrequest("POST", "/v1/files/clipboard")
        conn.putheader("Authorization", f"Bearer {api.token}")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(payload)))
        conn.endheaders()
        response = conn.getresponse()
        body = json.loads(response.read())
    finally:
        conn.close()
    assert len(payload) > 262_144
    assert response.status == 413 and body["error"]["code"] == "payload_too_large", (response.status, body)


@pytest.mark.parametrize("body, field", [
    ({"paths": ["C:\\a.txt"], "op": "move"}, "op"),
    ({"paths": ["C:\\a.txt"]}, "op"),
    ({"paths": ["C:\\a.txt"], "op": "COPY"}, "op"),
    ({"paths": ["C:\\a.txt"], "op": ["copy"]}, "op"),
    ({"paths": ["C:\\a.txt"], "op": {}}, "op"),
    ({"paths": ["C:\\a.txt"], "op": 1}, "op"),
    ({"op": "copy"}, "paths"),
    ({"paths": [], "op": "copy"}, "paths"),
    ({"paths": "C:\\a.txt", "op": "copy"}, "paths"),
    ({"paths": ["C:\\a.txt", 5], "op": "copy"}, "paths"),
    ({"paths": [None], "op": "cut"}, "paths"),
    ({"paths": ["C:\\a.txt"] * 1001, "op": "copy"}, "paths"),
])
def test_shape_errors_are_422_with_the_field(served, body, field):
    api, _ = served
    status, got = post(api, body)
    assert status == 422 and got["error"]["code"] == "invalid_parameter" and got["error"]["field"] == field, got


def test_a_non_object_body_is_422(served):
    api, _ = served
    status, got = post(api, ["C:\\a.txt"])
    assert status == 422 and got["error"]["code"] == "invalid_body", got


# ---- refusals --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\", "\\??\\", "//?/", "//./"])
@pytest.mark.parametrize("op", ["copy", "cut"])
def test_raw_prefixes_are_refused_403(served, prefix, op):
    api, files = served
    target = files["a"] if prefix.startswith("\\") else files["a"].replace("\\", "/")
    status, got = post(api, {"paths": [prefix + target], "op": op})
    assert status == 403 and got["error"]["code"] == "refused", got
    assert "prefixes are not accepted" in got["skipped"][0]["reason"]


def test_a_deny_listed_file_alone_is_403_with_the_reason(served):
    api, files = served
    status, got = post(api, {"paths": [files["env"]], "op": "copy"})
    assert status == 403 and got["error"]["code"] == "refused"
    assert got["skipped"] == [{"path": files["env"], "reason": "refused: protected file (secrets deny-list)"}]


def test_cut_of_the_indexed_root_is_403(served):
    api, files = served
    status, got = post(api, {"paths": [files["root"]], "op": "cut"})
    assert status == 403 and "indexed root folder" in got["skipped"][0]["reason"], got


def test_a_path_outside_the_roots_is_403(served):
    api, _ = served
    status, got = post(api, {"paths": ["C:\\Windows\\win.ini"], "op": "copy"})
    assert status == 403 and "outside the indexed roots" in got["skipped"][0]["reason"], got


# ---- success: response shape and the real clipboard ---------------------------------------------------------------------

def test_copy_200_shape_and_read_back(served):
    api, files = served
    status, got = post(api, {"paths": [files["a"], files["c"]], "op": "copy"})
    clip = readback_or_skip(status, got)
    assert status == 200, got
    assert got == {"ok": True, "message": "Copied 2 files \u2014 paste in File Explorer", "op": "copy", "count": 2,
                   "skipped": []}
    assert same(clip["files"], [files["a"], files["c"]]) and clip["effect"] == 1, clip


def test_cut_200_read_back_effect_2_and_files_stay(served):
    api, files = served
    status, got = post(api, {"paths": [files["a"], files["b"]], "op": "cut"})
    clip = readback_or_skip(status, got)
    assert status == 200 and got["op"] == "cut" and got["count"] == 2, got
    assert same(clip["files"], [files["a"], files["b"]]) and clip["effect"] == 2, clip
    assert os.path.isfile(files["a"]) and os.path.isfile(files["b"])


def test_partial_success_is_200_with_the_skipped_deny_listed_file(served):
    api, files = served
    status, got = post(api, {"paths": [files["env"], files["b"]], "op": "copy"})
    clip = readback_or_skip(status, got)
    assert status == 200 and got["ok"] is True and got["count"] == 1, got
    assert got["skipped"] == [{"path": files["env"], "reason": "refused: protected file (secrets deny-list)"}]
    assert got["message"].endswith("; 1 skipped")
    assert same(clip["files"], [files["b"]]) and clip["effect"] == 1, "the protected file never reaches the clipboard"


def test_the_debug_action_log_counts_paths_and_names_none(served):
    api, files = served
    post(api, {"paths": [files["env"], files["a"] + ":x"], "op": "cut"})
    status, body = api.get("/v1/_debug/ui")
    assert status == 200
    lines = [l for l in body["actions"] if l.get("verb") in ("copy_files", "cut_files")]
    assert lines and lines[-1]["verb"] == "cut_files" and lines[-1]["path"] == "2 paths", lines
    assert all(l["path"].endswith(" paths") for l in lines), lines
    assert "a.txt" not in repr(lines) and ".env" not in repr(lines)


# ---- R15C-N2: a path that cannot encode to UTF-16 ---------------------------------------------------------------------------

def test_a_lone_surrogate_path_alone_is_403_and_the_response_is_json(served):
    api, _ = served
    status, got = post(api, {"paths": ["\ud800"], "op": "copy"})
    assert status == 403 and got["error"]["code"] == "refused", (status, got)
    assert got["skipped"][0]["path"] == "\\ud800" and "not a usable path" in got["skipped"][0]["reason"]
    assert api.get("/v1/health")[0] == 200, "the server is still up"


def test_a_mixed_good_and_surrogate_request_is_200_with_only_the_good_path_placed(served):
    api, files = served
    status, got = post(api, {"paths": [files["a"], "\ud800"], "op": "copy"})
    state = readback_or_skip(status, got)
    assert status == 200 and got["ok"] is True and got["count"] == 1, got
    assert [s["path"] for s in got["skipped"]] == ["\\ud800"]
    assert same(state["files"], [files["a"]]) and state["effect"] == 1, state


def test_all_refused_including_a_surrogate_leaves_the_clipboard_alone(served):
    api, files = served
    if cb.probe():
        pytest.skip(f"clipboard read-back skipped: this session cannot open the clipboard ({cb.probe()})")
    cb.set_text("qa sentinel c15n2")
    status, got = post(api, {"paths": ["\ud800", files["env"]], "op": "cut"})
    assert status == 403 and len(got["skipped"]) == 2, got
    assert cb.read()["text"] == "qa sentinel c15n2"
