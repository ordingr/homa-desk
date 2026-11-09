"""Preview routes, static mount and launch nonce (plan 7.4 M3b-3; AC-PV-3..6, AC-SH-7, SEC-2, SEC-5).

Live tests run against ``conftest.pv`` (a private test-mode app whose one root holds the generated
``tests/fixtures/preview`` samples). They skip until ``GET /v1/files/preview`` is served. The contract assumed from the
plan: the JSON answer carries ``kind`` (markdown|text|html|image|table|pdf|docx|xlsx|pptx|meta|protected|placeholder|
too_large) and file bytes are streamed with ``Content-Type`` and ``Accept-Ranges`` (206 on ``Range``). Field names beyond
``kind`` are not fixed, so content assertions look at the whole serialised answer.
"""
import json
import os
import sys
import time
import urllib.parse

import pytest

from conftest import Api, launch_app, _free_port, wait_until
from tests.fixtures.preview import MB, OFFLINE_MARKER

KINDS = {"markdown", "text", "html", "image", "table", "pdf", "docx", "xlsx", "pptx", "meta", "protected",
         "placeholder", "too_large"}
PEM_BODY = "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu"


def dump(status_body) -> str:
    return json.dumps(status_body, default=str)


def ok_kind(app, key, **params):
    status, body = app.preview(key, **params)
    assert status == 200, (key, status, body)
    assert isinstance(body, dict) and body.get("kind") in KINDS, (key, body)
    return body


@pytest.fixture(scope="module")
def pv(preview_app):
    status, _ = preview_app.preview("csv")
    if status == 404:
        pytest.skip("GET /v1/files/preview is not served yet (M3b-3 routes_files.py)")
    return preview_app


# ---- deny-list, placeholder, size, binary (AC-PV-4/5/6) -------------------------------------------------------------

def test_pem_is_protected_and_no_key_material_is_returned(pv):
    body = ok_kind(pv, "pem")
    assert body["kind"] == "protected"
    assert PEM_BODY not in dump(body) and "BEGIN RSA" not in dump(body)


def test_text_file_containing_a_private_key_is_protected(pv):
    body = ok_kind(pv, "keybackup")
    assert body["kind"] == "protected"
    assert PEM_BODY not in dump(body)


def test_private_key_beyond_the_first_megabyte_is_caught_when_the_requested_range_covers_it(pv):
    size = os.path.getsize(pv.files["latekey"])
    assert size > MB + 1024
    status, headers, data = pv.raw(
        "GET", "/v1/files/preview?" + urllib.parse.urlencode({"path": str(pv.files["latekey"])}),
        headers={"Range": f"bytes={size - 400}-"})
    assert PEM_BODY.encode() not in data, "key bytes were served from a range that covers the key"
    assert status in (200, 403, 422) and (status != 200 or b"protected" in data.lower())


def test_offline_file_is_a_placeholder_and_its_content_is_not_returned(pv):
    body = ok_kind(pv, "offline")
    assert body["kind"] == "placeholder"
    assert OFFLINE_MARKER not in dump(body)


def test_sixty_megabyte_file_is_too_large_without_loading_it(pv):
    assert os.path.getsize(pv.files["big"]) == 60 * MB
    started = time.perf_counter()
    body = ok_kind(pv, "big")
    assert body["kind"] == "too_large"
    assert time.perf_counter() - started < 5
    assert len(dump(body)) < 4096


def test_executable_gets_a_metadata_card(pv):
    body = ok_kind(pv, "exe")
    assert body["kind"] == "meta"
    assert "setup.exe" in dump(body)


def test_binary_content_behind_a_text_extension_is_not_shown_as_text(pv):
    body = ok_kind(pv, "binary")
    assert body["kind"] in ("meta", "too_large", "protected")
    assert "text start" not in dump(body)


# ---- content kinds (AC-PV-2, AC-PV-3) ------------------------------------------------------------------------------------

