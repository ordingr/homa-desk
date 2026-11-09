"""Light / dark / system themes (plan 7.5 M4-2; AC-PS-1).

Registry reads are patched (never the real Windows setting). `settings` validation uses a real Settings object
against a scratch HOMA_HOME; recolouring uses real tkinter widgets.
"""
import json
import tkinter as tk

import pytest

from homa import paths
from homa.overlay import gfx, panel, theme
from homa.settings import DEFAULTS, Settings


@pytest.fixture(autouse=True)
def scratch_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMA_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def clean_theme_state():
    saved = (theme._mode, theme._resolved, list(theme._listeners), theme._poll_handle)
    theme._mode, theme._resolved, theme._listeners, theme._poll_handle = "dark", "dark", [], None
    yield
    theme._mode, theme._resolved, theme._listeners, theme._poll_handle = saved[0], saved[1], saved[2], saved[3]


def registry(monkeypatch, **values):
    """Patch the Personalize reads: names not given read as 'absent'."""
    monkeypatch.setattr(theme, "_read_dword", lambda name: values.get(name))


class FakeHandle:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeScheduler:
    def __init__(self):
        self.jobs = []

    def every(self, seconds, fn):
        handle = FakeHandle()
        self.jobs.append((seconds, fn, handle))
        return handle

    def live(self):
        return [j for j in self.jobs if not j[2].cancelled]


class FakeSettings:
    def __init__(self, **data):
        self.data = data
        self.listeners = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def on_change(self, key, fn):
        self.listeners[key] = fn

    def change(self, key, value):
        self.data[key] = value
        self.listeners[key](key, value)


# ---- resolution ------------------------------------------------------------------------------------------------------

def test_system_prefers_apps_value_over_system_value(monkeypatch):
    registry(monkeypatch, AppsUseLightTheme=1, SystemUsesLightTheme=0)
    assert theme.system_theme() == "light"
    registry(monkeypatch, AppsUseLightTheme=0, SystemUsesLightTheme=1)
    assert theme.system_theme() == "dark"


def test_system_falls_back_to_system_value_then_dark(monkeypatch):
    registry(monkeypatch, SystemUsesLightTheme=1)
    assert theme.system_theme() == "light"
    registry(monkeypatch)
    assert theme.system_theme() == "dark"


def test_fixed_modes_ignore_the_registry(monkeypatch):
    registry(monkeypatch, AppsUseLightTheme=1)
    assert theme.resolve("dark") == "dark"
    registry(monkeypatch, AppsUseLightTheme=0)
    assert theme.resolve("light") == "light"
    assert theme.resolve("system") == "dark"


@pytest.mark.parametrize("junk", [None, 3, "", "Light", "blue", ["dark"], {}])
def test_unknown_modes_normalise_to_dark(junk, monkeypatch):
    registry(monkeypatch, AppsUseLightTheme=1)
    assert theme.normalise(junk) == "dark"
    assert theme.resolve(junk) == "dark"


def test_real_registry_read_returns_light_or_dark():
    assert theme.system_theme() in ("light", "dark")


def test_palettes_have_the_same_names_and_distinct_values():
    assert set(theme.LIGHT) == set(theme.DARK) == set(theme.NAMES)
    assert theme.LIGHT["PANEL_BG"] != theme.DARK["PANEL_BG"]
    assert all(v.startswith("#") and len(v) == 7 for p in theme.PALETTES.values() for v in p.values())


# ---- palette lookup after a switch ----------------------------------------------------------------------------------------

def test_gfx_and_panel_resolve_the_active_palette_on_every_access(monkeypatch):
    registry(monkeypatch)
    theme.set_mode("dark")
    assert gfx.PANEL_BG == theme.DARK["PANEL_BG"] == panel.PANEL_BG
    assert theme.set_mode("light") is True
    assert gfx.PANEL_BG == theme.LIGHT["PANEL_BG"] == panel.PANEL_BG
    assert gfx.BUTTON_BG_HOVER == theme.LIGHT["BUTTON_BG_HOVER"]
    assert theme.set_mode("light") is False  # no change, no listener storm


def test_set_mode_notifies_listeners_with_old_and_new(monkeypatch):
    registry(monkeypatch)
    seen = []
    theme.on_change(lambda old, new: seen.append((old, new)))
    theme.set_mode("light")
    theme.set_mode("light")
    theme.set_mode("dark")
    assert seen == [("dark", "light"), ("light", "dark")]


def test_a_failing_listener_does_not_block_the_others(monkeypatch):
    registry(monkeypatch)
    seen = []

    def boom(old, new):
        raise RuntimeError("x")

    theme.on_change(boom)
    theme.on_change(lambda old, new: seen.append(new))
    theme.set_mode("light")
    assert seen == ["light"]


def test_refresh_only_follows_the_registry_in_system_mode(monkeypatch):
    registry(monkeypatch, AppsUseLightTheme=1)
    theme.set_mode("dark")
    assert theme.refresh() is False and theme.current() == "dark"
    theme.set_mode("system")
    assert theme.current() == "light"
    registry(monkeypatch, AppsUseLightTheme=0)
    assert theme.refresh() is True and theme.current() == "dark"


# ---- poll job -----------------------------------------------------------------------------------------------------------

