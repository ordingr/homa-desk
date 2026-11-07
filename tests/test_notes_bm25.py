"""Ask notes: BM25 over .md files and the markdown renderer (plan 7.3 M3a-6; AC-AN-1 .. AC-AN-6, AC-SH-5, PRD 12).

Real NotesIndex / AskNotesMode on real directories (tests/fixtures/notes copied to tmp, or a generated
2,000-note vault); the renderer is driven in a real (withdrawn) Tk root.
"""
import gzip
import json
import os
import shutil
import sys
import time

import pytest

from conftest import NOTES_FIXTURE, percentile

notes_bm25 = pytest.importorskip("assistant.search.notes_bm25", reason="assistant/search/notes_bm25.py (M3a-6) not present")

NotesIndex, AskNotesMode = notes_bm25.NotesIndex, notes_bm25.AskNotesMode
UNSET = "Set your notes folder in Settings"
FOOTER = "search over your notes; nothing left this PC"


class FakeSettings:
    def __init__(self, folder=""):
        self.folder = folder

    def get(self, key, default=None):
        return {"dir": self.folder} if key == "notes" else default


@pytest.fixture
def vault(tmp_path):
    dest = tmp_path / "vault"
    shutil.copytree(NOTES_FIXTURE, dest)
    return dest


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv("ASSISTANT_NOTES_DIR", raising=False)


def make_index(folder, tmp_path, name="notes.json.gz"):
    idx = NotesIndex(str(folder), index_path=tmp_path / "idx" / name)
    idx.start()
    assert idx.wait_ready(30), idx.status_text
    return idx


def names(rows):
    return [os.path.basename(r.path) for r in rows]


# ---- AC-AN-1: unset setting ---------------------------------------------------------------------------

def test_unset_folder_reports_set_your_notes_folder_and_returns_nothing(no_env, tmp_path):
    mode = AskNotesMode(settings=FakeSettings(""), index_path=tmp_path / "n.gz")
    assert mode.message == UNSET
    assert mode.search("zebrafish") == []
    assert not (tmp_path / "n.gz").exists()


def test_env_override_beats_the_setting_and_a_setting_alone_works(vault, tmp_path, monkeypatch):
    monkeypatch.delenv("ASSISTANT_NOTES_DIR", raising=False)
    via_setting = AskNotesMode(settings=FakeSettings(str(vault)), index_path=tmp_path / "a.gz")
    assert via_setting.index().wait_ready(30)
    assert via_setting.message is None and via_setting.search("zebrafish")
    via_setting.close()
    monkeypatch.setenv("ASSISTANT_NOTES_DIR", str(vault))
    via_env = AskNotesMode(settings=FakeSettings(""), index_path=tmp_path / "b.gz")
    assert via_env.index().wait_ready(30)
    assert via_env.search("zebrafish")
    via_env.close()


def test_mode_attributes_and_footer_are_verbatim(no_env):
    mode = AskNotesMode(settings=FakeSettings(""))
    assert (mode.keyword, mode.label, mode.local) == ("?", "Ask notes", True)
    assert mode.footer == FOOTER


# ---- AC-AN-2: ranking and passages ------------------------------------------------------------------------

def test_passages_are_ranked_and_carry_file_name_title_passage_and_highlights(vault, tmp_path):
    idx = make_index(vault, tmp_path)
    rows = idx.search("zebrafish migration")
    assert rows and names(rows)[0] == "alpha.md"
    top = rows[0]
    assert "zebrafish" in top.passage.lower()
    assert top.title and top.name == "alpha.md"
    assert top.highlights, "query terms must come back as highlight spans"
    for start, end in top.highlights:
        assert 0 <= start < end <= len(top.passage)
    assert any(top.passage[s:e].lower().startswith(("zebrafish", "migrat")) for s, e in top.highlights)
    assert "ignored.txt" not in names(idx.search("zebrafish")), "only .md files are indexed"


