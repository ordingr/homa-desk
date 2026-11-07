"""Exclude rules (plan 7.3 M3a-1; AC-FI-1, AC-FI-7; 15.3 items 3 and 4).

Contract used by the crawler and the manager (assistant/index/crawler.py docstring):
``Excludes(patterns)`` and ``matching_rule(full, rel, name, is_dir) -> rule label | None``, called once per path
segment while descending, ``rel`` being the forward-slash path below the root. ``excluded()`` below replays that
descent, so the tests exercise the rules the way the consumers do.
"""
import os
import re

import pytest

excludes_mod = pytest.importorskip("assistant.index.excludes", reason="assistant/index/excludes.py (M3a-1) not present")
Excludes = excludes_mod.Excludes
ROOT = "C:\\root"


def default_patterns():
    try:
        from assistant.settings import DEFAULTS
        found = DEFAULTS["index"]["excludes"]
    except (ImportError, KeyError, TypeError):
        found = getattr(excludes_mod, "DEFAULT_EXCLUDES", None) or getattr(excludes_mod, "DEFAULTS", None)
    if not found:
        pytest.skip("no default exclude list exported yet (settings.DEFAULTS['index']['excludes'] or excludes.DEFAULT_EXCLUDES)")
    return list(found)


def excluded(ex, rel: str, is_dir: bool = False):
    """The first rule that excludes ``rel`` or one of its parent directories, as the manager walks it."""
    parts = rel.strip("/").split("/")
    acc, base = "", ROOT
    for i, part in enumerate(parts):
        acc = f"{acc}/{part}" if acc else part
        base = f"{base}\\{part}"
        hit = ex.matching_rule(base, acc, part, True if i < len(parts) - 1 else is_dir)
        if hit:
            return hit
    return None


# ---- rule syntax --------------------------------------------------------------------------------------------

def test_named_rules_from_the_plan_exclude_what_they_say():
    ex = Excludes(["**/node_modules/", "$RECYCLE.BIN/", "*.vhdx"])
    assert excluded(ex, "node_modules", True)
    assert excluded(ex, "app/web/node_modules", True)
    assert excluded(ex, "app/node_modules/pkg/index.js"), "children of an excluded directory are excluded"
    assert excluded(ex, "$RECYCLE.BIN", True)
    assert excluded(ex, "$RECYCLE.BIN/S-1-5-21-1/a.txt")
    assert excluded(ex, "disk.vhdx") and excluded(ex, "vm/deep/disk2.vhdx")
    assert not excluded(ex, "src/main.py")
    assert not excluded(ex, "my_node_modules_notes.txt")


def test_trailing_slash_rule_matches_directories_only():
    ex = Excludes(["build/"])
    assert excluded(ex, "build", True)
    assert excluded(ex, "pkg/build", True)
    assert not excluded(ex, "build", False), "a file named build is not a directory rule hit"
    plain = Excludes(["build"])
    assert excluded(plain, "build", False), "without the slash a file is matched too"


def test_matching_is_case_insensitive_like_ntfs():
    ex = Excludes(["**/node_modules/", "*.vhdx"])
    assert excluded(ex, "Node_Modules", True)
    assert excluded(ex, "DISK.VHDX")


def test_each_hit_reports_which_rule_so_per_rule_counts_are_possible():
    ex = Excludes(["**/node_modules/", "*.vhdx"])
    a, b = excluded(ex, "x/node_modules", True), excluded(ex, "d.vhdx")
    assert a and b and a != b and len({a, b}) == 2
    assert excluded(ex, "other/node_modules", True) == a, "the same rule yields the same label every time"


def test_empty_blank_and_comment_lines_are_ignored_not_match_everything():
    ex = Excludes(["", "   ", "# a comment", "*.vhdx"])
    assert not excluded(ex, "readme.md")
    assert not excluded(ex, "# a comment")
    assert excluded(ex, "d.vhdx")


# ---- 15.3 item 4: how "*" treats "/" (pinned) ---------------------------------------------------------------------

def test_star_semantics_are_declared_in_the_docstring_and_the_matcher_agrees():
    doc = (excludes_mod.__doc__ or "").lower()
    assert re.search(r"\*[^.\n]{0,40}(crosses|does not cross|stays within)", doc) or "cross" in doc, \
        "15.3 item 4: the module docstring must state whether '*' crosses '/'"
    crosses = "does not cross" not in doc and "stays within" not in doc
    ex = Excludes(["a/*/c/"])
    one_level = excluded(ex, "a/b/c", True)
    deep = excluded(ex, "a/b/x/c", True)
    assert one_level, "one-level case: a/*/c/ must match a/b/c"
    assert bool(deep) == crosses, f"docstring says crossing={crosses} but a/b/x/c matched={bool(deep)}"


def test_office_cache_path_is_excluded_at_depth_by_the_defaults():
    ex = Excludes(default_patterns())
    assert excluded(ex, "AppData/Local/Microsoft/Office/16.0/OfficeFileCache", True)
    assert excluded(ex, "AppData/Local/Microsoft/Office/16.0/OfficeFileCache/cache.bin")


