"""Stable host boundary for trusted, independently shipped notice modules.

No sys.path changes, no import of module implementation by the frozen app, and
no .pyc files in verified version folders. This is distribution isolation, not a
security sandbox: only the configured publisher's packages may be installed.
"""
from dataclasses import dataclass
import importlib
import importlib.abc
import importlib.util
import json
from pathlib import Path
import sys
import uuid

from ai_module_updater import AiModuleUpdater, MODULE_API, UpdateError, validate_manifest


@dataclass(frozen=True)
class NoticeHost:
    root: object
    data_root: Path
    get_server: object
    get_parent: object
    message_box: object
    log: object
    call_later: object
    cancel_later: object
    api_version: int = MODULE_API


class _SourceLoader(importlib.abc.Loader):
    def __init__(self, path):
        self.path = path

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        # compile(bytes) supports encoding cookies; do not generate bytecode files.
        module.__file__ = str(self.path)
        exec(compile(self.path.read_bytes(), str(self.path), "exec"), module.__dict__)


class _ModuleFinder(importlib.abc.MetaPathFinder):
    def __init__(self, prefix, directory, allowed):
        self.prefix = prefix
        self.directory = directory.resolve()
        self.allowed = set(allowed)

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.prefix and not fullname.startswith(self.prefix + "."):
            return None
        parts = fullname.split(".")[1:]
        relative = "/".join(parts)
        options = [(f"{relative}/__init__.py" if relative else "__init__.py", True)]
        if relative:
            options.append((relative + ".py", False))
        for name, package in options:
            file = self.directory / name
            if name in self.allowed and file.is_file() and file.resolve().is_relative_to(self.directory):
                return importlib.util.spec_from_file_location(fullname, file, loader=_SourceLoader(file),
                    submodule_search_locations=[str(file.parent)] if package else None)
        # A module-local missing file must never fall through to path finders,
        # including a stray .pyc or an unlisted Python file.
        raise ModuleNotFoundError(f"알림 패키지에 등록되지 않은 모듈: {fullname}")


class RuntimeSession:
    def __init__(self, plugin, finder, version, host):
        self.plugin, self.finder, self.version, self.host = plugin, finder, version, host

    def close(self):
        try:
            self.plugin.stop()
        except (Exception, SystemExit) as exc:
            self.host.log(f"notice_module_stop_failed {exc}")
        finally:
            _unmount(self.finder)


def _unmount(finder):
    if finder in sys.meta_path:
        sys.meta_path.remove(finder)
    for name in list(sys.modules):
        if name == finder.prefix or name.startswith(finder.prefix + "."):
            del sys.modules[name]


def launch_module(directory: Path, manifest: dict, host: NoticeHost):
    directory = Path(directory)
    entry = Path(manifest["entrypoint"])
    parts = entry.with_suffix("").parts
    if not parts or parts[0] != "payload" or not all(part.isidentifier() for part in parts):
        raise UpdateError("모듈 진입 경로는 payload/ 아래 Python 모듈 이름이어야 합니다.")
    files = manifest.get("files")
    if not isinstance(files, dict) or "payload/__init__.py" not in files:
        raise UpdateError("알림 패키지에 payload/__init__.py 및 파일 목록이 필요합니다.")
    prefix = "_boss_notice_" + uuid.uuid4().hex
    allowed = [name.removeprefix("payload/") for name in files if name.startswith("payload/")]
    finder = _ModuleFinder(prefix, directory / "payload", allowed)
    sys.meta_path.insert(0, finder)
    plugin = None
    try:
        entry_name = ".".join((prefix, *parts[1:]))
        module = importlib.import_module(entry_name)
        if getattr(module, "MODULE_API", None) != host.api_version:
            raise UpdateError("실행 모듈의 API 버전이 본체와 다릅니다.")
        factory = getattr(module, "create_plugin", None)
        if not callable(factory):
            raise UpdateError("create_plugin(host) 진입 함수가 없습니다.")
        plugin = factory(host)
        if not all(callable(getattr(plugin, name, None)) for name in ("start", "stop", "health_check", "open_management")):
            raise UpdateError("알림 모듈에 필요한 생명주기 함수가 없습니다.")
        plugin.start()
        health = plugin.health_check()
        if not isinstance(health, dict) or health.get("ok") is not True or health.get("api_version") != host.api_version:
            raise UpdateError("알림 모듈 시작 검사를 통과하지 못했습니다.")
        return RuntimeSession(plugin, finder, manifest["version"], host)
    except (Exception, SystemExit):
        if plugin is not None and callable(getattr(plugin, "stop", None)):
            try:
                plugin.stop()
            except (Exception, SystemExit) as exc:
                host.log(f"notice_module_failed_start_cleanup {exc}")
        _unmount(finder)
        raise


class NoticeRuntime:
    def __init__(self, host, app_root, resource_root, app_version):
        self.host = host
        self.bundle = Path(resource_root) / "notice_module"
        self.updater = AiModuleUpdater(Path(app_root) / "update_ai", app_version)
        self.session = None

    def start(self):
        if self.session is not None:
            return
        manifest = json.loads((self.bundle / "metadata.json").read_text(encoding="utf-8"))
        # Shipped source belongs to the executable's resources. A release ZIP
        # carries its own complete, hash-verified module.json instead.
        manifest["python_version"] = f"{sys.version_info.major}.{sys.version_info.minor}"
        manifest["files"] = {p.relative_to(self.bundle).as_posix(): "bundled"
                             for p in (self.bundle / "payload").rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        validate_manifest(manifest, manifest["version"], self.updater.app_version)
        self.session = self.updater.start_runtime(self.bundle, manifest,
            lambda path, info: launch_module(path, info, self.host))
        self.host.log(f"notice_module_started version={self.session.version}")

    def open_management(self):
        self.start()
        self.session.plugin.open_management()

    def close(self):
        if self.session is not None:
            self.session.close()
            self.session = None