def test_limit_and_per_file_cap_and_empty_or_stopword_queries(vault, tmp_path):
    idx = make_index(vault, tmp_path)
    assert idx.search("the", limit=5) == [], "stop-word-only query returns nothing"
    assert idx.search("") == []
    assert idx.search("qqqqnotaword") == []
    rows = idx.search("migration contract pasta", limit=2)
    assert len(rows) <= 2


def test_dot_directories_node_modules_and_non_md_are_skipped(vault, tmp_path):
    for d in (".obsidian", "node_modules"):
        (vault / d).mkdir()
        (vault / d / "secret.md").write_text("# hidden\nxylophone zzyzx\n", encoding="utf-8")
    idx = make_index(vault, tmp_path)
    assert idx.search("xylophone") == []
    assert idx.note_count == 3


# ---- AC-AN-3: build in background, persistence, refresh ---------------------------------------------------------------

def test_building_message_is_visible_while_a_big_vault_indexes(no_env, tmp_path):
    big = tmp_path / "big"
    big.mkdir()
    for n in range(2000):
        (big / f"note_{n:04d}.md").write_text(f"# Note {n}\n\nbody about topic{n % 50} and shared words lorem ipsum {n}\n" * 6,
                                              encoding="utf-8")
    mode = AskNotesMode(settings=FakeSettings(str(big)), index_path=tmp_path / "big.gz")
    started = time.perf_counter()
    first = mode.message
    assert time.perf_counter() - started < 1.0, "first call must not block on the build"
    assert first == "building index…", first
    assert mode.index().wait_ready(10), "2,000 notes must index in under 10 s (PRD 12)"
    build = time.perf_counter() - started
    assert mode.message is None
    samples = []
    for n in range(40):
        t0 = time.perf_counter()
        assert mode.search(f"topic{n} shared words")
        samples.append((time.perf_counter() - t0) * 1000)
    print(f"\n[notes] 2000-note build {build:.2f}s; query p50 {percentile(samples, 50):.1f} ms, "
          f"p95 {percentile(samples, 95):.1f} ms (target < 200 ms)")
    assert percentile(samples, 95) < 200
    mode.close()


def test_changed_note_appears_after_refresh_and_deleted_note_disappears(vault, tmp_path):
    idx = make_index(vault, tmp_path)
    assert idx.search("quokka") == []
    (vault / "recipes" / "new.md").write_text("# New\n\nThe quokka sanctuary opens on monday.\n", encoding="utf-8")
    idx.refresh()
    assert names(idx.search("quokka")) == ["new.md"]
    (vault / "recipes" / "new.md").write_text("# New\n\nNothing about marsupials now.\n", encoding="utf-8")
    os.utime(vault / "recipes" / "new.md", (time.time() + 5, time.time() + 5))
    idx.refresh()
    assert idx.search("quokka") == [] and names(idx.search("marsupials")) == ["new.md"]
    (vault / "recipes" / "new.md").unlink()
    idx.refresh()
    assert idx.search("marsupials") == []


def test_index_is_persisted_and_reloaded_for_the_same_dir_and_discarded_for_another(vault, tmp_path):
    first = make_index(vault, tmp_path)
    assert first.search("zebrafish")
    saved = tmp_path / "idx" / "notes.json.gz"
    assert saved.is_file() and not (tmp_path / "idx" / "notes.json.gz.tmp").exists()
    second = make_index(vault, tmp_path)
    assert names(second.search("zebrafish"))[0] == "alpha.md"
    other = tmp_path / "other"
    other.mkdir()
    (other / "solo.md").write_text("# Solo\n\nlonely platypus\n", encoding="utf-8")
    third = make_index(other, tmp_path)
    assert third.search("zebrafish") == [] and names(third.search("platypus")) == ["solo.md"]


def test_corrupt_index_file_triggers_a_clean_rebuild(vault, tmp_path):
    saved = tmp_path / "idx" / "notes.json.gz"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b"\x1f\x8b not really gzip at all")
    idx = make_index(vault, tmp_path)
    assert names(idx.search("zebrafish"))[0] == "alpha.md"
    with gzip.open(saved, "rt", encoding="utf-8") as fh:
        assert json.load(fh), "a valid index replaces the corrupt one"


