"""`python -m assistant update`: the only update path (AC-IN-4, DEC 13). Nothing here runs unless the user
types the command; the app never checks for or downloads updates on its own."""
import datetime
import io
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional

from . import __version__, paths, settings

STATE_FILE = "state_update.json"
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
MAX_MEMBERS = 5000
MAX_REDIRECTS = 5
DOWNLOAD_TIMEOUT_S = 30.0
PROTECTED_DIR = ".assistant"
PIP_TIMEOUT_S = 900
GIT_TIMEOUT_S = 120


class UpdateError(Exception):
    pass


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError("the update server redirected to a non-https address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    handlers = [urllib.request.ProxyHandler({}), _HttpsOnlyRedirects()]
    try:
        import truststore
        handlers.append(urllib.request.HTTPSHandler(context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)))
    except Exception:
        pass
    return urllib.request.build_opener(*handlers)


def install_root() -> Path:
    return Path(__file__).resolve().parent.parent


def state_path() -> Path:
    return paths.home() / STATE_FILE


def _read_state() -> dict:
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(data: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def mark_updated() -> None:
    _write_state({"pending": True, "from": __version__})


def consume_notice() -> Optional[dict]:
    """Called once per start: the one-time 'Updated to <version>' notice, or None. Clears the marker."""
    state = _read_state()
    if state.get("pending") is not True:
        return None
    previous = state.get("from") if isinstance(state.get("from"), str) else ""
    try:
        _write_state({"pending": False, "last_seen": __version__})
    except OSError:
        return None
    detail = f"Was {previous}." if previous and previous != __version__ else "Files and dependencies refreshed."
    return {"title": f"Updated to {__version__}", "detail": detail}


def _run(argv: list, cwd: Optional[Path], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)


def _pip_argv(requirements: Path) -> list:
    argv = [sys.executable, "-m", "pip", "install", "-r", str(requirements), "--only-binary", ":all:"]
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        argv.insert(4, "--user")
    return argv


def _is_git_clone(root: Path) -> bool:
    return (root / ".git").exists()


def _pip_install(requirements: Path, dry_run: bool) -> None:
    argv = _pip_argv(requirements)
    print("pip: " + " ".join(argv))
    if dry_run:
        return
    done = _run(argv, requirements.parent, PIP_TIMEOUT_S)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-5:]
        raise UpdateError("pip could not install the pinned dependencies (no compiler and no admin rights are "
                          "used, so only prebuilt wheels work):\n" + "\n".join(tail))


def _head(git: str, root: Path) -> str:
    done = _run([git, "rev-parse", "HEAD"], root, GIT_TIMEOUT_S)
    return done.stdout.strip() if done.returncode == 0 else ""


def _update_git(root: Path, dry_run: bool) -> bool:
    git = shutil.which("git")
    if git is None:
        raise UpdateError("this folder is a git clone but git was not found on PATH")
    argv = [git, "pull", "--ff-only"]
    print("git: " + " ".join(["git", *argv[1:]]) + f"   (in {root})")
    changed = True
    if not dry_run:
        before = _head(git, root)
        done = _run(argv, root, GIT_TIMEOUT_S)
        sys.stdout.write(done.stdout or "")
        if done.returncode != 0:
            raise UpdateError("git pull --ff-only failed (local changes or a diverged branch?):\n"
                              + (done.stderr or "").strip()[-500:])
        after = _head(git, root)
        changed = not before or before != after
    _pip_install(root / "requirements.txt", dry_run)
    return changed


def _safe_members(archive: zipfile.ZipFile) -> list:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise UpdateError(f"the archive has more than {MAX_MEMBERS} entries")
    if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
        raise UpdateError("the archive would unpack to more than 200 MB")
    names = []
    for info in infos:
        name = info.filename.replace("\\", "/")
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or (pure.parts and ":" in pure.parts[0]):
            raise UpdateError("the archive contains a path that leaves the target folder")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise UpdateError("the archive contains a symbolic link")
        names.append((info, pure))
    return names