def test_poll_job_is_armed_only_while_the_mode_is_system(monkeypatch):
    registry(monkeypatch, AppsUseLightTheme=1)
    sched, st = FakeScheduler(), FakeSettings(theme="dark")
    theme.install(st, sched, root=object())
    assert sched.jobs == [] and theme.debug_state()["polling"] is False

    st.change("theme", "system")
    assert len(sched.live()) == 1 and sched.live()[0][0] == theme.POLL_SECONDS == 5.0
    assert theme.current() == "light" and theme.debug_state()["polling"] is True

    st.change("theme", "system")  # idempotent: still one job
    assert len(sched.jobs) == 1

    st.change("theme", "dark")
    assert sched.live() == [] and sched.jobs[0][2].cancelled and theme.debug_state()["polling"] is False
    assert theme.current() == "dark"

    st.change("theme", "system")  # re-arms with a fresh job
    assert len(sched.live()) == 1 and len(sched.jobs) == 2


def test_install_with_a_saved_system_mode_arms_immediately(monkeypatch):
    registry(monkeypatch, AppsUseLightTheme=0)
    sched = FakeScheduler()
    theme.install(FakeSettings(theme="system"), sched, root=object())
    assert len(sched.live()) == 1 and theme.mode() == "system" and theme.current() == "dark"


def test_poll_posts_a_refresh_only_when_windows_changed(monkeypatch):
    from homa import uibus
    posted = []
    monkeypatch.setattr(uibus, "post", lambda fn, *a: posted.append(fn))
    registry(monkeypatch, AppsUseLightTheme=0)
    theme.set_mode("system")
    theme._poll()
    assert posted == []
    registry(monkeypatch, AppsUseLightTheme=1)
    theme._poll()
    assert posted == [theme.refresh]
    posted.clear()
    theme.set_mode("dark")
    theme._poll()
    assert posted == []


# ---- recolour of live widgets -----------------------------------------------------------------------------------------------

def test_recolour_repaints_widgets_canvas_items_and_text_tags():
    root = tk.Tk()
    try:
        root.withdraw()
        d, l = theme.DARK, theme.LIGHT
        frame = tk.Frame(root, bg=d["PANEL_BG"])
        frame.pack()
        label = tk.Label(frame, bg=d["PANEL_BG"], fg=d["PANEL_FG"])
        label.pack()
        keep = tk.Label(frame, bg="#123456", fg="#654321")
        keep.pack()
        canvas = tk.Canvas(frame, bg=d["PANEL_BG"])
        canvas.pack()
        rect = canvas.create_rectangle(0, 0, 5, 5, fill=d["BUTTON_BG"], outline=d["INPUT_BORDER"])
        text = tk.Text(frame, bg=d["INPUT_BG"], fg=d["PANEL_FG"])
        text.pack()
        text.tag_configure("link", foreground=d["PANEL_LINK"])
        text.insert("end", "x", "link")

        changed = theme.recolour(root, "dark", "light")

        assert changed >= 8
        assert frame.cget("bg").lower() == l["PANEL_BG"].lower()
        assert label.cget("fg").lower() == l["PANEL_FG"].lower()
        assert (keep.cget("bg"), keep.cget("fg")) == ("#123456", "#654321")
        assert canvas.itemcget(rect, "fill").lower() == l["BUTTON_BG"].lower()
        assert canvas.itemcget(rect, "outline").lower() == l["INPUT_BORDER"].lower()
        assert text.cget("bg").lower() == l["INPUT_BG"].lower()
        assert text.tag_cget("link", "foreground").lower() == l["PANEL_LINK"].lower()

        theme.recolour(root, "light", "dark")
        assert frame.cget("bg").lower() == d["PANEL_BG"].lower()
        assert canvas.itemcget(rect, "fill").lower() == d["BUTTON_BG"].lower()
    finally:
        root.destroy()


# ---- settings validation and repair -----------------------------------------------------------------------------------------

def test_default_theme_is_dark():
    assert DEFAULTS["theme"] == "dark" == theme.DEFAULT_MODE


@pytest.mark.parametrize("saved,expected", [("light", "light"), ("system", "system"), ("dark", "dark"),
                                            ("Light", "dark"), ("blue", "dark"), ("", "dark"), (7, "dark"),
                                            (None, "dark"), (["light"], "dark"), ({}, "dark")])
def test_settings_load_repairs_an_invalid_theme_to_dark(saved, expected):
    paths.settings_file().write_text(json.dumps({"schema": 1, "theme": saved}), encoding="utf-8")
    s = Settings()
    s.load()
    assert s.get("theme") == expected


@pytest.mark.parametrize("bad", ["blue", "", None, 1, "SYSTEM"])
def test_settings_set_rejects_an_invalid_theme(bad):
    s = Settings()
    s.load()
    before = s.get("theme")
    with pytest.raises(ValueError):
        s.set("theme", bad)
    assert s.get("theme") == before


def test_settings_set_accepts_each_mode_and_persists():
    s = Settings()
    s.load()
    for mode in ("light", "system", "dark"):
        s.set("theme", mode)
        assert json.loads(paths.settings_file().read_text(encoding="utf-8"))["theme"] == mode
