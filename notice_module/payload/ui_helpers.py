"""Module-owned Tk layout helpers; no imports from the application."""


def center_window(window, parent):
    window.update_idletasks()
    width = max(window.winfo_reqwidth(), window.winfo_width())
    height = max(window.winfo_reqheight(), window.winfo_height())
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    window.geometry(f"+{max(0, x)}+{max(0, y)}")
