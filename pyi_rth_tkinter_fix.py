import os
import sys


def _set_tk_env() -> None:
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    tcl_dir = os.path.join(base_dir, "tcl8.6")
    tk_dir = os.path.join(base_dir, "tk8.6")
    if os.path.isdir(tcl_dir):
        os.environ["TCL_LIBRARY"] = tcl_dir
    if os.path.isdir(tk_dir):
        os.environ["TK_LIBRARY"] = tk_dir


_set_tk_env()
