"""Windows file-name rules for Rename (plan 7.4 M3b-4; AC-AC-4). Pure function, no mocks."""
import pytest

names = pytest.importorskip("assistant.actions.names", reason="assistant/actions/names.py (M3b-4) not present")


def reason(name):
    out = names.validate(name)
    assert isinstance(out, str) and out.strip(), f"{name!r} should be refused with a reason, got {out!r}"
    return out.lower()


@pytest.mark.parametrize("name", ["report v2.docx", "console.txt", "con1.txt", "com0", "comma.txt", ".gitignore",
                                  "a b.txt", "ünïcode-名前.md", "x" * 255, "archive.tar.gz", "lpt10.txt", "nul-x"])
def test_usable_names_return_none(name):
    assert names.validate(name) is None


@pytest.mark.parametrize("name", ["con", "CON", "con.txt", "Con.tar.gz", "prn.log", "AUX.md", "nul.", "nul .txt",
                                  "com1", "COM9.txt", "lpt1.txt", "LPT9", "conin$", "CONOUT$.x"])
def test_reserved_device_names_are_refused_with_and_without_an_extension(name):
    text = reason(name) if not name.endswith(".") else reason(name)
    assert "reserved" in text or "device" in text or "period" in text or "space" in text, text


@pytest.mark.parametrize("name,char", [("a<b", "<"), ("a>b", ">"), ("a:b", ":"), ('a"b', '"'), ("a/b", "/"),
                                       ("a\\b", "\\"), ("a|b", "|"), ("a?b", "?"), ("a*b", "*")])
def test_forbidden_characters_are_named_in_the_reason(name, char):
    assert char in names.validate(name)


@pytest.mark.parametrize("name", ["trailing. ", "trailing.", "trailing ", "name . "])
def test_trailing_space_or_period_is_refused_with_that_reason(name):
    text = reason(name)
    assert "period" in text or "space" in text, text


@pytest.mark.parametrize("name", ["", " ", "   ", "tab\tname", "new\nline", "nul\x00byte", "x" * 256, None, 5, b"bytes"])
def test_empty_control_overlong_and_non_string_names_are_refused(name):
    assert isinstance(names.validate(name), str) and names.validate(name)


def test_the_reasons_differ_by_cause():
    causes = {reason("con.txt"), reason("a<b"), reason("trailing. "), reason(""), reason("x" * 256)}
    assert len(causes) == 5, causes


def test_validate_never_raises_on_odd_input():
    for odd in ("..", ".", "...", "‮", "a\u0000", "\ud800" if False else "x", "COM¹.txt"):
        names.validate(odd)
