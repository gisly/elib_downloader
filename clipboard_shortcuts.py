"""Layout-independent clipboard shortcuts for CustomTkinter entry fields.

Tk 8.6 on Windows can fail to match ``<Control-c>`` when the active keyboard
layout is Cyrillic. Bind the *generic* ``<KeyPress>`` on the real tkinter.Entry
instead, inspect ControlMask and the Windows virtual-key code, and perform the
operation directly. This avoids depending on native ``<<Copy>>`` / ``<<Paste>>``
virtual event bindings that may also fail on affected Tk builds.
"""

import sys
import tkinter as tk

CONTROL_MASK = 0x0004
ALT_MASK = 0x0008  # Ctrl+Alt (often AltGr) must not trigger clipboard actions.

KEYSYM_ACTIONS = {
    "c": "copy", "с": "copy", "cyrillic_es": "copy",
    "v": "paste", "м": "paste", "cyrillic_em": "paste",
    "x": "cut", "ч": "cut", "cyrillic_che": "cut",
    "a": "select_all", "ф": "select_all", "cyrillic_ef": "select_all",
}

# Windows virtual-key codes are stable when switching between EN and RU.
WINDOWS_KEYCODES = {0x43: "copy", 0x56: "paste", 0x58: "cut", 0x41: "select_all"}

# Typical X11 evdev physical keycodes; useful if X11 also reports "??"
# for a Cyrillic Ctrl+key combination. Used only as a last resort.
X11_KEYCODES = {54: "copy", 55: "paste", 53: "cut", 38: "select_all"}


def _normalized_key(value):
    """Convert Tk's Unicode keysym convention (e.g. U0441) to a character."""
    key = str(value or "")
    if len(key) == 5 and key.startswith("U"):
        try:
            return chr(int(key[1:], 16)).casefold()
        except ValueError:
            pass
    return key.casefold()


def shortcut_action(event, *, platform=None):
    """Find the clipboard action for a Ctrl shortcut independent of layout."""
    state = getattr(event, "state", 0)
    if not (state & CONTROL_MASK) or state & ALT_MASK:
        return None

    current_platform = sys.platform if platform is None else platform
    if current_platform == "win32":
        # Use the physical virtual-key code first; keysym may be unusable in
        # Tk 8.6 with a Cyrillic layout, including an empty keysym.
        action = WINDOWS_KEYCODES.get(getattr(event, "keycode", None))
        if action:
            return action

    for value in (getattr(event, "keysym", ""), getattr(event, "char", "")):
        action = KEYSYM_ACTIONS.get(_normalized_key(value))
        if action:
            return action
    if current_platform.startswith("linux"):
        return X11_KEYCODES.get(getattr(event, "keycode", None))
    return None


def _selection_bounds(native_entry):
    try:
        return native_entry.index("sel.first"), native_entry.index("sel.last")
    except tk.TclError:
        return None


def _copy(entry, native_entry, selected):
    """Copy the visible text selection, never reveal a masked password."""
    if selected is None or entry.cget("show"):
        return
    start, end = selected
    value = entry.get()[start:end]
    if value:
        native_entry.clipboard_clear()
        native_entry.clipboard_append(value)


def perform_shortcut(entry, native_entry, action):
    """Perform an Entry action directly, respecting disabled/password fields.

    ``entry`` is the CustomTkinter wrapper, so .get/.delete/.insert respect
    its placeholder state. ``native_entry`` is its actual tkinter.Entry,
    which receives key events and owns the selection/cursor.
    """
    selected = _selection_bounds(native_entry)
    editable = entry.cget("state") == "normal"

    if action == "copy":
        _copy(entry, native_entry, selected)
    elif action == "cut":
        if editable and not entry.cget("show") and selected is not None:
            _copy(entry, native_entry, selected)
            entry.delete(*selected)
    elif action == "paste":
        if editable:
            try:
                value = native_entry.clipboard_get()
            except tk.TclError:
                return "break"
            if selected is not None:
                start, end = selected
                entry.delete(start, end)
            else:
                start = native_entry.index("insert")
            entry.insert(start, value)
            native_entry.icursor(start + len(value))
    elif action == "select_all":
        if entry.get():  # Do not select CustomTkinter's placeholder text.
            native_entry.select_range(0, tk.END)
            native_entry.icursor(tk.END)
    else:
        return None
    return "break"  # Prevent Tk class bindings from running twice.


def handle_clipboard_shortcut(event, *, entry=None):
    """Generic KeyPress handler for a CTkEntry or a plain tkinter.Entry."""
    action = shortcut_action(event)
    if action is None:
        return None
    native_entry = event.widget
    return perform_shortcut(entry if entry is not None else native_entry, native_entry, action)


def install_entry_shortcuts(entry):
    """Install shortcut handler on the *real* Tk Entry, not the CTk wrapper.

    Crucially, match <KeyPress> instead of <Control-KeyPress> or <Control-c>.
    Affected Windows/Tk builds can fail the latter when Russian is active.
    """
    native_entry = getattr(entry, "_entry", entry)

    def on_keypress(event):
        if event.widget is not native_entry:
            return None
        return handle_clipboard_shortcut(event, entry=entry)

    native_entry.bind("<KeyPress>", on_keypress, add="+")
    return on_keypress  # Can be used by tests or troubleshooting.


def install_entry_context_menu(entry):
    """Working mouse fallback if the Windows/Tk keyboard binding is broken.

    The native Entry keeps text selection when the user right-clicks it.
    """
    native = getattr(entry, "_entry", entry)
    menu = tk.Menu(native, tearoff=False)
    commands = (
        ("copy", "Копировать"),
        ("paste", "Вставить"),
        ("cut", "Вырезать"),
        ("select_all", "Выделить всё"),
    )
    for action, label in commands:
        menu.add_command(
            label=label,
            command=lambda a=action: perform_shortcut(entry, native, a),
        )

    def show_menu(event):
        selected = _selection_bounds(native) is not None
        editable = entry.cget("state") == "normal"
        protected = bool(entry.cget("show"))
        for action, _ in commands:
            enabled = {
                "copy": selected and not protected,
                "paste": editable,
                "cut": selected and editable and not protected,
                "select_all": bool(entry.get()),
            }[action]
            menu.entryconfigure({
                "copy": "Копировать", "paste": "Вставить",
                "cut": "Вырезать", "select_all": "Выделить всё",
            }[action], state="normal" if enabled else "disabled")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    native.bind("<Button-3>", show_menu, add="+")
    return menu
