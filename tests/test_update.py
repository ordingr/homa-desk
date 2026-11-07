"""`python -m assistant update` (plan 7.5 M4-2; AC-IN-4, DEC 13).

Git route: a scratch bare repo plus two clones (the "install" and an author) in tmp_path, with `_pip_argv` pointed at a
shim script, so neither git pull nor pip ever touches the repository under test. Zip route: archives built in memory;
the network step is covered by a fake opener (https is enforced before any socket is opened, and there is no TLS test
server here). The CLI and the one-time notice card are driven through the real entry point / a private test app.
"""
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

from assistant import __version__, paths, update
from conftest import REPO_ROOT, Api, wait_until

GIT = ["git", "-c", "user.name=qa", "-c", "user.email=qa@example.test", "-c", "commit.gpgsign=false"]


@pytest.fixture(autouse=True)
def scratch_home(tmp_path, monkeypatch):
    home = tmp_path / "ahome"
    home.mkdir()
    monkeypatch.setenv("ASSISTANT_HOME", str(home))
    return home


def git(cwd, *args):
    done = subprocess.run([*GIT, *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (args, done.stdout, done.stderr)
    return done.stdout.strip()


def marker():
    path = update.state_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ---- git route ------------------------------------------------------------------------------------------------------------

@pytest.fixture
def clones(tmp_path, monkeypatch):
    """(install, author, pip_log): install is a clone whose origin is a bare repo; pip is a logging shim."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    author = tmp_path / "author"
    subprocess.run(["git", "clone", str(bare), str(author)], check=True, capture_output=True)
    (author / "assistant").mkdir()
    (author / "assistant" / "__init__.py").write_text("v = 1\n", encoding="utf-8")
    (author / "requirements.txt").write_text("# none\n", encoding="utf-8")
    git(author, "add", "-A")
    git(author, "commit", "-m", "one")
    git(author, "push", "-u", "origin", "HEAD:main")
    install = tmp_path / "install"
    subprocess.run(["git", "clone", str(bare), str(install)], check=True, capture_output=True)

    pip_log = tmp_path / "pip.log"
    shim = tmp_path / "pipshim.py"
    shim.write_text("import os, sys\nopen(os.environ['PIP_SHIM_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                    "sys.exit(int(os.environ.get('PIP_SHIM_RC', '0')))\n", encoding="utf-8")
    monkeypatch.setenv("PIP_SHIM_LOG", str(pip_log))
    monkeypatch.setattr(update, "install_root", lambda: install)
    monkeypatch.setattr(update, "_pip_argv", lambda req: [sys.executable, str(shim), "install", "-r", str(req)])
    return install, author, pip_log


def push_commit(author, text):
    (author / "assistant" / "__init__.py").write_text(text, encoding="utf-8")
    git(author, "commit", "-am", text.strip())
    git(author, "push", "origin", "HEAD:main")


def run_update(dry=False):
    return update.run(Namespace(dry_run=dry))


def test_git_pull_with_new_commit_installs_requirements_and_writes_the_marker(clones, capsys):
    install, author, pip_log = clones
    push_commit(author, "v = 2\n")
    assert run_update() == 0
    out = capsys.readouterr().out
    assert "git pull --ff-only" in out and "Updated." in out
    assert (install / "assistant" / "__init__.py").read_text(encoding="utf-8") == "v = 2\n"
    assert pip_log.read_text(encoding="utf-8").count("install -r") == 1 and "requirements.txt" in pip_log.read_text()
    assert marker() == {"pending": True, "from": __version__}


def test_git_pull_with_no_change_writes_no_marker(clones, capsys):
    install, author, pip_log = clones
    assert run_update() == 0
    assert "Already up to date." in capsys.readouterr().out
    assert marker() is None
    assert pip_log.exists(), "requirements are still reconciled on an up-to-date tree"


def test_dry_run_changes_nothing(clones, capsys):
    install, author, pip_log = clones
    push_commit(author, "v = 3\n")
    head = git(install, "rev-parse", "HEAD")
    assert run_update(dry=True) == 0
    out = capsys.readouterr().out
    assert "dry run: nothing was changed" in out and "git pull --ff-only" in out
    assert git(install, "rev-parse", "HEAD") == head and marker() is None and not pip_log.exists()


def test_diverged_branch_fails_with_exit_1_and_no_marker(clones, capsys):
    install, author, pip_log = clones
    (install / "assistant" / "local.txt").write_text("x", encoding="utf-8")
    git(install, "add", "-A")
    git(install, "commit", "-m", "local")
    push_commit(author, "v = 4\n")
    assert run_update() == 1
    assert "update failed: git pull --ff-only failed" in capsys.readouterr().err
    assert marker() is None and not pip_log.exists()


def test_pip_failure_is_exit_1_with_the_tail_and_no_marker(clones, capsys, monkeypatch):
    install, author, pip_log = clones
    push_commit(author, "v = 5\n")
    monkeypatch.setenv("PIP_SHIM_RC", "3")
    assert run_update() == 1
    assert "pip could not install the pinned dependencies" in capsys.readouterr().err
    assert marker() is None


def test_missing_git_is_a_clear_failure(clones, capsys, monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    assert run_update() == 1
    assert "git was not found" in capsys.readouterr().err


def test_pip_command_shape():
    argv = update._pip_argv(Path("req.txt"))
    assert argv[:4] == [sys.executable, "-m", "pip", "install"]
    assert "--only-binary" in argv and argv[argv.index("--only-binary") + 1] == ":all:"
    assert ("--user" in argv) == (sys.prefix == sys.base_prefix)


def test_pip_user_flag_only_outside_a_venv(monkeypatch):
    monkeypatch.setattr(sys, "prefix", "C:/venv")
    monkeypatch.setattr(sys, "base_prefix", "C:/base")
    assert "--user" not in update._pip_argv(Path("r.txt"))
    monkeypatch.setattr(sys, "prefix", "C:/base")
    assert "--user" in update._pip_argv(Path("r.txt"))


# ---- one-time notice ---------------------------------------------------------------------------------------------------------

def test_marker_yields_one_notice_and_is_cleared():
    assert update.consume_notice() is None
    update.mark_updated()
    first = update.consume_notice()
    assert first == {"title": f"Updated to {__version__}", "detail": "Files and dependencies refreshed."}
    assert update.consume_notice() is None
    assert marker() == {"pending": False, "last_seen": __version__}


def test_notice_mentions_the_previous_version_when_it_differs():
    update._write_state({"pending": True, "from": "0.0.1"})
    assert update.consume_notice()["detail"] == "Was 0.0.1."


@pytest.mark.parametrize("raw", ["not json", "[]", '{"pending": "yes"}', '{"pending": 1}', ""])
def test_garbage_marker_gives_no_notice(raw):
    update.state_path().write_text(raw, encoding="utf-8")
    assert update.consume_notice() is None


def test_live_app_shows_one_updated_card_and_clears_the_marker(app_factory, tmp_path):
    home = tmp_path / "notice_home"
    home.mkdir()
    (home / update.STATE_FILE).write_text(json.dumps({"pending": True, "from": "0.0.1"}), encoding="utf-8")
    with app_factory(test_mode=False, home=home) as (base, token):
        api = Api(base, token)

        def updated_cards():
            status, cards = api.get("/v1/cards")
            assert status == 200
            return [c for c in cards if c["id"] == "updated"]

        updated = wait_until(updated_cards, timeout=20)  # the HTTP server answers before the overlay is built
        assert len(updated) == 1 and updated[0]["title"] == f"Updated to {__version__}"
        assert json.loads((home / update.STATE_FILE).read_text(encoding="utf-8"))["pending"] is False
        Api(base, token).request("DELETE", "/v1/cards/updated")
    with app_factory(test_mode=False, home=home) as (base, token):
        log = home / "logs" / "app.log"
        assert wait_until(lambda: log.is_file() and log.read_text(encoding="utf-8").count("overlay ready") >= 2,
                          timeout=20), "second start never finished building the overlay"
        status, cards = Api(base, token).get("/v1/cards")
        assert status == 200 and not [c for c in cards if c["id"] == "updated"], "the notice must not come back"


# ---- no scheduled check (DEC 13) -------------------------------------------------------------------------------------------------

def test_nothing_schedules_an_update_check():
    sched = (REPO_ROOT / "assistant" / "scheduler.py").read_text(encoding="utf-8")
    assert not re.search(r"\bupdate\b", sched, re.IGNORECASE), "scheduler.py must not know about updates"
    app_src = (REPO_ROOT / "assistant" / "app.py").read_text(encoding="utf-8")
    uses = re.findall(r"\bupdate\.(\w+)", app_src)
    assert set(uses) == {"consume_notice"}, uses
    for path in (REPO_ROOT / "assistant").rglob("*.py"):
        if path.name in ("update.py", "__main__.py"):
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"update\.(run|_update_zip|_download)|assistant\.update|from \.+ import .*update",
                             text), path


# ---- zip route -----------------------------------------------------------------------------------------------------------------

def make_zip(entries, compression=zipfile.ZIP_STORED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, body in entries:
            if isinstance(body, zipfile.ZipInfo):
                z.writestr(body, "x")
            else:
                z.writestr(name, body)
    return buf.getvalue()


GOOD = [("assistant/__init__.py", "v = 9\n"), ("requirements.txt", "# none\n"), ("run.cmd", "echo\n")]


@pytest.fixture
def install_dir(tmp_path, monkeypatch):
    root = tmp_path / "inst" / "Assistant"
    root.mkdir(parents=True)
    (root / "marker.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(update, "install_root", lambda: root)
    return root


def set_source(value):
    paths.settings_file().write_text(json.dumps({"schema": 1, "update": {"source": value}}), encoding="utf-8")


def siblings(root):
    return sorted(p.name for p in root.parent.iterdir())


def test_zip_route_without_a_source_prints_the_dry_text_and_exits_2(install_dir, capsys):
    assert update.run(Namespace(dry_run=False)) == 2
    out = capsys.readouterr().out
    assert "No update source is configured" in out and "nothing was downloaded" in out
    assert siblings(install_dir) == ["Assistant"] and marker() is None


def test_zip_route_without_a_source_in_dry_run_is_exit_0(install_dir, capsys):
    assert update.run(Namespace(dry_run=True)) == 0
    assert "No update source is configured" in capsys.readouterr().out


@pytest.mark.parametrize("source", ["http://example.test/a.zip", "ftp://example.test/a.zip", "file:///c:/a.zip",
                                    "https://exa mple.test/a.zip", "//example.test/a.zip", "example.test/a.zip"])
def test_non_https_sources_are_treated_as_unset(install_dir, capsys, source):
    set_source(source)
    assert update.settings.peek_update_source() == ""
    assert update.run(Namespace(dry_run=False)) == 2
    assert "No update source is configured" in capsys.readouterr().out


def test_dry_run_with_a_source_names_it_and_downloads_nothing(install_dir, capsys, monkeypatch):
    set_source("https://example.test/rel.zip")
    monkeypatch.setattr(update, "_download", lambda url: pytest.fail("dry run must not download"))
    assert update.run(Namespace(dry_run=True)) == 0
    out = capsys.readouterr().out
    assert "download: https://example.test/rel.zip" in out and "dry run: nothing was changed" in out
    assert siblings(install_dir) == ["Assistant"]


def test_full_zip_update_unpacks_beside_the_install_and_leaves_the_old_one(install_dir, capsys, monkeypatch, tmp_path):
    set_source("https://example.test/rel.zip")
    data = make_zip(GOOD)
    monkeypatch.setattr(update, "_download", lambda url: data)
    seen = []
    monkeypatch.setattr(update, "_pip_install", lambda req, dry: seen.append((req, dry)))
    assert update.run(Namespace(dry_run=False)) == 0
    new = [n for n in siblings(install_dir) if n.startswith("Assistant-update-")]
    assert len(new) == 1
    target = install_dir.parent / new[0]
    assert (target / "assistant" / "__init__.py").read_text(encoding="utf-8") == "v = 9\n"
    assert (install_dir / "marker.txt").read_text(encoding="utf-8") == "old"
    assert seen == [], "pip is only run with --yes"
    assert marker() == {"pending": True, "from": __version__}
    assert not [n for n in siblings(install_dir) if n.startswith("assistant-update-")], "staging dir left behind"
    out = capsys.readouterr().out
    assert "Start the new copy from" in out and str(target / "requirements.txt") in out


def test_zip_update_without_yes_never_runs_pip_and_prints_the_command(install_dir, capsys, monkeypatch):
    set_source("https://example.test/rel.zip")
    monkeypatch.setattr(update, "_download", lambda url: make_zip(GOOD))
    monkeypatch.setattr(update, "_pip_install", lambda req, dry: pytest.fail("pip must not run without --yes"))
    ran = []
    monkeypatch.setattr(update, "_run", lambda *a, **k: ran.append(a) or pytest.fail("nothing may be spawned"))
    assert update.run(Namespace(dry_run=False)) == 0
    out = capsys.readouterr().out
    assert ran == []
    assert "install its requirements yourself" in out and "--yes" in out
    assert "-m pip install -r" in out and "requirements.txt" in out and "--only-binary :all:" in out
    assert marker() == {"pending": True, "from": __version__}


def test_zip_update_with_yes_runs_pip_once_on_the_new_copy(install_dir, capsys, monkeypatch):
    set_source("https://example.test/rel.zip")
    monkeypatch.setattr(update, "_download", lambda url: make_zip(GOOD))
    seen = []
    monkeypatch.setattr(update, "_pip_install", lambda req, dry: seen.append((req, dry)))
    assert update.run(Namespace(dry_run=False, yes=True)) == 0
    (target,) = [install_dir.parent / n for n in siblings(install_dir) if n.startswith("Assistant-update-")]
    assert seen == [(target / "requirements.txt", False)]
    assert "install its requirements yourself" not in capsys.readouterr().out


def test_zip_update_dry_run_never_runs_pip_even_with_yes(install_dir, capsys, monkeypatch):
    set_source("https://example.test/rel.zip")
    monkeypatch.setattr(update, "_download", lambda url: pytest.fail("dry run must not download"))
    ran = []
    monkeypatch.setattr(update, "_run", lambda *a, **k: ran.append(a))
    assert update.run(Namespace(dry_run=True, yes=True)) == 0
    out = capsys.readouterr().out
    assert ran == [] and "pip: " in out and "dry run: nothing was changed" in out
    assert siblings(install_dir) == ["Assistant"]
    assert update.run(Namespace(dry_run=True)) == 0
    assert "pass --yes to allow it" in capsys.readouterr().out
    assert ran == []


def test_a_corrupt_member_is_a_clean_failure_with_no_leftovers(install_dir, capsys, monkeypatch):
    set_source("https://example.test/rel.zip")
    raw = bytearray(make_zip(GOOD))
    at = raw.index(b"v = 9")
    raw[at:at + 5] = b"v = 8"  # same size, so only the stored CRC no longer matches
    monkeypatch.setattr(update, "_download", lambda url: bytes(raw))
    monkeypatch.setattr(update, "_pip_install", lambda req, dry: pytest.fail("pip must not run"))
    assert update.run(Namespace(dry_run=False, yes=True)) == 1
    err = capsys.readouterr().err
    assert err.strip() == "update failed: the download is not a valid zip file", err
    assert siblings(install_dir) == ["Assistant"], "neither a target nor a staging folder is left"
    assert marker() is None


def test_a_truncated_download_is_a_clean_failure(install_dir, capsys, monkeypatch):
    set_source("https://example.test/rel.zip")
    monkeypatch.setattr(update, "_download", lambda url: make_zip(GOOD)[:40])
    assert update.run(Namespace(dry_run=False)) == 1
    assert "update failed:" in capsys.readouterr().err and siblings(install_dir) == ["Assistant"]


def test_the_git_route_runs_the_resolved_git_path_not_the_bare_name(clones, monkeypatch):
    install, author, pip_log = clones
    real = update.shutil.which("git")
    resolved = real.replace("/", "\\")
    monkeypatch.setattr(update.shutil, "which", lambda name: resolved if name == "git" else None)
    calls = []
    original = update._run

    def spy(argv, cwd, timeout):
        calls.append(list(argv))
        return original(argv, cwd, timeout)
    monkeypatch.setattr(update, "_run", spy)
    push_commit(author, "v = 6\n")
    assert run_update() == 0
    git_calls = [c for c in calls if Path(c[0]).name.lower().startswith("git")]
    assert len(git_calls) == 3, calls
    assert [c[1] for c in git_calls] == ["rev-parse", "pull", "rev-parse"]
    assert all(c[0] == resolved for c in git_calls), "every git call (including rev-parse HEAD) uses the resolved path"


def test_cli_accepts_yes_and_help_describes_it():
    done = subprocess.run([sys.executable, "-m", "assistant", "update", "--help"], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=60)
    assert "--yes" in done.stdout and "pip" in done.stdout


def test_cli_dry_run_with_yes_through_the_real_entry_point_changes_nothing(tmp_path):
    home = tmp_path / "cli_home2"
    home.mkdir()
    env = dict(os.environ, ASSISTANT_HOME=str(home))
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout
    done = subprocess.run([sys.executable, "-m", "assistant", "update", "--dry-run", "--yes"], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=60)
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert "dry run: nothing was changed" in done.stdout and before == after


def extract(tmp_path, entries, **kw):
    root = tmp_path / "x" / "Assistant"
    root.mkdir(parents=True)
    return root, update._extract(make_zip(entries, **kw), root)


def test_wrapper_folder_is_stripped_on_extract(tmp_path):
    root, target = extract(tmp_path, [("rel-1.2/" + n, b) for n, b in GOOD])
    assert (target / "assistant" / "__init__.py").is_file() and (target / "run.cmd").is_file()
    assert not (target / "rel-1.2").exists()


def test_flat_archive_is_not_stripped(tmp_path):
    root, target = extract(tmp_path, GOOD)
    assert (target / "assistant" / "__init__.py").is_file() and (target / "requirements.txt").is_file()


def test_dot_assistant_folder_is_skipped_at_any_wrapper_depth(tmp_path, capsys):
    entries = GOOD + [(".assistant/token", "SECRET"), (".ASSISTANT/settings.json", "{}")]
    root, target = extract(tmp_path, entries)
    assert not (target / ".assistant").exists()
    assert "left out 2 entries" in capsys.readouterr().out
    root2 = tmp_path / "y" / "Assistant"
    root2.mkdir(parents=True)
    t2 = update._extract(make_zip([("rel/" + n, b) for n, b in entries]), root2)
    assert not (t2 / ".assistant").exists() and (t2 / "assistant" / "__init__.py").is_file()


@pytest.mark.parametrize("name", ["../evil.txt", "assistant/../../evil.txt", "..\\evil.txt", "/abs/evil.txt",
                                  "\\abs\\evil.txt", "C:/evil.txt", "C:\\Windows\\evil.txt", "D:evil.txt"])
def test_zip_slip_absolute_and_drive_paths_are_refused(tmp_path, name):
    root = tmp_path / "x" / "Assistant"
    root.mkdir(parents=True)
    with pytest.raises(update.UpdateError, match="leaves the target folder"):
        update._extract(make_zip(GOOD + [(name, "boom")]), root)
    assert siblings(root) == ["Assistant"]
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "x" / "evil.txt").exists()


def test_symlink_entries_are_refused(tmp_path):
    info = zipfile.ZipInfo("assistant/link")
    info.external_attr = (0o120777 << 16)
    root = tmp_path / "x" / "Assistant"
    root.mkdir(parents=True)
    with pytest.raises(update.UpdateError, match="symbolic link"):
        update._extract(make_zip(GOOD + [("assistant/link", info)]), root)
    assert siblings(root) == ["Assistant"]


def test_more_than_5000_entries_is_refused(tmp_path):
    entries = GOOD + [(f"assistant/f{i}.txt", "") for i in range(update.MAX_MEMBERS)]
    root = tmp_path / "x" / "Assistant"
    root.mkdir(parents=True)
    with pytest.raises(update.UpdateError, match="more than 5000 entries"):
        update._extract(make_zip(entries), root)
    assert siblings(root) == ["Assistant"]


def test_exactly_5000_entries_is_accepted(tmp_path):
    entries = GOOD + [(f"assistant/f{i}.txt", "") for i in range(update.MAX_MEMBERS - len(GOOD))]
    assert len(entries) == update.MAX_MEMBERS
    root, target = extract(tmp_path, entries)
    assert (target / "assistant" / "f0.txt").is_file()


def test_unpacked_size_over_200_mb_is_refused(tmp_path):
    big = [("assistant/blob.bin", b"\0" * (101 * 1024 * 1024)), ("assistant/blob2.bin", b"\0" * (101 * 1024 * 1024))]
    data = make_zip(GOOD + big, compression=zipfile.ZIP_DEFLATED)
    assert len(data) < update.MAX_ZIP_BYTES
    root = tmp_path / "x" / "Assistant"
    root.mkdir(parents=True)
    with pytest.raises(update.UpdateError, match="200 MB"):
        update._extract(data, root)
    assert siblings(root) == ["Assistant"]


def test_archive_without_the_package_is_refused_and_cleaned_up(tmp_path):
    root = tmp_path / "x" / "Assistant"
    root.mkdir(parents=True)
    with pytest.raises(update.UpdateError, match="does not contain the assistant package"):
        update._extract(make_zip([("readme.txt", "hi"), ("other/__init__.py", "")]), root)
    assert siblings(root) == ["Assistant"], "staging folder must be removed"


def test_not_a_zip_is_refused(tmp_path):
    root = tmp_path / "x" / "Assistant"
    root.mkdir(parents=True)
    with pytest.raises(update.UpdateError, match="not a zip"):
        update._extract(b"<html>login</html>", root)


def test_existing_target_is_never_overwritten(tmp_path, monkeypatch):
    root = tmp_path / "x" / "Assistant"
    root.mkdir(parents=True)

    class Frozen(update.datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2030, 1, 2, 3, 4, 5)

    monkeypatch.setattr(update.datetime, "datetime", Frozen)
    keep = tmp_path / "x" / "Assistant-update-20300102-030405"
    keep.mkdir()
    (keep / "mine.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(update.UpdateError, match="already exists"):
        update._extract(make_zip(GOOD), root)
    assert (keep / "mine.txt").read_text(encoding="utf-8") == "mine" and siblings(root) == ["Assistant", keep.name]


# ---- network guards -----------------------------------------------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, body=b"", declared=None):
        self._body = body
        self.headers = {} if declared is None else {"Content-Length": str(declared)}
        self.read_sizes = []

    def read(self, n=-1):
        self.read_sizes.append(n)
        return self._body[:n] if n >= 0 else self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


def test_declared_oversize_is_refused_without_reading(monkeypatch):
    resp = FakeResponse(b"x", declared=update.MAX_ZIP_BYTES + 1)
    monkeypatch.setattr(update, "_opener", lambda: FakeOpener(resp))
    with pytest.raises(update.UpdateError, match="larger than 50 MB"):
        update._download("https://example.test/a.zip")
    assert resp.read_sizes == []


def test_streamed_oversize_without_a_declared_length_is_refused(monkeypatch):
    resp = FakeResponse(b"x" * (update.MAX_ZIP_BYTES + 5))
    monkeypatch.setattr(update, "_opener", lambda: FakeOpener(resp))
    with pytest.raises(update.UpdateError, match="larger than 50 MB"):
        update._download("https://example.test/a.zip")
    assert resp.read_sizes == [update.MAX_ZIP_BYTES + 1], "the read itself must be capped"


def test_a_lying_small_content_length_does_not_bypass_the_cap(monkeypatch):
    resp = FakeResponse(b"x" * (update.MAX_ZIP_BYTES + 5), declared=10)
    monkeypatch.setattr(update, "_opener", lambda: FakeOpener(resp))
    with pytest.raises(update.UpdateError, match="larger than 50 MB"):
        update._download("https://example.test/a.zip")


def test_exactly_the_cap_is_accepted(monkeypatch):
    resp = FakeResponse(b"x" * update.MAX_ZIP_BYTES, declared=update.MAX_ZIP_BYTES)
    opener = FakeOpener(resp)
    monkeypatch.setattr(update, "_opener", lambda: opener)
    assert len(update._download("https://example.test/a.zip")) == update.MAX_ZIP_BYTES
    assert opener.requests[0][1] == update.DOWNLOAD_TIMEOUT_S


@pytest.mark.parametrize("error,text", [
    (urllib.error.HTTPError("https://example.test/a.zip", 404, "nf", {}, None), "answered 404"),
    (urllib.error.URLError("boom"), "could not reach the update server"),
    (TimeoutError("slow"), "could not reach the update server"),
])
def test_network_errors_become_update_errors_without_leaking_urls(monkeypatch, error, text):
    monkeypatch.setattr(update, "_opener", lambda: FakeOpener(error=error))
    with pytest.raises(update.UpdateError, match=text) as info:
        update._download("https://example.test/secret-token-path.zip")
    assert "secret-token-path" not in str(info.value)


def test_opener_ignores_proxy_environment_and_has_https_only_redirects(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    opener = update._opener()
    # An empty ProxyHandler registers no *_open methods, so build_opener drops it and also skips the default
    # env-reading one: no handler in the chain can route through a proxy.
    assert not [h for h in opener.handlers if isinstance(h, update.urllib.request.ProxyHandler)]
    assert any(isinstance(h, update._HttpsOnlyRedirects) for h in opener.handlers)


def test_redirect_to_http_is_refused_and_to_https_is_followed():
    handler = update._HttpsOnlyRedirects()
    request = update.urllib.request.Request("https://example.test/a.zip")
    with pytest.raises(urllib.error.URLError, match="non-https"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://example.test/b.zip")
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(request, None, 302, "Found", {}, "ftp://example.test/b.zip")
    follow = handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, "https://other.test/b.zip")
    assert follow is not None and follow.full_url == "https://other.test/b.zip"
    assert handler.max_redirections == update.MAX_REDIRECTS == 5


# ---- CLI wiring ------------------------------------------------------------------------------------------------------------------

def test_cli_dry_run_through_the_real_entry_point(tmp_path):
    home = tmp_path / "cli_home"
    home.mkdir()
    env = dict(os.environ, ASSISTANT_HOME=str(home))
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout
    done = subprocess.run([sys.executable, "-m", "assistant", "update", "--dry-run"], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=60)
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert "dry run: nothing was changed" in done.stdout
    assert "--only-binary :all:" in done.stdout
    assert before == after and not (home / update.STATE_FILE).exists()


def test_cli_help_lists_update():
    done = subprocess.run([sys.executable, "-m", "assistant", "update", "--help"], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0 and "--dry-run" in done.stdout
