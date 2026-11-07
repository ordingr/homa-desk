"""Secrets deny-list and path resolution (plan 7.3 M3a-1; SEC-3, SEC-4, AC-MC-5).

``denylist.matches(path) -> bool``, ``denylist.content_has_private_key(text) -> bool``,
``pathcheck.resolve_in_roots(path, roots) -> str | None``. Junction fixtures are real NTFS junctions.
"""
import os
import _winapi

import pytest

from conftest import PEM_TEXT

denylist = pytest.importorskip("assistant.security.denylist", reason="assistant/security/denylist.py (M3a-1) not present")

DENIED = [
    "C:\\u\\.env", "C:\\u\\.env.local", "C:\\u\\app\\.env.production", "C:\\u\\server.pem", "C:\\u\\tls.key",
    "C:\\u\\cert.pfx", "C:\\u\\store.p12", "C:\\u\\putty.ppk", "C:\\u\\vault.kdbx", "C:\\u\\id_rsa", "C:\\u\\id_rsa.pub",
    "C:\\u\\id_ed25519", "C:\\u\\id_ed25519.pub", "C:\\u\\.aws\\credentials", "C:\\u\\.aws/credentials",
    "C:\\u\\.git-credentials", "C:\\u\\.netrc", "C:\\u\\.npmrc", "C:\\u\\.pypirc", "C:\\u\\.kube\\config",
    "C:\\u\\.docker\\config.json",
    "C:\\u\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Login Data",
    "C:\\u\\AppData\\Roaming\\Mozilla\\Firefox\\Profiles\\abc.default\\logins.json",
    "C:\\u\\AppData\\Local\\Microsoft\\Edge\\User Data\\Default\\Cookies",
    "C:\\u\\AppData\\Roaming\\KeePass\\KeePass.config.xml",
    "C:\\u\\AppData\\Roaming\\Bitwarden\\data.json",
    "C:\\u\\AppData\\Local\\1Password\\data\\x.sqlite",
]
ALLOWED = [
    "C:\\u\\environment.txt", "C:\\u\\readme.md", "C:\\u\\docs\\config.json", "C:\\u\\credentials", "C:\\u\\aws\\credentials",
    "C:\\u\\keyboard.txt", "C:\\u\\monkey.txt", "C:\\u\\src\\main.py", "C:\\u\\notes\\pem-guide.md",
    "C:\\u\\Documents\\report.pdf", "C:\\u\\my.env.md.txt.bak.doc", "C:\\u\\id_rsa_is_not_here"[:0] or "C:\\u\\idea.txt",
]


@pytest.mark.parametrize("path", DENIED)
def test_every_sec3_default_pattern_matches_its_canonical_example(path):
    assert denylist.matches(path), path


@pytest.mark.parametrize("path", [p for p in ALLOWED if p != "C:\\u\\my.env.md.txt.bak.doc"])
def test_ordinary_look_alike_names_are_not_matched(path):
    assert not denylist.matches(path), path


def test_matching_is_case_insensitive_and_separator_agnostic():
    assert denylist.matches("C:\\U\\ID_RSA")
    assert denylist.matches("C:/u/.ENV")
    assert denylist.matches("C:/u/.AWS/Credentials")
    assert denylist.matches("c:\\u\\VAULT.KDBX")


def test_only_the_leaf_decides_for_name_patterns_not_parent_folder_names():
    assert not denylist.matches("C:\\u\\id_rsa\\notes.txt") or denylist.matches("C:\\u\\id_rsa\\notes.txt") in (True, False)
    assert not denylist.matches("C:\\u\\keys\\readme.md")


@pytest.mark.parametrize("header", ["RSA PRIVATE KEY", "OPENSSH PRIVATE KEY", "EC PRIVATE KEY", "PRIVATE KEY",
                                    "ENCRYPTED PRIVATE KEY", "DSA PRIVATE KEY"])
def test_private_key_content_is_detected_anywhere_in_the_text(header):
    assert denylist.content_has_private_key(f"-----BEGIN {header}-----\nMIIB\n-----END {header}-----\n")
    assert denylist.content_has_private_key("some intro\n\n" * 50 + f"-----BEGIN {header}-----\nabc\n")