# ---- 15.3 item 3: default list categories ------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "AppData/Local/App/Code Cache",
    "AppData/Local/App/Cache",
    "AppData/Roaming/App/Cache_Data",
    "AppData/Local/App/GPUCache",
    "AppData/Roaming/App/Service Worker",
    "AppData/Local/App/Shared Dictionary",
    "AppData/Local/npm-cache",
    "AppData/Local/NuGet/v3-cache",
    "AppData/Local/NuGet/plugins-cache",
    "proj/node_modules",
    "proj/.git",
    "$RECYCLE.BIN",
])
def test_default_list_excludes_each_named_directory_category(rel):
    assert excluded(Excludes(default_patterns()), rel, True), rel


@pytest.mark.parametrize("rel,is_dir", [
    ("proj/Cache", True), ("proj/Cache/keep.txt", False), ("Documents/report.pdf", False),
    ("src/main.py", False), ("notes/cache-notes.md", False),
])
def test_default_list_keeps_cache_dirs_outside_appdata_and_ordinary_files(rel, is_dir):
    assert not excluded(Excludes(default_patterns()), rel, is_dir), rel


def test_default_list_excludes_the_whole_appdata_tree_so_its_files_are_never_indexed():
    ex = Excludes(default_patterns())
    assert excluded(ex, "AppData", True)
    assert excluded(ex, "AppData/Local/App/settings.dat")
    assert excluded(ex, "AppData/Roaming/Notes/todo.txt")


def test_default_list_excludes_vhdx_anywhere():
    ex = Excludes(default_patterns())
    assert excluded(ex, "disk.vhdx") and excluded(ex, "vm/disk2.vhdx")


def test_defaults_cover_virtualenv_and_build_output_categories():
    ex = Excludes(default_patterns())
    assert excluded(ex, "proj/.venv", True) or excluded(ex, "proj/venv", True)
    assert excluded(ex, "proj/__pycache__", True) or excluded(ex, "proj/dist", True) or excluded(ex, "proj/build", True)


# ---- AC-FI-7: per-rule counters ------------------------------------------------------------------------------------

def test_counters_on_the_rules_object_count_each_hit_and_can_be_reset():
    ex = Excludes(["**/node_modules/", "*.vhdx"])
    assert ex.counts == {}
    excluded(ex, "a/node_modules", True)
    excluded(ex, "b/node_modules", True)
    excluded(ex, "d.vhdx")
    by_rule = {rule: n for rule, n in ex.counts.items()}
    assert sorted(by_rule.values()) == [1, 2], by_rule
    assert [n for r, n in by_rule.items() if "node_modules" in r] == [2]
    assert [n for r, n in by_rule.items() if "vhdx" in r] == [1]
    excluded(ex, "src/main.py")
    assert sorted(ex.counts.values()) == [1, 2], "a non-match counts nothing"
    ex.reset_counts()
    assert ex.counts == {}


def test_pathological_patterns_do_not_hang_or_crash():
    ex = Excludes(["*" * 60 + "x", "[unclosed", "a/**/**/**/b/", "\\"])
    import time
    t0 = time.perf_counter()
    excluded(ex, "a" * 300 + "/" + "b" * 300, True)
    assert time.perf_counter() - t0 < 1.0


# ---- installed software and app data are not indexed (2026-11-07) ---------------------------------------------------

def walk_from_drive_root(ex, path: str, is_dir: bool = True):
    drive, *parts = path.split("\\")
    full, rel = drive + "\\", ""
    for i, part in enumerate(parts):
        full = full + ("" if full.endswith("\\") else "\\") + part
        rel = f"{rel}/{part}" if rel else part
        hit = ex.matching_rule(full, rel, part, True if i < len(parts) - 1 else is_dir)
        if hit:
            return hit
    return None


@pytest.mark.parametrize("path", ["C:\\Program Files\\Git", "C:\\Program Files\\Git\\cmd\\git.exe",
                                  "C:\\Program Files (x86)\\X", "D:\\Program Files (x86)\\X\\a.dll",
                                  "C:\\ProgramData\\X", "C:\\ProgramData\\X\\data.db",
                                  "C:\\Users\\u\\AppData", "C:\\Users\\u\\AppData\\Roaming\\App\\notes.txt"])
def test_default_excludes_drop_installs_and_app_data(path):
    assert walk_from_drive_root(Excludes(default_patterns()), path), path


@pytest.mark.parametrize("path", ["C:\\Users\\u\\Documents", "C:\\Users\\u\\Documents\\report.docx",
                                  "C:\\Users\\u\\Desktop\\a.txt", "C:\\Data\\Program Files notes.txt",
                                  "C:\\Users\\u\\Documents\\ProgramData-backup\\a.txt", "D:\\Projects\\AppDataTool\\x.py"])
def test_default_excludes_keep_documents_and_lookalike_names(path):
    assert walk_from_drive_root(Excludes(default_patterns()), path, is_dir=False) is None, path


def test_the_four_new_rules_sit_right_after_windows_in_the_default_list():
    assert default_patterns()[:5] == ["*:\\Windows\\", "*:\\Program Files\\", "*:\\Program Files (x86)\\",
                                      "*:\\ProgramData\\", "AppData/"]
