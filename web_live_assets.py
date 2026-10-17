"""Private worker asset swaps. No public command endpoint or listener restart."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import time
from types import MappingProxyType

from web_asset_store import (ASSET_API, LOGIN_API, git_hash, load_assets,
                             read_version, version_id)
from web_service_common import diagnostic, read_json, worker_paths


@dataclass(frozen=True)
class AssetSnapshot:
    files: object
    etags: object
    version: str = ""

    @classmethod
    def create(cls, files, version=""):
        files = MappingProxyType(dict(files))
        etags = MappingProxyType({"/" + name: '"' + hashlib.sha256(files[name]).hexdigest() + '"'
                                  for name in ("schedule.js", "login.js", "style.css")})
        return cls(files, etags, version)


def asset_request_path(root, run_id):
    worker_paths(Path(root), run_id)
    return Path(root) / ("asset_request_" + run_id + ".json")


class LiveWebAssets:
    def __init__(self, root, run_id, logger):
        self.root, self.run_id, self.logger = Path(root), run_id, logger
        self.path = asset_request_path(root, run_id)
        files = load_assets(self.root, logger)
        version = version_id({name: git_hash(raw) for name, raw in files.items()})
        self.snapshot = AssetSnapshot.create(files, version)
        self.previous = None
        self.signature = None
        self.result = {}

    def status(self):
        return dict(web_asset_reload=1, web_asset_api=ASSET_API, web_login_api=LOGIN_API,
                    web_page_version=self.snapshot.version, **self.result)

    def poll(self):
        try:
            info = self.path.stat()
            signature = (info.st_mtime_ns, info.st_size, info.st_ino)
        except FileNotFoundError:
            return False
        except OSError:
            return False
        if signature == self.signature:
            return False
        self.signature = signature
        request = None
        try:
            request = read_json(self.path)
            if (request.get('schema') != 1 or request.get('run_id') != self.run_id
                    or re.fullmatch(r'[0-9a-f]{32}', str(request.get('id', ''))) is None
                    or re.fullmatch(r'[0-9a-f]{64}', str(request.get('version', ''))) is None
                    or request.get('action') not in {'apply', 'rollback'}
                    or type(request.get('until')) not in {int, float}
                    or not time.time() < request['until'] <= time.time() + 30):
                raise ValueError('invalid or expired asset request')
            version = request['version']
            if version == self.snapshot.version:
                candidate = self.snapshot
            elif request['action'] == 'rollback':
                if self.previous is None or self.previous.version != version:
                    raise ValueError('previous asset snapshot unavailable')
                candidate = self.previous
            else:
                candidate = AssetSnapshot.create(read_version(self.root, version), version)
            # A timeout/cancellation may replace the command while files load.
            # Never publish a superseded command after its compensating rollback.
            if read_json(self.path) != request:
                return False
            if not time.time() < request['until']:
                raise ValueError('asset request expired during validation')
            if candidate is not self.snapshot:
                self.previous, self.snapshot = self.snapshot, candidate
            self.result = dict(asset_job_id=request['id'], asset_job_state='applied')
            diagnostic(self.logger, 'web_page_assets_swapped', version=version[:12], action=request['action'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.result = dict(asset_job_id=request.get('id', '') if isinstance(request, dict) else '',
                               asset_job_state='error')
            diagnostic(self.logger, 'web_page_asset_swap_failed', error_type=type(exc).__name__)
        return True