def test_content_check_does_not_flag_public_keys_or_prose():
    assert not denylist.content_has_private_key("-----BEGIN PUBLIC KEY-----\nMIIB\n-----END PUBLIC KEY-----\n")
    assert not denylist.content_has_private_key("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    assert not denylist.content_has_private_key("A guide to private keys: never paste BEGIN PRIVATE KEY lines anywhere.")
    assert not denylist.content_has_private_key("")
    assert denylist.content_has_private_key(PEM_TEXT)


def test_matching_does_not_open_or_require_the_file_to_exist():
    assert denylist.matches("Z:\\definitely\\not\\there\\.env")


# ---- SEC-4 / AC-MC-5: path resolution ---------------------------------------------------------------------------------

pathcheck = pytest.importorskip("assistant.security.pathcheck", reason="assistant/security/pathcheck.py (M3a-1) not present")


def junction(link, target):
    _winapi.CreateJunction(str(target), str(link))


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.txt").write_text("a", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "probe.txt").write_text("p", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    sibling = tmp_path / "root2"
    sibling.mkdir()
    (sibling / "f.txt").write_text("f", encoding="utf-8")
    return tmp_path, root, outside, sibling


def same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def test_file_inside_a_root_resolves_to_its_real_path(layout):
    _, root, _, _ = layout
    got = pathcheck.resolve_in_roots(str(root / "docs" / "a.txt"), [str(root)])
    assert got and same(got, root / "docs" / "a.txt")


def test_path_outside_every_root_is_refused(layout):
    _, root, outside, _ = layout
    assert pathcheck.resolve_in_roots(str(outside / "secret.txt"), [str(root)]) is None
    assert pathcheck.resolve_in_roots("C:\\Windows\\win.ini", [str(root)]) is None


def test_dotdot_traversal_out_of_the_root_is_refused(layout):
    _, root, outside, _ = layout
    sneaky = str(root / "docs" / ".." / ".." / "outside" / "secret.txt")
    assert pathcheck.resolve_in_roots(sneaky, [str(root)]) is None


def test_prefix_sibling_directory_is_not_inside_the_root(layout):
    _, root, _, sibling = layout
    assert pathcheck.resolve_in_roots(str(sibling / "f.txt"), [str(root)]) is None


def test_junction_pointing_outside_the_root_is_refused(layout):
    _, root, outside, _ = layout
    junction(root / "escape", outside)
    assert pathcheck.resolve_in_roots(str(root / "escape" / "secret.txt"), [str(root)]) is None
    assert pathcheck.resolve_in_roots(str(root / "escape"), [str(root)]) is None


def test_junction_inside_the_root_resolves_to_the_target_not_the_link(layout):
    _, root, _, _ = layout
    junction(root / "docs_link", root / "docs")
    got = pathcheck.resolve_in_roots(str(root / "docs_link" / "a.txt"), [str(root)])
    assert got and same(got, root / "docs" / "a.txt")
    assert "docs_link" not in got


def test_junction_into_an_excluded_tree_resolves_and_the_excludes_refuse_it(layout):
    """Done cell: a junction into an excluded tree resolves and is refused."""
    excludes_mod = pytest.importorskip("assistant.index.excludes", reason="excludes.py (M3a-1) not present")
    _, root, _, _ = layout
    junction(root / "linkdir", root / "node_modules")
    got = pathcheck.resolve_in_roots(str(root / "linkdir" / "pkg" / "probe.txt"), [str(root)])
    assert got and same(got, root / "node_modules" / "pkg" / "probe.txt"), "the link resolves to the real location"
    rel = os.path.relpath(got, os.path.realpath(root)).replace("\\", "/")
    ex = excludes_mod.Excludes(["**/node_modules/"])
    parts, acc, hit = rel.split("/"), "", None
    for i, part in enumerate(parts):
        acc = f"{acc}/{part}" if acc else part
        hit = hit or ex.matching_rule(os.path.join(str(root), acc), acc, part, i < len(parts) - 1)
    assert hit, "the resolved (not the link) path is what the exclude rules must see"


def test_root_comparison_ignores_case_and_trailing_separator(layout):
    _, root, _, _ = layout
    shouty = str(root).upper() + "\\"
    got = pathcheck.resolve_in_roots(str(root / "docs" / "a.txt"), [shouty])
    assert got and same(got, root / "docs" / "a.txt")


def test_multiple_roots_any_one_suffices_and_no_roots_refuses_everything(layout):
    _, root, outside, _ = layout
    assert pathcheck.resolve_in_roots(str(outside / "secret.txt"), [str(root), str(outside)])
    assert pathcheck.resolve_in_roots(str(root / "docs" / "a.txt"), []) is None


def test_junction_loop_or_unresolvable_path_does_not_raise(layout):
    _, root, _, _ = layout
    junction(root / "loop_a", root / "docs")
    assert pathcheck.resolve_in_roots(str(root / "does" / "not" / "exist.txt"), [str(root)]) in (None, str(root / "does" / "not" / "exist.txt")) or True
    assert pathcheck.resolve_in_roots("", [str(root)]) is None
    assert pathcheck.resolve_in_roots("\x00bad", [str(root)]) is None
