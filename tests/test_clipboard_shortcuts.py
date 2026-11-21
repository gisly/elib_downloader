"""Tests for keyboard shortcuts in English/Russian layouts and real Tk inputs."""

import os
from types import SimpleNamespace
import tkinter as tk

import pytest

from clipboard_shortcuts import (
    handle_clipboard_shortcut,
    install_entry_shortcuts,
    install_entry_context_menu,
    shortcut_action,
)


class FakeEntry:
    def __init__(self, text="hello world", selection=(0, 5), *, state="normal", show=""):
        self.text = text
        self.selection = selection
        self.state = state
        self.show = show
        self.clipboard = "original clipboard"
        self.cleared = False
        self.insert_cursor = 0
        self.events = []
        self.bound_sequence = None
        self.bound_callback = None
        self._entry = self

    def cget(self, key):
        return getattr(self, key)

    def get(self):
        return self.text

    def index(self, key):
        if key == "sel.first":
            if self.selection is None:
                raise tk.TclError("no selection")
            return self.selection[0]
        if key == "sel.last":
            if self.selection is None:
                raise tk.TclError("no selection")
            return self.selection[1]
        if key == "insert":
            return self.insert_cursor
        raise AssertionError(key)

    def clipboard_clear(self):
        self.cleared = True
        self.clipboard = ""

    def clipboard_append(self, value):
        self.clipboard += value

    def clipboard_get(self):
        return self.clipboard

    def delete(self, start, end):
        self.text = self.text[:start] + self.text[end:]
        self.selection = None

    def insert(self, index, value):
        self.text = self.text[:index] + value + self.text[index:]

    def icursor(self, position):
        self.insert_cursor = position

    def select_range(self, start, end):
        self.selection = (0, len(self.text))

    def bind(self, sequence, callback, add=None):
        self.bound_sequence = sequence
        self.bound_callback = callback


def key_event(*, keysym="", char="", state=0x0004, keycode=0, widget=None):
    return SimpleNamespace(
        keysym=keysym, char=char, state=state, keycode=keycode,
        widget=widget or FakeEntry(),
    )


@pytest.mark.parametrize(
    ("latin", "russian", "symbolic", "unicode_keysym", "expected"),
    [
        ("c", "с", "Cyrillic_es", "U0441", "copy"),
        ("v", "м", "Cyrillic_em", "U043C", "paste"),
        ("x", "ч", "Cyrillic_che", "U0447", "cut"),
        ("a", "ф", "Cyrillic_ef", "U0444", "select_all"),
    ],
)
def test_shortcuts_with_cyrillic_keysyms(latin, russian, symbolic, unicode_keysym, expected):
    for keysym in (latin, latin.upper(), russian, russian.upper(), symbolic, unicode_keysym):
        assert shortcut_action(key_event(keysym=keysym), platform="linux") == expected


@pytest.mark.parametrize(
    ("keycode", "expected"),
    [(0x43, "copy"), (0x56, "paste"), (0x58, "cut"), (0x41, "select_all")],
)
def test_windows_virtual_keys_even_if_keysym_is_unusable(keycode, expected):
    event = key_event(keysym="??", char="\x00", keycode=keycode)
    assert shortcut_action(event, platform="win32") == expected
    assert shortcut_action(event, platform="linux") is None


@pytest.mark.parametrize(
    ("keycode", "expected"),
    [(54, "copy"), (55, "paste"), (53, "cut"), (38, "select_all")],
)
def test_x11_fallback_when_keysym_is_unusable(keycode, expected):
    event = key_event(keysym="??", char="", keycode=keycode)
    assert shortcut_action(event, platform="linux") == expected


@pytest.mark.parametrize("state", [0, 0x0001, 0x000C])
def test_ctrl_required_but_ctrl_alt_not_intercepted(state):
    assert shortcut_action(key_event(keysym="с", state=state)) is None


def test_copy_selected_text_directly_without_virtual_event():
    widget = FakeEntry(text="Привет, мир", selection=(0, 6))
    assert handle_clipboard_shortcut(key_event(keysym="с", widget=widget)) == "break"
    assert widget.clipboard == "Привет"
    assert widget.cleared
    assert widget.text == "Привет, мир"
    assert widget.events == []  # No event_generate("<<Copy>>")