def test_markdown_comes_back_as_data_never_as_html_and_keeps_the_script_text_inert(pv):
    status, headers, data = pv.raw(
        "GET", "/v1/files/preview?" + urllib.parse.urlencode({"path": str(pv.files["md"])}))
    assert status == 200
    assert headers["content-type"].startswith("application/json"), headers
    body = json.loads(data)
    assert body["kind"] == "markdown"
    text = dump(body)
    assert "mermaid" in text and "markmap" in text, "diagram fences must reach the client untouched"
    assert "<script>alert(1)</script>" in text, "raw markdown text is passed through for the client to sanitise"
    assert body["kind"] != "html"


def test_svg_is_never_served_as_inline_html_or_text_html(pv):
    status, headers, data = pv.raw(
        "GET", "/v1/files/preview?" + urllib.parse.urlencode({"path": str(pv.files["svg"])}))
    assert status == 200
    ctype = headers["content-type"].lower()
    assert not ctype.startswith("text/html"), headers
    if ctype.startswith("application/json"):
        body = json.loads(data)
        assert body["kind"] in ("image", "text", "meta"), body
    else:
        assert ctype.startswith("image/svg+xml"), headers
        assert headers.get("x-content-type-options", "").lower() == "nosniff", headers


def test_csv_is_a_table(pv):
    body = ok_kind(pv, "csv")
    assert body["kind"] == "table"
    assert "widget" in dump(body) and "gadget" in dump(body)


def test_notebook_is_readable_text(pv):
    body = ok_kind(pv, "ipynb")
    assert body["kind"] in ("text", "markdown", "table")
    assert "hi-notebook" in dump(body)


def test_docx_text_extraction(pv):
    body = ok_kind(pv, "docx")
    assert body["kind"] == "docx" and "hello-docx" in dump(body) and "second paragraph" in dump(body)


def test_xlsx_text_extraction_as_cells(pv):
    body = ok_kind(pv, "xlsx")
    assert body["kind"] in ("xlsx", "table")
    text = dump(body)
    assert "item2" in text and "item3" in text and "20" in text


def test_pptx_slide_text_and_media_list(pv):
    body = ok_kind(pv, "pptx")
    assert body["kind"] == "pptx"
    text = dump(body)
    assert "hello-pptx" in text
    assert "image1.png" in text, "the embedded media must be listed"


def test_pdf_is_answered_as_pdf_or_with_extracted_text(pv):
    status, headers, data = pv.raw(
        "GET", "/v1/files/preview?" + urllib.parse.urlencode({"path": str(pv.files["pdf"])}))
    assert status == 200
    if headers["content-type"].startswith("application/json"):
        body = json.loads(data)
        assert body["kind"] == "pdf"
    else:
        assert headers["content-type"].startswith("application/pdf")
        assert data.startswith(b"%PDF")
        assert headers.get("accept-ranges", "").lower() == "bytes"


def test_png_webp_avif_are_images_and_heic_is_not_shown_as_an_image(pv):
    for key, mime in (("png", "image/png"), ("webp", "image/webp"), ("avif", "image/avif")):
        status, headers, data = pv.raw(
            "GET", "/v1/files/preview?" + urllib.parse.urlencode({"path": str(pv.files[key])}))
        assert status == 200, key
        if headers["content-type"].startswith("application/json"):
            assert json.loads(data)["kind"] == "image", key
        else:
            assert headers["content-type"].lower().startswith(mime), (key, headers)
    status, headers, data = pv.raw(
        "GET", "/v1/files/preview?" + urllib.parse.urlencode({"path": str(pv.files["heic"])}))
    assert status == 200
    if headers["content-type"].startswith("application/json"):
        assert json.loads(data)["kind"] == "meta"
    else:
        assert not headers["content-type"].lower().startswith("image/")


# ---- ranges -----------------------------------------------------------------------------------------------------------------

def _ranged(app, key, rng):
    path = "/v1/files/preview?" + urllib.parse.urlencode({"path": str(app.files[key])})
    status, headers, data = app.raw("GET", path, headers={"Range": rng})
    if status == 200:
        status, headers, data = app.raw("GET", path + "&" + urllib.parse.urlencode({"range": rng}))
    return status, headers, data