# ---- AC-AN-6: cloud placeholders never opened -------------------------------------------------------------------------

_opened: list = []
_watching = [False]


def _audit(event, args):
    if _watching[0] and event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        _opened.append(os.fsdecode(args[0]))


sys.addaudithook(_audit)


def test_offline_note_is_skipped_counted_and_never_opened(vault, tmp_path):
    from tests.fixtures.tree import _set_offline
    cloud = vault / "cloud-only.md"
    cloud.write_text("# Cloud\n\ncumulonimbus passage that must never be indexed\n", encoding="utf-8")
    _set_offline(cloud)
    _opened.clear()
    _watching[0] = True
    try:
        idx = make_index(vault, tmp_path)
        idx.refresh()
    finally:
        _watching[0] = False
    assert not [p for p in _opened if p.lower().endswith("cloud-only.md")], "OFFLINE note was opened"
    assert idx.search("cumulonimbus") == []
    assert idx.skipped_placeholder == 1
    assert idx.note_count == 3
    mode = AskNotesMode(settings=FakeSettings(str(vault)), index_path=tmp_path / "m.gz")
    mode.index().wait_ready(30)
    assert mode.skipped_placeholder == 1
    mode.close()


# ---- markdown rendering ---------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tk_text():
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no Tk display available: {exc}")
    root.withdraw()
    widget = tk.Text(root)
    yield widget
    root.destroy()


def _tags_at(widget, needle):
    """Tags covering the first occurrence of ``needle`` in the widget."""
    idx = widget.search(needle, "1.0")
    assert idx, f"{needle!r} not rendered; text is {widget.get('1.0', 'end')!r}"
    return set(widget.tag_names(idx))


def test_markdown_renders_headings_bold_lists_code_tables_with_tags_and_hits(tk_text):
    mdrender = pytest.importorskip("assistant.overlay.mdrender_tk", reason="mdrender_tk.py (M3a-6) not present")
    md = (NOTES_FIXTURE / "projects" / "alpha.md").read_text(encoding="utf-8")
    mdrender.render(tk_text, md, terms=["zebrafish", "migration"])
    shown = tk_text.get("1.0", "end")
    for raw in ("**", "## ", "```", "|---"):
        assert raw not in shown, f"raw markdown {raw!r} leaked into the rendered text"
    assert "md_h2" in _tags_at(tk_text, "Goals")
    assert "md_bold" in _tags_at(tk_text, "quarterly roadmap")
    assert "md_codeblock" in _tags_at(tk_text, "def migrate")
    assert "md_code" in _tags_at(tk_text, "migrate()")
    assert "md_table" in _tags_at(tk_text, "Hosting") or "md_tablehead" in _tags_at(tk_text, "Hosting")
    assert "•" in shown and "onboarding" in shown
    assert "md_hit" in _tags_at(tk_text, "zebrafish")
    assert any("md_url" in t for t in _tags_at(tk_text, "the wiki")) or "md_link" in _tags_at(tk_text, "the wiki")


def test_render_clear_true_replaces_and_clear_false_appends(tk_text):
    mdrender = pytest.importorskip("assistant.overlay.mdrender_tk", reason="mdrender_tk.py (M3a-6) not present")
    mdrender.render(tk_text, "# one\n")
    mdrender.render(tk_text, "# two\n")
    assert "one" not in tk_text.get("1.0", "end") and "two" in tk_text.get("1.0", "end")
    mdrender.render(tk_text, "# three\n", clear=False)
    assert "two" in tk_text.get("1.0", "end") and "three" in tk_text.get("1.0", "end")


def test_render_survives_unbalanced_markup(tk_text):
    mdrender = pytest.importorskip("assistant.overlay.mdrender_tk", reason="mdrender_tk.py (M3a-6) not present")
    mdrender.render(tk_text, "**bold never closed\n```\nfence never closed\n| a | b\n[link](\n", terms=["bold"])
    assert "bold never closed" in tk_text.get("1.0", "end")
