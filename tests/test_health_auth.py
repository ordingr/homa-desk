import http.client
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from conftest import REPO_ROOT

HEALTH = "/v1/health"


def _get(base_url, headers=None, host=None):
    """GET /v1/health, optionally connecting to ``host`` (e.g. 'localhost') instead of the loopback IP."""
    parts = urlsplit(base_url)
    conn = http.client.HTTPConnection(host or parts.hostname, parts.port, timeout=10)
    try:
        conn.request("GET", HEALTH, headers=headers or {})
        response = conn.getresponse()
        raw = response.read()
        return response.status, raw
    finally:
        conn.close()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_health_with_token_returns_version(running_app):
    base_url, token = running_app
    status, raw = _get(base_url, _bearer(token))
    body = json.loads(raw)
    assert status == 200
    assert body["status"] == "ok"
    assert re.fullmatch(r"\d+\.\d+\.\d+", body["version"])


def test_health_via_localhost_name(running_app):
    base_url, token = running_app
    status, _ = _get(base_url, _bearer(token), host="localhost")
    assert status == 200


def test_health_without_token_is_401(running_app):
    base_url, _ = running_app
    assert _get(base_url)[0] == 401


def test_health_wrong_token_is_401(running_app):
    base_url, token = running_app
    assert _get(base_url, _bearer("x" * len(token)))[0] == 401
    assert _get(base_url, _bearer(token[:-1]))[0] == 401


def test_health_non_bearer_scheme_is_401(running_app):
    base_url, token = running_app
    assert _get(base_url, {"Authorization": f"Basic {token}"})[0] == 401


def test_foreign_host_header_is_403_even_with_valid_token(running_app):
    base_url, token = running_app
    headers = _bearer(token)
    headers["Host"] = "evil.example"
    assert _get(base_url, headers)[0] == 403


def test_foreign_origin_is_403_even_with_valid_token(running_app):
    base_url, token = running_app
    headers = _bearer(token)
    headers["Origin"] = "http://evil.example"
    assert _get(base_url, headers)[0] == 403


def test_local_origin_is_allowed(running_app):
    base_url, token = running_app
    headers = _bearer(token)
    headers["Origin"] = "http://localhost:3000"
    assert _get(base_url, headers)[0] == 200


def test_foreign_host_is_403_without_token_too(running_app):
    """Host check runs before the token check: no 401/403 oracle for a rebinding page."""
    base_url, _ = running_app
    assert _get(base_url, {"Host": "evil.example"})[0] == 403


def _icacls_aces(path: Path):
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True, timeout=30, check=True).stdout
    aces = []
    for line in out.splitlines():
        if not line.strip():
            break
        line = line[len(str(path)):] if line.startswith(str(path)) else line
        aces.append(line.strip())
    return aces


def test_token_file_acl_is_current_user_only(running_app, assistant_home):
    token_path = assistant_home / "token"
    me = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip().lower()
    aces = _icacls_aces(token_path)
    assert len(aces) == 1, f"expected exactly one ACE, got {aces}"
    principal, _, rights = aces[0].rpartition(":")
    assert principal.lower() == me
    assert "(I)" not in rights, "inherited ACE present"
    assert "(F)" in rights


def test_doctor_output_never_contains_token(running_app, assistant_env, assistant_home):
    _, token = running_app
    result = subprocess.run([sys.executable, "-m", "assistant", "--doctor"], cwd=REPO_ROOT,
                            env=assistant_env, capture_output=True, text=True, timeout=60)
    combined = result.stdout + result.stderr
    assert token not in combined
    assert re.search(r"^token file\s+present$", result.stdout, re.M)
    assert re.search(r"^python\s+3\.\d+\.\d+$", result.stdout, re.M)
    assert re.search(r"^port\s+\d+ in use$", result.stdout, re.M)
    assert re.search(r"^Outlook COM\s+n/a$", result.stdout, re.M)


def test_app_log_never_contains_token(running_app, assistant_home):
    _get(running_app[0])  # produce at least one rejected-request log line
    _get(running_app[0], _bearer(running_app[1]))
    log_text = (assistant_home / "logs" / "app.log").read_text(encoding="utf-8")
    assert "starting assistant" in log_text
    assert running_app[1] not in log_text


def test_second_instance_on_same_port_exits_nonzero(running_app, assistant_port, tmp_path):
    env = dict(__import__("os").environ, ASSISTANT_HOME=str(tmp_path))
    (tmp_path / "settings.json").write_text(json.dumps({"schema": 1, "port": assistant_port}))
    result = subprocess.run([sys.executable, "-m", "assistant"], cwd=REPO_ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert "could not start the HTTP server" in result.stderr


def test_corrupt_settings_falls_back_to_defaults_and_is_left_alone(tmp_path):
    """Doctor only (no second server): a corrupt settings.json must not crash and must not be overwritten."""
    env = dict(__import__("os").environ, ASSISTANT_HOME=str(tmp_path))
    (tmp_path / "settings.json").write_text("{not json")
    result = subprocess.run([sys.executable, "-m", "assistant", "--doctor"], cwd=REPO_ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode in (0, 1)
    assert re.search(r"^port\s+17171 ", result.stdout, re.M)
    assert (tmp_path / "settings.json").read_text() == "{not json"