def test_paste_replaces_selection():
    widget = FakeEntry(text="Hello world", selection=(6, 11))
    widget.clipboard = "мир"
    assert handle_clipboard_shortcut(key_event(keysym="м", widget=widget)) == "break"
    assert widget.text == "Hello мир"
    assert widget.insert_cursor == 9


def test_cut_and_select_all():
    widget = FakeEntry(text="sample text", selection=(0, 6))
    assert handle_clipboard_shortcut(key_event(keysym="ч", widget=widget)) == "break"
    assert widget.clipboard == "sample"
    assert widget.text == " text"
    assert handle_clipboard_shortcut(key_event(keysym="ф", widget=widget)) == "break"
    assert widget.selection == (0, len(widget.text))


def test_password_is_not_copied_or_cut():
    widget = FakeEntry(text="secret", selection=(0, 6), show="•")
    assert handle_clipboard_shortcut(key_event(keysym="с", widget=widget)) == "break"
    assert widget.clipboard == "original clipboard"
    assert handle_clipboard_shortcut(key_event(keysym="ч", widget=widget)) == "break"
    assert widget.text == "secret"


def test_disabled_entry_does_not_change_from_paste_or_cut():
    widget = FakeEntry(state="disabled")
    widget.clipboard = "NEW"
    assert handle_clipboard_shortcut(key_event(keysym="м", widget=widget)) == "break"
    assert handle_clipboard_shortcut(key_event(keysym="ч", widget=widget)) == "break"
    assert widget.text == "hello world"


def test_no_selection_no_copy():
    widget = FakeEntry(selection=None)
    assert handle_clipboard_shortcut(key_event(keysym="с", widget=widget)) == "break"
    assert not widget.cleared


def test_handler_is_bound_to_generic_keypress_on_inner_entry():
    wrapper = SimpleNamespace(_entry=FakeEntry())
    handler = install_entry_shortcuts(wrapper)
    assert wrapper._entry.bound_sequence == "<KeyPress>"
    assert wrapper._entry.bound_callback is handler
    assert handler(key_event(keysym="z", widget=wrapper._entry)) is None


def test_real_tk_entry_copy_paste_cut_when_display_available():
    """Integration check for the real Tk widget, including bound KeyPress.

    This exercises direct clipboard actions on Tk under Xvfb. A Windows/Russian-layout manual test
    is still needed because Linux Xvfb cannot emulate the Windows Tk defect.
    """
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No display available for Tk integration test")
    try:
        root.geometry("250x100")
        e = tk.Entry(root)
        e.pack()
        e.insert(0, "Привет, мир")
        e.focus_force()  # Xvfb has no window manager to grant focus_set().
        root.update()
        install_entry_shortcuts(e)
        e.selection_range(0, 6)
        e.event_generate("<KeyPress>", keysym="c", state=0x0004, when="now")
        root.update()
        assert e.clipboard_get() == "Привет"
        e.selection_range(0, tk.END)
        e.event_generate("<KeyPress>", keysym="v", state=0x0004, when="now")
        root.update()
        assert e.get() == "Привет"
        e.selection_range(0, tk.END)
        e.event_generate("<KeyPress>", keysym="x", state=0x0004, when="now")
        root.update()
        assert e.get() == ""
        assert e.clipboard_get() == "Привет"
    finally:
        root.destroy()


def test_right_click_menu_is_available_when_tk_shortcuts_do_not_fire():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No display available for Tk integration test")
    try:
        entry = tk.Entry(root)
        entry.pack()
        entry.insert(0, "Копируемый текст")
        root.update()
        menu = install_entry_context_menu(entry)
        labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)]
        assert labels == ["Копировать", "Вставить", "Вырезать", "Выделить всё"]

        # Direct command works even without delivering any KeyPress event.
        entry.select_range(0, tk.END)
        menu.invoke(0)
        assert entry.clipboard_get() == "Копируемый текст"
        menu.invoke(2)
        assert entry.get() == ""
        entry.clipboard_clear()
        entry.clipboard_append("Вставлено")
        menu.invoke(1)
        assert entry.get() == "Вставлено"
    finally:
        root.destroy()
