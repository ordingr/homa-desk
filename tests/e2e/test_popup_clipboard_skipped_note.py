"""QA: the popup details line carries the skip reasons after a Copy / Cut (plan 2026-11-09, 4.1.6)."""
import pytest

from tests.e2e.test_popup_multiselect import (  # noqa: F401  (fixtures)
    root, store_and_index, popup, no_claude_binary, search)


def run(root, popup, result):
    search(root, popup, "design")
    popup.on_clipboard = lambda paths, op: result
    assert popup.clipboard("copy") is True
    return popup.details, popup.details_kind


SK = lambda *rs: [{"path": f"p{i}", "reason": r} for i, r in enumerate(rs)]


def test_ok_with_two_reasons(root, popup):
    d, k = run(root, popup, {"ok": True, "message": "Copied 1 file — paste in File Explorer",
                             "skipped": SK("refused: protected folder", "not_found: the file is gone")})
    assert d == ("Copied 1 file — paste in File Explorer (refused: protected folder; not_found: the file is gone)")
    assert k == "ok"


def test_three_reasons_truncated_and_dupes_collapsed(root, popup):
    d, _ = run(root, popup, {"ok": True, "message": "M", "skipped": SK("a", "a", "b", "c")})
    assert d == "M (a; b; …)"
    d, _ = run(root, popup, {"ok": True, "message": "M", "skipped": SK("a", "a", "b")})
    assert d == "M (a; b)"


@pytest.mark.parametrize("sk", [None, [], "x", [{"path": "p"}], [5], {"reason": "r"}])
def test_no_skipped_leaves_message_unchanged(root, popup, sk):
    d, _ = run(root, popup, {"ok": True, "message": "Copied 2 files", "skipped": sk})
    assert d == "Copied 2 files"
    d, _ = run(root, popup, {"ok": True, "message": "Copied 2 files"})
    assert d == "Copied 2 files"


def test_refused_all_shows_message_and_reasons(root, popup):
    d, k = run(root, popup, {"ok": False, "message": "Nothing was copied.", "skipped": SK("refused: protected folder")})
    assert d == "Nothing was copied. (refused: protected folder)" and k == "err"


def test_cut_op_too(root, popup):
    search(root, popup, "design")
    popup.on_clipboard = lambda paths, op: {"ok": True, "message": "Cut 1", "skipped": SK("r")}
    popup.clipboard("cut")
    assert popup.details == "Cut 1 (r)"
