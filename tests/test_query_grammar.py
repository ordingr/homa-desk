"""Files-mode query grammar (plan 7.3 M3a-3; DEC 5, AC-FI-8 .. AC-FI-12; PRD 6.4)."""
import random
import time

import pytest

query = pytest.importorskip("homa.index.query", reason="homa/index/query.py (M3a-3) not present")

# "doc" is deliberately absent: with it, "design doc" would become "ext: doc" (coordinator note, M3a-3).
KNOWN = {"md", "py", "html", "txt", "yml", "pdf", "docx", "xlsx", "csv", "png", "json", "js", "ts"}
DOT = "·"


def parse(text):
    return query.parse(text, KNOWN)


def cf(items):
    return [x.casefold() for x in items]


# ---- the five reference examples (AC-FI-12) --------------------------------------------------------------------------

def test_design_md_is_a_term_plus_an_extension_filter():
    q = parse("design md")
    assert cf(q.terms) == ["design"] and cf(q.exts) == ["md"] and not q.path_terms and not q.path_parts
    assert q.interpretation == f'name or path contains "design" {DOT} ext: md'


def test_design_doc_makes_two_anywhere_terms_when_doc_is_not_a_known_extension():
    q = parse("design doc")
    assert cf(q.terms) == ["design", "doc"] and not q.path_terms and not q.exts
    assert q.interpretation == f'name or path contains "design" {DOT} name or path contains "doc"'


def test_config_dash_e_yml():
    q = parse("config -e yml")
    assert cf(q.terms) == ["config"] and cf(q.exts) == ["yml"]
    assert q.interpretation == f'name or path contains "config" {DOT} ext: yml'


def test_report_dash_p_documents():
    q = parse("report -p Documents")
    assert cf(q.terms) == ["report"] and q.path_parts == ["Documents"]
    assert q.interpretation == f'name or path contains "report" {DOT} path contains "Documents"'


def test_plain_design():
    q = parse("design")
    assert cf(q.terms) == ["design"] and not (q.exts or q.path_terms or q.path_parts or q.nots or q.phrases)
    assert q.interpretation == 'name or path contains "design"'


# ---- extension token rules ------------------------------------------------------------------------------------------

def test_a_lone_extension_word_is_a_name_term_not_a_filter():
    q = parse("md")
    assert cf(q.terms) == ["md"] and not q.exts


def test_extension_matching_is_case_insensitive_and_dot_tolerant():
    assert cf(parse("Design MD").exts) == ["md"]
    assert cf(parse("design -e .YML").exts) == ["yml"]


def test_quoting_an_extension_makes_it_a_substring_term():
    q = parse('design "md"')
    assert not q.exts and q.phrases == ["md"] and cf(q.terms) == ["design"]
    lone = parse('"md"')
    assert not lone.exts and lone.phrases == ["md"]


def test_ext_long_form_takes_a_comma_list_and_path_long_form_is_a_path_part():
    q = parse("ext:md,py")
    assert sorted(cf(q.exts)) == ["md", "py"]
    assert parse("path:docs").path_parts == ["docs"]
    assert parse('path:"my docs"').path_parts == ["my docs"]
    assert cf(parse("notes -e md,py").exts) == ["md", "py"]


# ---- literal dash, NOT, phrases, wildcards --------------------------------------------------------------------------------

def test_dash_x_is_a_literal_term_never_a_not():
    q = parse("-x foo")
    assert not q.nots
    assert cf(q.terms) == ["-x", "foo"] and not q.path_terms
    q2 = parse("-5 foo")
    assert not q2.nots and "-5" in q2.terms


def test_dash_e_and_dash_p_without_a_value_are_literal():
    for text in ("-e", "report -p", "-p"):
        q = parse(text)
        assert not q.exts and not q.path_parts, text
        assert any(t.startswith("-") for t in q.terms + q.path_terms), text


def test_bang_excludes_and_shows_in_the_interpretation():
    q = parse("plan !draft")
    assert q.nots == ["draft"] and cf(q.terms) == ["plan"]
    assert "draft" in q.interpretation
    assert "!" not in "".join(q.terms + q.path_terms)


def test_quoted_phrase_is_kept_whole_with_its_spaces():
    q = parse('"quoted phrase"')
    assert q.phrases == ["quoted phrase"] and not q.terms
    assert "quoted phrase" in q.interpretation


def test_wildcards_survive_parsing():
    assert parse("des*gn").terms == ["des*gn"]
    assert parse("desig?").terms == ["desig?"]


def test_mixed_query_parses_every_part():
    q = parse('report -p Documents !draft "final v2" -e pdf')
    assert cf(q.terms) == ["report"] and q.path_parts == ["Documents"] and q.nots == ["draft"]
    assert q.phrases == ["final v2"] and cf(q.exts) == ["pdf"]
    for piece in ("report", "Documents", "draft", "final v2", "pdf"):
        assert piece in q.interpretation


# ---- robustness (input parsing is a security review point) ------------------------------------------------------------------

def test_empty_and_blank_queries_mean_all_files_without_error():
    for text in ("", "   ", "\t\n", None):
        q = parse(text)
        assert not (q.terms or q.exts or q.phrases or q.nots)
        assert isinstance(q.interpretation, str) and q.interpretation


def test_unterminated_quotes_and_dangling_operators_do_not_raise():
    for text in ('"abc', 'foo "bar baz', '"', '""', "!", "!!", "ext:", "path:", 'ext:"', "-", "--", "- -", '!"', "ext:,,,"):
        q = parse(text)
        assert isinstance(q.interpretation, str), text
    assert parse('"abc').phrases == ["abc"]


def test_oversized_input_is_capped_and_says_so():
    long_q = parse("a" * 5000)
    assert sum(len(t) for t in long_q.terms + long_q.path_terms + long_q.phrases) <= 600
    assert long_q.notes, "truncation must be reported in Query.notes"
    many = parse(" ".join(f"w{i}" for i in range(100)))
    assert len(many.terms) + len(many.path_terms) <= 40 and many.notes


def test_pathological_wildcard_and_quote_input_parses_fast():
    started = time.perf_counter()
    for text in ("*" * 5000, "?*" * 3000, '"' * 4000, "!" * 4000, "ext:" * 1000, "-e " * 1500, "\\" * 4000):
        parse(text)
    assert time.perf_counter() - started < 1.0


def test_fuzzed_input_never_raises_and_always_returns_strings():
    rng = random.Random(1234)
    alphabet = list('abc DESIGN "!-*?\\/:,.é日\x00\t') + ["ext:", "path:", "-e ", "-p ", "md ", "!"]
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 40)))
        q = parse(text)
        assert isinstance(q.interpretation, str)
        for field in (q.terms, q.path_terms, q.exts, q.path_parts, q.nots, q.phrases):
            assert all(isinstance(x, str) for x in field), text


def test_unicode_terms_are_kept_verbatim():
    q = parse("résumé 日本語")
    assert q.terms == ["résumé", "日本語"] and not q.path_terms


def test_every_plain_term_and_phrase_is_described_as_name_or_path():
    q = parse('alpha beta "gamma delta" path:docs')
    assert q.terms == ["alpha", "beta"] and q.phrases == ["gamma delta"] and q.path_parts == ["docs"]
    assert q.interpretation == (f'name or path contains "alpha" {DOT} name or path contains "beta" {DOT} '
                                f'name or path contains "gamma delta" {DOT} path contains "docs"')


def test_wildcard_term_is_described_as_name_or_path_matching():
    q = parse("des*gn")
    assert q.interpretation.startswith("name or path ") and '"des*gn"' in q.interpretation
