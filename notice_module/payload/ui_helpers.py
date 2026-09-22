"""Module-owned Tk layout helpers; no imports from the application."""
import tkinter as tk
from tkinter import ttk


ACCENT = "#1e40af"
SURFACE = "#f8fafc"


def title_band(parent, title, subtitle=""):
    band = tk.Frame(parent, bg="#172554", padx=18, pady=12)
    band.pack(fill="x")
    tk.Label(band, text=title, bg="#172554", fg="white", font=("맑은 고딕", 15, "bold"),
             anchor="w").pack(fill="x")
    if subtitle:
        detail = tk.Label(band, text=subtitle, bg="#172554", fg="#dbeafe", anchor="w",
                          wraplength=640, justify="left")
        detail.pack(fill="x", pady=(5, 0))
        band.bind("<Configure>", lambda event: detail.configure(wraplength=max(240, event.width - 36)))
    tk.Frame(parent, bg=ACCENT, height=3).pack(fill="x")


def notice_styles(parent):
    """Scoped styles only; native Windows themes can ignore heading colors."""
    style = ttk.Style(parent)
    style.configure("Notice.Treeview", rowheight=30, background="white", fieldbackground="white",
                    foreground="#0f172a", borderwidth=1)
    style.map("Notice.Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
    # Reuse clam's colorable heading elements without changing the app theme.
    try:
        for name in ("cell", "border", "padding", "image", "text"):
            element = "Notice.Treeheading." + name
            if element not in style.element_names():
                style.element_create(element, "from", "clam", "Treeheading." + name)
        style.layout("Notice.Treeview.Heading", [
            ("Notice.Treeheading.cell", {"sticky": "nswe"}),
            ("Notice.Treeheading.border", {"sticky": "nswe", "children": [
                ("Notice.Treeheading.padding", {"sticky": "nswe", "children": [
                    ("Notice.Treeheading.image", {"side": "right", "sticky": ""}),
                    ("Notice.Treeheading.text", {"sticky": "we"})]})]})])
    except tk.TclError:
        pass  # Unusual Tk installation: keep readable fonts and native border.
    style.configure("Notice.Treeview.Heading", background="#dbeafe", foreground="#172554",
                    font=("맑은 고딕", 10, "bold"), padding=(6, 8), relief="raised")
    style.map("Notice.Treeview.Heading", background=[("active", "#bfdbfe")])


class NoticeTabs(tk.Frame):
    """Connected, high-contrast tabs independent of the host's ttk theme."""
    def __init__(self, parent):
        super().__init__(parent, bg="#eff6ff")
        self.bar = tk.Frame(self, bg="#eff6ff")
        self.bar.pack(fill="x")
        self.body = tk.Frame(self, bg=ACCENT, padx=3, pady=3)
        self.body.pack(fill="both", expand=True)
        self.pages = []
        self.buttons = []
        self.current = None

    def add_page(self, text, *, padding=10):
        index = len(self.pages)
        page = tk.Frame(self.body, bg=SURFACE, padx=padding, pady=padding)
        button = tk.Button(self.bar, text=text, command=lambda: self.select(index),
                           relief="flat", borderwidth=0, padx=10, pady=10, cursor="hand2",
                           highlightthickness=2, highlightcolor="#f59e0b", takefocus=True)
        button.grid(row=0, column=index, sticky="nsew", padx=(0, 3))
        self.bar.columnconfigure(index, weight=1)
        for sequence, delta in (("<Left>", -1), ("<Right>", 1)):
            button.bind(sequence, lambda event, d=delta: self._move(d))
        self.pages.append(page)
        self.buttons.append(button)
        self.select(0 if self.current is None else self.current)
        return page

    def tabs(self):
        return tuple(range(len(self.pages)))

    def select(self, index=None):
        if index is None:
            return self.current
        if self.current is not None:
            self.pages[self.current].pack_forget()
        self.current = index
        self.pages[index].pack(fill="both", expand=True)
        for i, button in enumerate(self.buttons):
            active = i == index
            button.configure(bg=ACCENT if active else "#cbd5e1", fg="white" if active else "#334155",
                             activebackground=ACCENT if active else "#b6c6de",
                             activeforeground="white" if active else "#172554",
                             highlightbackground=ACCENT if active else "#94a3b8",
                             font=("맑은 고딕", 10, "bold" if active else "normal"))

    def _move(self, delta):
        self.select((self.current + delta) % len(self.pages))
        self.buttons[self.current].focus_set()
        return "break"


def example_entry(parent, variable, example='', **options):
    """Visual hint only: example text never enters the submitted StringVar."""
    entry = ttk.Entry(parent, textvariable=variable, **options)
    if example:
        hint = tk.Label(entry, text='예: ' + example, bg='white', fg='#64748b', anchor='w', cursor='xterm')
        def refresh(*_args):
            if not variable.get() and entry.focus_get() != entry:
                hint.place(x=4, rely=0.5, anchor='w', relwidth=0.96)
            else:
                hint.place_forget()
        hint.bind('<Button-1>', lambda _event: entry.focus_set())
        entry.bind('<FocusIn>', lambda _event: hint.place_forget())
        entry.bind('<FocusOut>', refresh)
        trace = variable.trace_add('write', refresh)
        entry.bind('<Destroy>', lambda event: variable.trace_remove('write', trace) if event.widget == entry else None)
        refresh()
    return entry


def center_window(window, parent):
    window.update_idletasks()
    width = max(window.winfo_reqwidth(), window.winfo_width())
    height = max(window.winfo_reqheight(), window.winfo_height())
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    window.geometry(f"+{max(0, x)}+{max(0, y)}")
