"""Tk-only capture visibility helpers. Call on the UI thread, never workers."""
import tkinter as tk


def app_windows(root):
    pending, windows = [root], []
    while pending:
        widget = pending.pop()
        try:
            if not widget.winfo_exists():
                continue
            if widget is root or isinstance(widget, tk.Toplevel):
                windows.append(widget)
            pending.extend(widget.winfo_children())
        except tk.TclError:
            continue
    return windows


def lower_app_windows(app, hwnd):
    """Remove always-on-top before lowering, including nested settings popups."""
    for window in app_windows(app.root):
        try:
            window.attributes('-topmost', False)
            window.lower()
        except tk.TclError:
            pass
    app._force_window_foreground(hwnd)


def hide_app_windows(app, hwnd):
    saved = []
    for window in app_windows(app.root):
        try:
            saved.append((window, window.state(), window.attributes('-topmost')))
        except tk.TclError:
            pass
    # Snapshot all transient children before hiding their owner can unmap them.
    for window, _state, _topmost in saved:
        try:
            window.attributes('-topmost', False)
            window.withdraw()
        except tk.TclError:
            pass
    app.root.update_idletasks()
    app._force_window_foreground(hwnd)
    return saved


def restore_app_windows(saved):
    for window, state, topmost in saved:
        try:
            if window.winfo_exists():
                window.attributes('-topmost', topmost)
                window.state(state)
        except tk.TclError:
            pass


def minimize_app_windows(app, hwnd):
    """Minimize the taskbar owner and hide transient/topmost capture blockers."""
    saved = []
    for window in app_windows(app.root):
        try:
            saved.append((window, window.state(), window.attributes('-topmost')))
        except tk.TclError:
            pass
    try:
        # All states are captured before changing the owner unmaps its children.
        for window, state, _topmost in reversed(saved):
            try:
                window.attributes('-topmost', False)
                if window is app.root and state not in {'withdrawn', 'iconic'}:
                    window.iconify()
                else:
                    window.withdraw()
            except tk.TclError:
                window.withdraw()  # Transient/override-redirect windows cannot iconify.
        app.root.update_idletasks()
        app._force_window_foreground(hwnd)
    except Exception:
        restore_app_windows(saved)
        raise
    return saved