def test_range_on_a_3mb_log_returns_206_with_exactly_the_requested_bytes(pv):
    size = os.path.getsize(pv.files["log"])
    assert size >= 3 * MB - 10
    status, headers, data = _ranged(pv, "log", "bytes=0-1048575")
    assert status == 206, (status, headers)
    assert len(data) == 1048576
    assert headers.get("accept-ranges", "").lower() == "bytes"
    assert headers.get("content-range", "").startswith("bytes 0-1048575/")
    assert data == pv.files["log"].read_bytes()[:1048576]
    assert not headers["content-type"].lower().startswith("text/html")


def test_open_ended_and_out_of_range_requests(pv):
    size = os.path.getsize(pv.files["log"])
    status, headers, data = _ranged(pv, "log", f"bytes={size - 100}-")
    assert status == 206 and len(data) == 100
    status, headers, data = _ranged(pv, "log", f"bytes={size + 10}-{size + 20}")
    assert status == 416, status


# ---- path and auth rules -------------------------------------------------------------------------------------------------

def test_a_file_outside_every_root_is_refused_without_leaking_content(pv, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET-m3b6\n", encoding="utf-8")
    status, body = pv.preview(outside)
    assert status in (400, 403, 404, 422) or (status == 200 and body["kind"] in ("meta", "protected"))
    assert "OUTSIDE-SECRET-m3b6" not in dump(body)


def test_dot_dot_escape_out_of_the_root_is_refused(pv, tmp_path):
    outside = tmp_path / "escape-target.txt"
    outside.write_text("ESCAPED-m3b6\n", encoding="utf-8")
    sneaky = str(pv.root / "notes" / ".." / ".." / ".." / outside.name)
    status, body = pv.preview(sneaky)
    assert "ESCAPED-m3b6" not in dump(body)
    assert status != 200 or body.get("kind") in ("meta", "protected")


def test_the_assistants_own_folder_is_refused(pv):
    status, body = pv.preview(pv.home / "token")
    assert pv.token not in dump(body)
    assert status != 200 or body.get("kind") in ("meta", "protected")


def test_the_preview_route_requires_the_bearer_token(pv):
    qs = urllib.parse.urlencode({"path": str(pv.files["csv"])})
    status, _ = pv.api.get("/v1/files/preview?" + qs, token=False)
    assert status == 401
    status, _ = pv.api.get("/v1/files/preview?" + qs, token="not-the-token")
    assert status == 401


def test_a_folder_and_a_missing_file_do_not_crash_the_route(pv):
    status, body = pv.preview(pv.root / "notes")
    assert status in (200, 400, 404, 422) and "Traceback" not in dump(body)
    status, body = pv.preview(pv.root / "notes" / "does-not-exist.md")
    assert status in (400, 403, 404, 422) and "Traceback" not in dump(body)


# ---- static mount (HOMA_STATIC_DIR) ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def static_app(tmp_path_factory):
    base = tmp_path_factory.mktemp("static_app")
    static = base / "build"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>qa-static-index</title>", encoding="utf-8")
    (static / "assets" / "a.js").write_text("console.log('qa-asset')", encoding="utf-8")
    (base / "outside-secret.txt").write_text("STATIC-TRAVERSAL-SECRET\n", encoding="utf-8")
    previous = os.environ.get("HOMA_STATIC_DIR")
    os.environ["HOMA_STATIC_DIR"] = str(static)
    try:
        with launch_app(base / "home", _free_port(), {}) as (base_url, token):
            api = Api(base_url, token)
            # Stopping the app before its index thread has started raises in IndexManager.stop (follow-up
            # for the Tech Lead), so wait for startup to finish before any early skip.
            console = base / "home" / "console.txt"
            wait_until(lambda: "file index loaded" in console.read_text(errors="replace"), timeout=30)
            status, _ = api.get("/app/assets/a.js")
            if status == 404:
                pytest.skip("/app/ is not served yet (M3b-3 static.py)")
            yield api
    finally:
        if previous is None:
            os.environ.pop("HOMA_STATIC_DIR", None)
        else:
            os.environ["HOMA_STATIC_DIR"] = previous


def _get(api, path, token=True):
    import http.client
    conn = http.client.HTTPConnection(api.host, int(api.port), timeout=10)
    hdrs = {"Authorization": f"Bearer {api.token}"} if token else {}
    try:
        conn.request("GET", path, headers=hdrs)
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def test_static_files_are_served_from_the_override_folder_with_no_store(static_app):
    status, headers, data = _get(static_app, "/app/assets/a.js")
    assert status == 200 and b"qa-asset" in data
    assert any(w in headers.get("cache-control", "").lower() for w in ("no-store", "no-cache")), headers
    status, headers, data = _get(static_app, "/app/")
    assert status == 200 and b"qa-static-index" in data
    assert "no-store" in headers.get("cache-control", "").lower()


@pytest.mark.parametrize("probe", ["/app/../outside-secret.txt", "/app/%2e%2e/outside-secret.txt",
                                   "/app/..%2foutside-secret.txt", "/app/assets/..%5c..%5coutside-secret.txt",
                                   "/app/assets/../../outside-secret.txt"])
def test_static_mount_does_not_traverse_out_of_the_folder(static_app, probe):
    status, headers, data = _get(static_app, probe)
    assert b"STATIC-TRAVERSAL-SECRET" not in data, (probe, status)


def test_the_page_loads_without_a_bearer_but_the_exemption_never_reaches_v1(static_app):
    status, headers, data = _get(static_app, "/app/?launch=unknown-nonce", token=False)
    assert status == 200 and b"qa-static-index" in data
    for probe in ("/v1/health", "/v1/health?launch=x", "/v1/search?q=a", "/v1/files/preview?path=C:%5Cx.txt",
                  "/app/../v1/health"):
        status, _, data = _get(static_app, probe, token=False)
        assert status in (401, 404) and static_app.token.encode() not in data, probe
    status, _, _ = _get(static_app, "/v1/health", token=True)
    assert status == 200


def test_exchange_with_an_unknown_nonce_is_forbidden_and_gives_no_token(static_app):
    status, body = static_app.post("/v1/session/exchange", {"nonce": "never-issued"}, token=False)
    assert status == 403, (status, body)
    assert static_app.token not in dump(body)
    status, body = static_app.post("/v1/session/exchange", {}, token=False)
    assert status in (400, 403, 422)


def test_fullpage_close_requires_the_token_and_is_harmless_with_no_window(static_app):
    status, _ = static_app.post("/v1/fullpage/close", {}, token=False)
    assert status == 401
    status, body = static_app.post("/v1/fullpage/close", {})
    if status == 404:
        pytest.skip("/v1/fullpage/close is not served yet (M3b-3 / M3b-9 wiring)")
    assert status in (200, 204), (status, body)


# ---- nonce lifecycle (in-process; the minting call is not named in the plan) -------------------------------------------

def _nonce_api():
    auth = pytest.importorskip("homa.server.auth", reason="homa/server/auth.py not present")
    for name in dir(auth):
        low = name.lower()
        if "nonce" in low and any(w in low for w in ("mint", "issue", "new", "create", "make")) and callable(getattr(auth, name)):
            return getattr(auth, name)
    pytest.skip("no nonce-minting function found in homa.server.auth (names tried: *mint*/*issue*/*new*/*create*/"
                "*make* + nonce); the plan fixes the exchange route but not the mint call - tech-lead to name it")


def _asgi_post(app, path, body: dict):
    import asyncio
    payload = json.dumps(body).encode()
    sent = []
    queue = [payload]

    async def receive():
        if queue:
            return {"type": "http.request", "body": queue.pop(0), "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(b"host", b"127.0.0.1"), (b"content-type", b"application/json"),
                         (b"content-length", str(len(payload)).encode())],
             "http_version": "1.1", "scheme": "http", "server": ("127.0.0.1", 1), "client": ("127.0.0.1", 2),
             "root_path": ""}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], (json.loads(data) if data else None)


