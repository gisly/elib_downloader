"""Safe Windows 10 fallback for Ctrl+C/V/X/A with a Russian layout.

Tk versions before 8.6.18 can fail to dispatch keyboard shortcuts when a
non-Latin layout is active.  To avoid calling Python from a native Windows
hook (which can crash Tk's event loop), poll Win32 keyboard state *from* the
Tk event loop instead.  We never install a hook or Win32 callback.

The normal Tk/CustomTkinter class bindings remain responsible for English
layout shortcuts.  The fallback is enabled only for the Russian layout and
old Tk, to avoid applying clipboard operations twice.
"""

from __future__ import annotations

import sys

from clipboard_shortcuts import perform_shortcut

VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt, including AltGr
VK_ACTIONS = {
    0x43: "copy",        # C -> С
    0x56: "paste",       # V -> М
    0x58: "cut",         # X -> Ч
    0x41: "select_all",  # A -> Ф
}
RUSSIAN_LANG_ID = 0x0419


def needs_windows_fallback(tk_patchlevel: str) -> bool:
    """Tk 8.6.18 contains the upstream non-Latin shortcut fix."""
    try:
        main, minor, patch = (int(n) for n in tk_patchlevel.split(".")[:3])
    except (ValueError, TypeError):
        return True  # Unknown Tk build: enable fallback rather than crash.
    return (main, minor, patch) < (8, 6, 18)


class User32KeyState:
    """Read-only, synchronous WinAPI calls; never register a callback/hook."""

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("Windows keyboard polling requires Windows")
        import ctypes
        from ctypes import wintypes

        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
        self.user32.GetKeyboardLayout.restype = ctypes.c_void_p
        self.user32.GetActiveWindow.argtypes = ()
        self.user32.GetActiveWindow.restype = wintypes.HWND

    def is_down(self, vk: int) -> bool:
        return bool(self.user32.GetAsyncKeyState(vk) & 0x8000)

    def is_russian(self) -> bool:
        # 0 = current GUI thread. Low word of HKL is the language ID.
        return bool((self.user32.GetKeyboardLayout(0) or 0) & 0xFFFF == RUSSIAN_LANG_ID)

    def is_app_active(self) -> bool:
        # GetActiveWindow is scoped to the calling GUI thread.  No shortcuts
        # should be handled while the user is in another application.
        return bool(self.user32.GetActiveWindow())


class WindowsShortcutPoller:
    """Run entirely within Tk's main thread using `after`.

    Polling high bits of GetAsyncKeyState recognizes a shortcut even if Tk
    fails to produce a corresponding <KeyPress> event. The operation fires
    once per chord (not once per poll). There are no ctypes callbacks.
    """

    def __init__(self, root, entries, *, backend=None, interval_ms=15, on_shortcut=None):
        self.root = root
        self.entries = {getattr(entry, "_entry", entry): entry for entry in entries}
        self.backend = backend if backend is not None else User32KeyState()
        self.interval_ms = interval_ms
        self.on_shortcut = on_shortcut
        self._held = set()
        self._after_id = None
        self._closed = False
        self._tick()

    def poll_once(self):
        """One polling cycle, callable directly in deterministic tests."""
        if self._closed:
            return
        if not self.backend.is_app_active() or not self.backend.is_russian():
            self._held.clear()
            return
        if not self.backend.is_down(VK_CONTROL) or self.backend.is_down(VK_MENU):
            self._held.clear()
            return

        pressed = {vk for vk in VK_ACTIONS if self.backend.is_down(vk)}
        newly_pressed = pressed - self._held
        self._held = pressed
        if not newly_pressed:
            return

        focused = self.root.focus_get()
        wrapper = self.entries.get(focused)
        if wrapper is None:
            return
        for vk in VK_ACTIONS:
            if vk in newly_pressed:
                action = VK_ACTIONS[vk]
                perform_shortcut(wrapper, focused, action)
                if self.on_shortcut is not None:
                    self.on_shortcut(action)

    def _tick(self):
        if self._closed:
            return
        try:
            self.poll_once()
        finally:
            if not self._closed:
                self._after_id = self.root.after(self.interval_ms, self._tick)

    def close(self):
        self._closed = True
        self._held.clear()
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
            self._after_id = None
