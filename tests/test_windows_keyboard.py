"""No hook/callback is required; polling logic can be tested without Windows."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from windows_keyboard import (
    RUSSIAN_LANG_ID,
    VK_ACTIONS,
    VK_CONTROL,
    VK_MENU,
    WindowsShortcutPoller,
    needs_windows_fallback,
)


@pytest.mark.parametrize("version,needed", [
    ("8.6.12", True), ("8.6.17", True), ("8.6.18", False),
    ("8.7.0", False), ("9.0.0", False), ("unknown", True),
])
def test_fallback_for_old_tk_only(version, needed):
    assert needs_windows_fallback(version) == needed


class FakeBackend:
    def __init__(self):
        self.down = set()
        self.russian = True
        self.active = True

    def is_down(self, code):
        return code in self.down

    def is_russian(self):
        return self.russian

    def is_app_active(self):
        return self.active


class FakeRoot:
    def __init__(self, focus):
        self.focus = focus
        self.scheduled = {}
        self.counter = 0

    def focus_get(self):
        return self.focus

    def after(self, delay, callback):
        self.counter += 1
        self.scheduled[self.counter] = (delay, callback)
        return self.counter

    def after_cancel(self, job):
        self.scheduled.pop(job, None)


@pytest.fixture()
def setup(monkeypatch):
    actions = []
    monkeypatch.setattr("windows_keyboard.perform_shortcut", lambda wrapper, focus, action: actions.append((wrapper, focus, action)))
    native = object()
    entry = SimpleNamespace(_entry=native)
    root = FakeRoot(native)
    backend = FakeBackend()
    callback_actions = []
    poller = WindowsShortcutPoller(root, [entry], backend=backend, on_shortcut=callback_actions.append)
    return poller, root, backend, entry, native, actions, callback_actions


@pytest.mark.parametrize("vk,action", sorted(VK_ACTIONS.items()))
def test_russian_shortcuts_only_fire_once_while_held(setup, vk, action):
    poller, root, backend, entry, native, actions, reported = setup
    backend.down.update({VK_CONTROL, vk})
    poller.poll_once()
    poller.poll_once()
    assert actions == [(entry, native, action)]
    assert reported == [action]
    backend.down.clear()
    poller.poll_once()
    backend.down.update({VK_CONTROL, vk})
    poller.poll_once()
    assert len(actions) == 2
    poller.close()
    assert root.scheduled == {}


@pytest.mark.parametrize("reason", ["english", "inactive", "altgr", "no_ctrl", "other_focus"])
def test_do_not_intercept_other_contexts(setup, reason):
    poller, root, backend, entry, native, actions, reported = setup
    backend.down = {VK_CONTROL, 0x43}
    if reason == "english": backend.russian = False
    if reason == "inactive": backend.active = False
    if reason == "altgr": backend.down.add(VK_MENU)
    if reason == "no_ctrl": backend.down.remove(VK_CONTROL)
    if reason == "other_focus": root.focus = object()
    poller.poll_once()
    assert actions == []
    assert reported == []


def test_no_native_callback_or_hook_in_win_code():
    source = Path(__file__).resolve().parents[1].joinpath("windows_keyboard.py").read_text()
    assert "SetWindowsHookExW(" not in source
    assert "WINFUNCTYPE(" not in source
    assert "CFUNCTYPE(" not in source
    assert "WH_GETMESSAGE =" not in source


def test_poller_schedules_only_tk_after(setup):
    poller, root, backend, *rest = setup
    assert len(root.scheduled) == 1
    poller.close()
    assert root.scheduled == {}
