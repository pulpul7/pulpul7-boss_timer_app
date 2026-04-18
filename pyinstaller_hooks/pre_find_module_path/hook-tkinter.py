from pathlib import Path
import sysconfig


def pre_find_module_path(api):
    stdlib_path = Path(sysconfig.get_path("stdlib") or "")
    tkinter_package = stdlib_path / "tkinter"
    if tkinter_package.exists():
        api.search_dirs = [str(stdlib_path)]