def _strip_top(members: list) -> int:
    if any(p.parts[:2] == ("assistant", "__init__.py") for _, p in members):
        return 0
    tops = {p.parts[0] for _, p in members if p.parts}
    return 1 if len(tops) == 1 and all(len(p.parts) > 1 or i.is_dir() for i, p in members) else 0


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"assistant-update/{__version__}"})
    try:
        with _opener().open(request, timeout=DOWNLOAD_TIMEOUT_S) as resp:
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_ZIP_BYTES:
                raise UpdateError("the release archive is larger than 50 MB; not downloading it")
            data = resp.read(MAX_ZIP_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"the update server answered {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"could not reach the update server ({type(exc).__name__})") from None
    if len(data) > MAX_ZIP_BYTES:
        raise UpdateError("the release archive is larger than 50 MB; not downloading it")
    return data


def _extract(data: bytes, root: Path) -> Path:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError("the download is not a zip file") from None
    with archive:
        members = _safe_members(archive)
        strip = _strip_top(members)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = root.parent / f"{root.name}-update-{stamp}"
        if target.exists():
            raise UpdateError(f"{target} already exists; not overwriting it")
        staging = Path(tempfile.mkdtemp(prefix="assistant-update-", dir=str(root.parent)))
        try:
            skipped = 0
            for info, pure in members:
                parts = pure.parts[strip:]
                if not parts:
                    continue
                if parts[0].lower() == PROTECTED_DIR:
                    skipped += 1
                    continue
                dest = staging.joinpath(*parts)
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
            if not (staging / "assistant" / "__init__.py").is_file():
                raise UpdateError("the archive does not contain the assistant package")
            if skipped:
                print(f"left out {skipped} entries under {PROTECTED_DIR}/ (your data is never overwritten)")
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return target


def _update_zip(root: Path, dry_run: bool, yes: bool = False) -> Optional[bool]:
    source = settings.peek_update_source()
    if not source:
        print("No update source is configured, so nothing was downloaded.\n"
              "This folder is not a git clone. To update from a release archive, set\n"
              '  "update": {"source": "https://.../assistant-release.zip"}\n'
              f"in {paths.settings_file()} and run this command again.\n"
              "It would then download that https address (size limit 50 MB, no proxy), unpack it next to this\n"
              f"folder as {root.name}-update-<date>, print the pip command for its requirements (it runs it only\n"
              f"with --yes), and leave {PROTECTED_DIR}/ alone.")
        return None
    print(f"download: {source}")
    print(f"unpack next to the install: {root.parent}{os.sep}{root.name}-update-<date>")
    if dry_run:
        _pip_install(root / "requirements.txt", True)
        if not yes:
            print("pip would not run automatically (pass --yes to allow it); the command is printed for you to run")
        return False
    data = _download(source)
    print(f"downloaded {len(data):,} bytes")
    target = _extract(data, root)
    print(f"unpacked to {target}")
    if yes:
        _pip_install(target / "requirements.txt", False)
    else:
        print("Review the unpacked files, then install its requirements yourself (or run this command again with --yes):")
        print("  " + " ".join(_pip_argv(target / "requirements.txt")))
    print(f"Start the new copy from {target} (run.cmd or python -m assistant); the old folder is untouched.")
    return True


def run(args) -> int:
    dry_run = bool(getattr(args, "dry_run", False))
    root = install_root()
    try:
        if _is_git_clone(root):
            changed = _update_git(root, dry_run)
        else:
            changed = _update_zip(root, dry_run, bool(getattr(args, "yes", False)))
    except UpdateError as exc:
        print(f"update failed: {exc}", file=sys.stderr)
        return 1
    except zipfile.BadZipFile:
        print("update failed: the download is not a valid zip file", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"update failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if changed is None:
        return 0 if dry_run else 2
    if dry_run:
        print("dry run: nothing was changed")
        return 0
    if not changed:
        print("Already up to date.")
        return 0
    try:
        mark_updated()
    except OSError:
        pass
    print("Updated. Restart Assistant to use it; it will show an 'Updated to <version>' card once.")
    return 0
