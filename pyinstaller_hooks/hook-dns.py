"""DNS wire records are imported by name at runtime, including error replies."""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("dns.rdtypes")