def test_nonce_exchanges_once_then_is_forbidden():
    mint = _nonce_api()
    from homa.server import app as server_app
    token = "T" * 43
    app = server_app.create_app(lambda: token)
    nonce = mint()
    nonce = nonce[0] if isinstance(nonce, tuple) else nonce
    status, body = _asgi_post(app, "/v1/session/exchange", {"nonce": nonce})
    assert status == 200 and body == {"token": token}, (status, body)
    status, body = _asgi_post(app, "/v1/session/exchange", {"nonce": nonce})
    assert status == 403 and token not in json.dumps(body)


def test_nonce_expires_after_30_seconds(monkeypatch):
    mint = _nonce_api()
    from homa.server import app as server_app
    from homa.server import auth
    token = "T" * 43
    app = server_app.create_app(lambda: token)
    nonce = mint()
    nonce = nonce[0] if isinstance(nonce, tuple) else nonce
    clock = getattr(auth, "_clock", None)
    if clock is not None:
        base = clock()
        monkeypatch.setattr(auth, "_clock", lambda: base + 29.0)
        near = mint()
        near = near[0] if isinstance(near, tuple) else near
        status, body = _asgi_post(app, "/v1/session/exchange", {"nonce": near})
        assert status == 200, "a nonce must still work 29 s after it was minted"
        monkeypatch.setattr(auth, "_clock", lambda: base + 31.0)
    else:
        time.sleep(31)
    status, body = _asgi_post(app, "/v1/session/exchange", {"nonce": nonce})
    assert status == 403 and token not in json.dumps(body)


