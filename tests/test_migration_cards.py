"""The two start-up cards the rename adds (wired in homa/app.py _show_migration_cards), seen through a real scratch app:
the one-time "Assistant is now Homa" notice and the hooks-point-at-another-port warning."""
import json

from homa import hooks_installer

from conftest import _free_port, launch_app, wait_until

def fetch(base_url: str, token: str) -> dict:
    import http.client
    port = int(base_url.rsplit(":", 1)[1])
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", "/v1/cards", headers={"Authorization": f"Bearer {token}"})
        body = json.loads(conn.getresponse().read())
    finally:
        conn.close()
    return {c["id"]: c for c in body}


def test_migration_notice_becomes_one_sticky_card_once(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "migration-notice.json").write_text(
        json.dumps({"shown": False, "title": "Assistant is now Homa", "detail": "Your data moved."}), encoding="utf-8")
    with launch_app(home, _free_port(), test_mode=True) as (base, token):
        assert wait_until(lambda: "migrated" in fetch(base, token), timeout=15)
        card = fetch(base, token)["migrated"]
        assert card["title"] == "Assistant is now Homa" and card["sticky"] is True
    assert json.loads((home / "migration-notice.json").read_text())["shown"] is True


def test_hooks_at_a_different_port_post_a_sticky_warning_card(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    port = _free_port()
    fake_user = home.parent / (home.name + "_userprofile")
    settings = fake_user / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    handler = {"type": "http", "url": f"http://127.0.0.1:{port + 1}{hooks_installer.ROUTE}",
               "headers": {"Authorization": "Bearer x"}, "onFailure": "continue"}
    settings.write_text(json.dumps({"hooks": {e: [{"hooks": [handler]}] for e in hooks_installer.EVENTS}}),
                        encoding="utf-8")
    before = settings.read_bytes()
    with launch_app(home, port, test_mode=True) as (base, token):
        assert wait_until(lambda: "hooks-mismatch" in fetch(base, token), timeout=15)
        card = fetch(base, token)["hooks-mismatch"]
        assert f"point at port {port + 1}" in card["title"] and f"listens on {port}" in card["title"]
        assert card["sticky"] is True and "re-install" in card["detail"]
    assert settings.read_bytes() == before, "the start-up check is read-only"