def test_nonces_are_unique_unguessable_and_bounded():
    mint = _nonce_api()
    seen = {mint() for _ in range(8)}
    assert len(seen) == 8 and all(isinstance(n, str) and len(n) >= 32 for n in seen)


# ---- the audit-hook proof: refused files are never opened (in-process ASGI over the real route module) ---------------

_OPENED = []
_WATCH = set()


def _audit(event, args):
    if event == "open" and _WATCH:
        target = args[0]
        if isinstance(target, (str, bytes, os.PathLike)):
            name = os.path.normcase(os.fsdecode(target))
            if name in _WATCH:
                _OPENED.append(name)


sys.addaudithook(_audit)


class _FakeIndex:
    def __init__(self, store, root):
        self._store, self._root = store, str(root)

    def current(self):
        return self._store, None

    def status(self):
        return {"roots": [{"path": self._root}]}


def _asgi_get(app, path_with_query: str, token: str):
    import asyncio
    path, _, qs = path_with_query.partition("?")
    sent = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": qs.encode(),
             "headers": [(b"host", b"127.0.0.1"), (b"authorization", b"Bearer " + token.encode())],
             "http_version": "1.1", "scheme": "http", "server": ("127.0.0.1", 1), "client": ("127.0.0.1", 2),
             "root_path": ""}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def test_offline_and_deny_listed_files_are_never_opened_by_the_server(tmp_path):
    pytest.importorskip("homa.server.routes_files", reason="homa/server/routes_files.py (M3b-3) not present")
    from homa.server import app as server_app
    from conftest import walk_store
    from tests.fixtures.preview import build_preview_tree
    files = build_preview_tree(tmp_path / "root")
    store = walk_store(tmp_path / "root")
    token = "T" * 43
    fake = _FakeIndex(store, tmp_path / "root")
    app = server_app.create_app(lambda: token, index=fake, settings=None)
    if not any(getattr(r, "path", "") == "/v1/files/preview" for r in app.router.routes):
        from homa.server import routes_files
        routes_files.register_files_routes(app, fake, None)
    for key in ("offline", "pem", "big"):
        _WATCH.clear()
        _OPENED.clear()
        _WATCH.add(os.path.normcase(str(files[key])))
        status, data = _asgi_get(app, "/v1/files/preview?" + urllib.parse.urlencode({"path": str(files[key])}), token)
        _WATCH.clear()
        if status == 404:
            pytest.skip("preview route not mounted by create_app in-process (M3b-9 wiring)")
        assert status == 200, (key, status, data[:200])
        assert json.loads(data)["kind"] == {"offline": "placeholder", "pem": "protected", "big": "too_large"}[key]
        assert not _OPENED, f"{key}: the server opened a file it must not read: {_OPENED}"
    _WATCH.clear()
    _OPENED.clear()
    _WATCH.add(os.path.normcase(str(files["csv"])))
    status, data = _asgi_get(app, "/v1/files/preview?" + urllib.parse.urlencode({"path": str(files["csv"])}), token)
    _WATCH.clear()
    assert status == 200 and _OPENED, "control: a previewable file must show up in the audit hook, or the hook proves nothing"


# ---- M3b security fixes (R9): CSP on /app/, key scan before the window, bounded Range, linear extractor --------------

def test_app_index_carries_a_content_security_policy_that_forbids_external_loads(pv):
    status, headers, data = pv.raw("GET", "/app/", token=False)
    assert status == 200
    csp = headers.get("content-security-policy", "")
    assert csp, headers
    assert "default-src 'self'" in csp
    assert "connect-src" not in csp or "http" not in csp.split("connect-src", 1)[1].split(";")[0].replace("http://127.0.0.1", "")
    assert "*" not in csp.replace("'unsafe-inline'", "").replace("'wasm-unsafe-eval'", "")


def test_a_key_header_before_the_requested_range_still_protects_the_range(pv):
    size = os.path.getsize(pv.files["key10k"])
    assert 10_000 < size < 10_500
    status, body = pv.preview("key10k", range="10100-10400")
    assert status == 200 and body["kind"] == "protected", body
    assert "BEGIN" not in dump(body) and PEM_BODY not in dump(body)
    status, body = pv.preview("key10k", range="0-100")
    assert status == 200 and body["kind"] == "protected", ("the default scan covers the first 1 MiB", body)


def test_a_5000_digit_range_is_refused_with_416_or_422_not_an_error_page(pv):
    path = "/v1/files/preview?" + urllib.parse.urlencode({"path": str(pv.files["log"])})
    for header in ("bytes=" + "9" * 5000 + "-", "bytes=0-" + "9" * 5000, "bytes=" + "1" * 5000 + "-" + "2" * 5000):
        status, headers, data = pv.raw("GET", path, headers={"Range": header})
        assert status == 416, (status, data[:120])
    status, body = pv.preview("log", range="9" * 5000 + "-")
    assert status == 422, (status, body)
    assert pv.api.get("/v1/health")[0] == 200


def test_a_1mb_malformed_shared_strings_part_extracts_in_under_half_a_second(tmp_path):
    from homa.preview import extract
    from tests.fixtures.preview import xlsx_malformed_shared_bytes
    path = tmp_path / "malformed.xlsx"
    path.write_bytes(xlsx_malformed_shared_bytes(1_000_000))
    started = time.perf_counter()
    try:
        with open(path, "rb") as fh:
            extract.extract_xlsx(fh)
    except extract.ExtractError:
        pass
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"{elapsed:.2f}s on 1 MB of unterminated <si><t> openers"


def test_a_malformed_workbook_over_http_answers_quickly(pv, tmp_path):
    from tests.fixtures.preview import xlsx_malformed_shared_bytes
    bad = pv.root / "docs" / "malformed-shared.xlsx"
    bad.write_bytes(xlsx_malformed_shared_bytes(1_000_000))
    assert wait_until(lambda: pv.preview(bad)[0] != 404, timeout=15), "new file never reached the index"
    started = time.perf_counter()
    status, body = pv.preview(bad)
    assert time.perf_counter() - started < 2.0
    assert status == 200 and body["kind"] in ("xlsx", "meta"), body
